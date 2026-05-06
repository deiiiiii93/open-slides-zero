"""Optional image insertion endpoints."""

from __future__ import annotations

import mimetypes
import socket
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..artifacts import store
from ..graph.nodes.image_insert import (
    apply_image_mappings,
    build_image_assets,
    create_image_insertion_plan,
    generate_slot_image,
)
from ..llm.runtime_config import (
    RuntimeLLMConfig,
    redact_secrets,
    runtime_config_from_request,
    use_runtime_config,
)
from .common import config_for, current_state, graph, mirror_to_disk
from .owners import Owner, current_owner, require_deck_owner

router = APIRouter()
_GENERATION_LOCKS: dict[str, threading.Lock] = {}
_GENERATION_LOCKS_GUARD = threading.Lock()
_MAX_PROXY_IMAGE_BYTES = 24 * 1024 * 1024
_PROXY_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PROXY_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}
_PROXY_PLACEHOLDER_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#f4f1ea"/>
<rect x="12" y="12" width="616" height="336" rx="14" fill="none" stroke="#b7b0a4" \
stroke-width="4" stroke-dasharray="16 12"/>
<text x="320" y="188" text-anchor="middle" font-family="Arial, sans-serif" \
font-size="28" fill="#6e665c">Image unavailable</text>
</svg>"""


class ImageMappingIn(BaseModel):
    slot_id: str
    asset_id: str


class ApplyImagesBody(BaseModel):
    mappings: list[ImageMappingIn] = Field(default_factory=list)


class GenerateImageBody(BaseModel):
    slide_idx: int
    slot_id: str
    prompt: str


class GenerateImagesBody(BaseModel):
    items: list[GenerateImageBody] = Field(default_factory=list)


def _snapshot_values(thread_id: str) -> dict[str, Any]:
    snap = graph().get_state(config_for(thread_id))  # type: ignore[arg-type]
    if not snap or not snap.values:
        raise HTTPException(status_code=404, detail="Unknown deck")
    return dict(snap.values)


def _persist(thread_id: str, update: dict[str, Any]) -> dict[str, Any]:
    graph().update_state(config_for(thread_id), update)  # type: ignore[arg-type]
    mirror_to_disk(thread_id)
    return current_state(thread_id)


def _generation_lock(thread_id: str) -> threading.Lock:
    with _GENERATION_LOCKS_GUARD:
        lock = _GENERATION_LOCKS.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _GENERATION_LOCKS[thread_id] = lock
        return lock


@contextmanager
def _serial_image_generation(thread_id: str) -> Iterator[None]:
    lock = _generation_lock(thread_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        host = info[4][0]
        try:
            addr = ip_address(host)
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            return False
    return True


def _placeholder_image_response() -> Response:
    return Response(
        content=_PROXY_PLACEHOLDER_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


def _ensure_public_proxy_url(url: str) -> None:
    if not _is_public_http_url(url):
        raise HTTPException(
            status_code=400,
            detail="Only public http(s) image URLs can be proxied.",
        )


def _sniff_image_media_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.lstrip()[:5].lower() == b"<svg " or content.lstrip()[:4].lower() == b"<svg":
        return "image/svg+xml"
    return None


def _image_media_type(content_type: str | None, content: bytes) -> str | None:
    media_type = (content_type or "").split(";", 1)[0].lower()
    if media_type.startswith("image/"):
        return media_type
    return _sniff_image_media_type(content)


def _proxy_response(content: bytes, content_type: str | None) -> Response:
    media_type = _image_media_type(content_type, content)
    if not media_type:
        return _placeholder_image_response()
    if len(content) > _MAX_PROXY_IMAGE_BYTES:
        return _placeholder_image_response()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _fetch_proxy_image_httpx(url: str) -> tuple[bytes, str | None]:
    current_url = url
    with httpx.Client(
        timeout=20.0,
        follow_redirects=False,
        headers=_PROXY_REQUEST_HEADERS,
    ) as client:
        for _ in range(6):
            _ensure_public_proxy_url(current_url)
            resp = client.get(current_url)
            if resp.status_code in _PROXY_REDIRECT_STATUSES:
                location = resp.headers.get("location")
                if not location:
                    break
                current_url = urljoin(current_url, location)
                continue
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type")
    raise httpx.TooManyRedirects("Too many image proxy redirects")


class _NoProxyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _fetch_proxy_image_urllib(url: str) -> tuple[bytes, str | None]:
    current_url = url
    opener = urllib.request.build_opener(_NoProxyRedirectHandler)
    for _ in range(6):
        _ensure_public_proxy_url(current_url)
        req = urllib.request.Request(current_url, headers=_PROXY_REQUEST_HEADERS, method="GET")
        try:
            with opener.open(req, timeout=20.0) as resp:
                content = resp.read(_MAX_PROXY_IMAGE_BYTES + 1)
                return content, resp.headers.get("content-type")
        except urllib.error.HTTPError as exc:
            if exc.code in _PROXY_REDIRECT_STATUSES:
                location = exc.headers.get("location")
                if not location:
                    break
                current_url = urljoin(exc.url or current_url, location)
                continue
            raise
    raise urllib.error.HTTPError(url, 508, "Too many image proxy redirects", {}, None)


@router.get("/images/proxy")
def proxy_image(url: str = Query(..., min_length=1, max_length=4096)) -> Response:
    try:
        content, content_type = _fetch_proxy_image_httpx(url)
    except HTTPException:
        raise
    except Exception:
        try:
            content, content_type = _fetch_proxy_image_urllib(url)
        except HTTPException:
            raise
        except Exception:
            return _placeholder_image_response()
    try:
        return _proxy_response(content, content_type)
    except HTTPException:
        raise
    except Exception:
        return _placeholder_image_response()


@router.post("/decks/{thread_id}/images/plan")
def plan_images(
    thread_id: str,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    values = _snapshot_values(thread_id)
    with use_runtime_config(runtime_config):
        plan = create_image_insertion_plan(values)
    update = {
        "image_assets": plan.get("assets") or build_image_assets(
            values.get("materials") or [],
            thread_id,
        ),
        "image_insertion_plan": plan,
        "image_insertion_status": "planned",
    }
    state = _persist(thread_id, update)
    return {"ok": True, "plan": plan, "state": state}


@router.post("/decks/{thread_id}/images/apply")
def apply_images(
    thread_id: str,
    body: ApplyImagesBody,
    owner: Owner = Depends(current_owner),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    with _serial_image_generation(thread_id):
        values = _snapshot_values(thread_id)
        update = apply_image_mappings(
            values,
            [mapping.model_dump() for mapping in body.mappings],
        )
        state = _persist(thread_id, update)
    return {
        "ok": True,
        "applied_mappings": update["image_insertion_plan"].get("applied_mappings", []),
        "state": state,
    }


@router.post("/decks/{thread_id}/images/generate")
def generate_image(
    thread_id: str,
    body: GenerateImageBody,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is required.")
    with _serial_image_generation(thread_id):
        values = _snapshot_values(thread_id)
        try:
            with use_runtime_config(runtime_config):
                update = generate_slot_image(
                    values,
                    slot_id=body.slot_id,
                    prompt=body.prompt.strip(),
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = redact_secrets(exc)
            graph().update_state(  # type: ignore[arg-type]
                config_for(thread_id),
                {
                    "image_generation_errors": [{
                        "slot_id": body.slot_id,
                        "slide_idx": body.slide_idx,
                        "message": message,
                    }]
                },
            )
            raise HTTPException(status_code=502, detail=message) from exc
        generated_asset = update.pop("generated_asset")
        state = _persist(thread_id, update)
    return {"ok": True, "asset": generated_asset, "state": state}


@router.post("/decks/{thread_id}/images/generate_batch")
def generate_images(
    thread_id: str,
    body: GenerateImagesBody,
    owner: Owner = Depends(current_owner),
    runtime_config: RuntimeLLMConfig = Depends(runtime_config_from_request),
) -> dict[str, Any]:
    require_deck_owner(thread_id, owner)
    if not body.items:
        raise HTTPException(status_code=400, detail="At least one image prompt is required.")

    with _serial_image_generation(thread_id):
        values = _snapshot_values(thread_id)
        generated_assets: list[dict[str, Any]] = []
        update: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for item in body.items:
            prompt = item.prompt.strip()
            if not prompt:
                errors.append({
                    "slot_id": item.slot_id,
                    "slide_idx": item.slide_idx,
                    "message": "Prompt is required.",
                })
                continue
            try:
                with use_runtime_config(runtime_config):
                    update = generate_slot_image(values, slot_id=item.slot_id, prompt=prompt)
            except ValueError as exc:
                errors.append({
                    "slot_id": item.slot_id,
                    "slide_idx": item.slide_idx,
                    "message": str(exc),
                })
                continue
            except Exception as exc:
                errors.append({
                    "slot_id": item.slot_id,
                    "slide_idx": item.slide_idx,
                    "message": redact_secrets(exc),
                })
                continue
            generated_assets.append(update.pop("generated_asset"))
            values.update(update)

        if not generated_assets:
            graph().update_state(  # type: ignore[arg-type]
                config_for(thread_id),
                {"image_generation_errors": errors},
            )
            detail = errors[0]["message"] if errors else "No images were generated."
            raise HTTPException(status_code=502, detail=detail)

        if errors:
            update["image_generation_errors"] = errors
        state = _persist(thread_id, update)
    return {
        "ok": True,
        "assets": generated_assets,
        "errors": errors,
        "state": state,
    }


@router.get("/decks/{thread_id}/images/assets/{asset_id}/content")
def image_asset_content(
    thread_id: str,
    asset_id: str,
    owner: Owner = Depends(current_owner),
):
    require_deck_owner(thread_id, owner)
    values = _snapshot_values(thread_id)
    assets = values.get("image_assets") or build_image_assets(
        values.get("materials") or [],
        thread_id,
    )
    asset = next((item for item in assets if item.get("asset_id") == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="Unknown image asset")

    uri = str(asset.get("uri") or "")
    if uri.startswith(("http://", "https://")):
        return proxy_image(uri)
    if uri.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Data URI assets do not have file content.")

    path = Path(uri).resolve()
    root = store.thread_dir(thread_id).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=403, detail="Asset is outside this deck's artifact directory.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image file missing")

    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/png",
        filename=path.name,
    )
