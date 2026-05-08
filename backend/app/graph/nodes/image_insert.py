"""Optional post-HTML image insertion helpers.

This stage is API-driven rather than part of the required graph path: rendered
placeholder HTML remains exportable, and the user explicitly approves mappings
or generation prompts before any slide HTML is mutated.
"""

from __future__ import annotations

import base64
import hashlib
import html
import math
import mimetypes
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from pydantic import BaseModel, Field

from ...artifacts import store
from ...llm import image_gen, zenmux
from ...llm.models import get_model

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_PLACEHOLDER_RE = re.compile(
    r"<(?P<tag>[a-zA-Z][\w:-]*)\b"
    r"(?=[^>]*\bdata-image-placeholder\s*=\s*(['\"]?)true\2)"
    r"(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)"
    r"</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"([:\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?P<uri>[^)\s]+)(?:\s+[^)]*)?\)")
_URL_RE = re.compile(r"https?://[^\s)\]\"<>]+")
_GBIF_OCCURRENCE_RE = re.compile(r"^/occurrence/(\d+)$")
_SUPPORTED_GENERATION_ASPECTS = {
    "1:1": 1.0,
    "3:4": 3 / 4,
    "4:3": 4 / 3,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}


class _MappingChoice(BaseModel):
    slot_id: str
    asset_id: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class _ImagePlanResponse(BaseModel):
    mappings: list[_MappingChoice] = Field(default_factory=list)


def _is_url(uri: str) -> bool:
    return uri.startswith(("http://", "https://"))


def _is_data_uri(uri: str) -> bool:
    return uri.startswith("data:image/")


def _looks_like_image_uri(uri: str) -> bool:
    if _is_data_uri(uri):
        return True
    path = urlparse(uri).path if _is_url(uri) else uri
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def _commons_redirect_uri(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.netloc.lower() != "commons.wikimedia.org":
        return None
    path = unquote(parsed.path)
    prefix = "/wiki/File:"
    if not path.startswith(prefix):
        return None
    filename = path.removeprefix(prefix)
    if not filename:
        return None
    return f"https://commons.wikimedia.org/wiki/Special:Redirect/file/{quote(filename)}"


@lru_cache(maxsize=512)
def _gbif_media_url(occurrence_id: str) -> str | None:
    try:
        import httpx

        response = httpx.get(
            f"https://api.gbif.org/v1/occurrence/{occurrence_id}",
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    for item in data.get("media") or []:
        if item.get("type") != "StillImage":
            continue
        identifier = str(item.get("identifier") or "").strip()
        if identifier:
            return identifier
    return None


def _gbif_occurrence_media_uri(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.netloc.lower() != "www.gbif.org":
        return None
    match = _GBIF_OCCURRENCE_RE.match(parsed.path)
    if not match:
        return None
    return _gbif_media_url(match.group(1))


def _resolved_web_image_uri(uri: str) -> str | None:
    if not _is_url(uri):
        return uri if _is_data_uri(uri) else None
    if commons := _commons_redirect_uri(uri):
        return commons
    if _looks_like_image_uri(uri):
        return uri
    if gbif := _gbif_occurrence_media_uri(uri):
        return gbif
    return None


def _resolve_local_image_ref(material_uri: str, ref: str) -> str | None:
    if ref.startswith(("http://", "https://", "data:image/")):
        return _resolved_web_image_uri(ref)
    if not _looks_like_image_uri(ref):
        return None
    if not material_uri or material_uri.startswith("text:"):
        return None
    base = Path(material_uri)
    candidates = [
        (base.parent / ref),
        (Path.cwd() / base.parent / ref),
    ]
    if not base.is_absolute():
        candidates.append((Path.cwd().parent / base.parent / ref))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file():
            return str(resolved)
    return None


def _image_refs_from_text(text: str) -> list[str]:
    refs: list[str] = []
    refs.extend(match.group("uri").strip("<>") for match in _MARKDOWN_IMAGE_RE.finditer(text or ""))
    refs.extend(match.group(0).strip("<>") for match in _URL_RE.finditer(text or ""))
    return refs


def _asset_id(seed: str, idx: int) -> str:
    digest = hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"asset-{idx}-{digest}"


def _filename_from_uri(uri: str) -> str | None:
    if uri.startswith("data:"):
        return None
    parsed = urlparse(uri)
    name = Path(parsed.path if parsed.scheme else uri).name
    return name or None


def build_image_assets(materials: list[dict[str, Any]], thread_id: str | None = None) -> list[dict[str, Any]]:
    """Derive stable image assets from normalized materials."""
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_asset(
        *,
        uri: str,
        idx: int,
        material: dict[str, Any],
        summary: str | None = None,
    ) -> None:
        if uri in seen:
            return
        seen.add(uri)
        name = _filename_from_uri(uri) or material.get("name")
        asset = {
            "asset_id": _asset_id(uri, idx),
            "uri": uri,
            "name": name,
            "source": "user",
            "media_type": mimetypes.guess_type(uri)[0] or "image/png",
            "note": material.get("note"),
            "summary": (summary if summary is not None else material.get("parsed") or "").strip(),
        }
        if thread_id:
            asset["thread_id"] = thread_id
        assets.append(asset)

    for idx, material in enumerate(materials):
        uri = str(material.get("uri") or "")
        if uri and (material.get("kind") == "image" or _looks_like_image_uri(uri)):
            if resolved := _resolved_web_image_uri(uri):
                add_asset(uri=resolved, idx=idx, material=material)
            elif not _is_url(uri) and Path(uri).exists():
                add_asset(uri=uri, idx=idx, material=material)

        parsed = str(material.get("parsed") or "")
        for ref_idx, ref in enumerate(dict.fromkeys(_image_refs_from_text(parsed)), start=1):
            resolved = _resolve_local_image_ref(uri, ref)
            if not resolved:
                continue
            add_asset(
                uri=resolved,
                idx=(idx * 1000) + ref_idx,
                material=material,
                summary=f"Referenced in {material.get('name') or 'source material'}: {ref}",
            )
    return assets


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw):
        attrs[match.group(1).lower()] = next(
            group for group in match.groups()[1:] if group is not None
        )
    return attrs


def _clean_text(raw: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", raw)).strip()


def _hint_from_placeholder(attrs: dict[str, str], body: str) -> str:
    for key in ("data-prompt-hint", "aria-label", "alt"):
        if value := attrs.get(key):
            return value.strip()
    text = _clean_text(body)
    match = re.search(r"Suggested:\s*(.+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text or "image"


def extract_placeholder_slots(html_slides: dict[int | str, str]) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for raw_idx, slide_html in sorted(html_slides.items(), key=lambda item: int(item[0])):
        slide_idx = int(raw_idx)
        ordinal = 0
        for match in _PLACEHOLDER_RE.finditer(slide_html or ""):
            attrs = _parse_attrs(match.group("attrs"))
            slots.append({
                "slot_id": f"slide-{slide_idx}-slot-{ordinal}",
                "slide_idx": slide_idx,
                "slot_index": ordinal,
                "tag": match.group("tag").lower(),
                "hint": _hint_from_placeholder(attrs, match.group("body")),
                "style": attrs.get("style", ""),
                "attrs": attrs,
                **_slot_geometry(attrs),
            })
            ordinal += 1
    return slots


def has_image_insertion_opportunity(values: dict[str, Any]) -> bool:
    html_slides = values.get("html_slides") or {}
    slots = extract_placeholder_slots(html_slides)
    assets = values.get("image_assets") or build_image_assets(
        values.get("materials") or [],
        values.get("thread_id"),
    )
    return bool(slots or assets)


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value.lower())
        if len(token) > 1
    }


def _fallback_mappings(
    slots: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not slots or not assets:
        return []
    used: set[str] = set()
    mappings: list[dict[str, Any]] = []
    for index, slot in enumerate(slots):
        hint_tokens = _tokenize(str(slot.get("hint") or ""))
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for asset in assets:
            if asset["asset_id"] in used:
                continue
            haystack = " ".join(
                str(asset.get(key) or "")
                for key in ("name", "summary", "note")
            )
            asset_tokens = _tokenize(haystack)
            overlap = len(hint_tokens & asset_tokens)
            score = overlap / max(1, len(hint_tokens))
            if score > best[0]:
                best = (score, asset)
        score, asset = best
        if asset is None:
            asset = assets[index] if index < len(assets) else None
            score = 0.5 if asset else 0.0
        if asset is None:
            continue
        used.add(asset["asset_id"])
        mappings.append({
            "slot_id": slot["slot_id"],
            "asset_id": asset["asset_id"],
            "confidence": round(max(score, 0.5), 2),
            "rationale": "Best available match from image order and metadata.",
        })
    return mappings


def _plan_prompt(slots: list[dict[str, Any]], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_assets = [
        {
            "asset_id": asset["asset_id"],
            "name": asset.get("name"),
            "source": asset.get("source"),
            "summary": str(asset.get("summary") or "")[:700],
            "note": asset.get("note"),
        }
        for asset in assets
    ]
    compact_slots = [
        {
            "slot_id": slot["slot_id"],
            "slide_idx": slot["slide_idx"],
            "hint": slot["hint"],
        }
        for slot in slots
    ]
    return [
        {
            "role": "system",
            "content": (
                "Match slide image placeholders to user-provided image assets. "
                "Return only confident mappings. Do not invent asset ids. "
                "Prefer not to reuse an asset unless the same visual clearly fits multiple slots."
            ),
        },
        {
            "role": "user",
            "content": (
                "Image placeholders:\n"
                f"{compact_slots}\n\n"
                "Image assets:\n"
                f"{compact_assets}\n"
            ),
        },
    ]


def _ai_prompt_for_slot(slot: dict[str, Any]) -> str:
    hint = str(slot.get("hint") or "presentation image").strip()
    slide_num = int(slot.get("slide_idx", 0)) + 1
    aspect = str(slot.get("generation_aspect_ratio") or "").strip()
    aspect_hint = f" Use a {aspect} image canvas." if aspect else ""
    return (
        f"Create a polished presentation image for slide {slide_num}: {hint}. "
        "Use a clean editorial composition, no visible text, no watermarks, "
        "and enough negative space to sit inside a slide image slot."
        f"{aspect_hint}"
    )


def create_image_insertion_plan(values: dict[str, Any]) -> dict[str, Any]:
    slots = extract_placeholder_slots(values.get("html_slides") or {})
    assets = values.get("image_assets") or build_image_assets(
        values.get("materials") or [],
        values.get("thread_id"),
    )
    mappings: list[dict[str, Any]] = []
    if slots and assets:
        try:
            response = zenmux.chat_structured(
                get_model("image_plan"),
                _plan_prompt(slots, assets),
                _ImagePlanResponse,
                temperature=0.1,
            )
            valid_assets = {asset["asset_id"] for asset in assets}
            valid_slots = {slot["slot_id"] for slot in slots}
            used_slots: set[str] = set()
            for mapping in response.mappings:
                if mapping.slot_id not in valid_slots or mapping.asset_id not in valid_assets:
                    continue
                if mapping.slot_id in used_slots:
                    continue
                used_slots.add(mapping.slot_id)
                mappings.append(mapping.model_dump())
        except Exception:
            mappings = _fallback_mappings(slots, assets)
    mapped_slots = {mapping["slot_id"] for mapping in mappings}
    unmatched = [
        {
            "slot_id": slot["slot_id"],
            "slide_idx": slot["slide_idx"],
            "hint": slot["hint"],
            "prompt": _ai_prompt_for_slot(slot),
            "reason": "No approved user image mapping yet.",
        }
        for slot in slots
        if slot["slot_id"] not in mapped_slots
    ]
    return {
        "status": "planned",
        "slots": slots,
        "assets": assets,
        "mappings": mappings,
        "applied_mappings": [],
        "unmatched_slots": unmatched,
    }


def _asset_src(asset: dict[str, Any]) -> str:
    uri = str(asset.get("uri") or "")
    if _is_url(uri) or _is_data_uri(uri):
        return uri
    path = Path(uri)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _style_map(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _dimension_px(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:px)?", raw, flags=re.IGNORECASE)
    if not match:
        return None
    px = float(match.group(1))
    return px if px > 0 else None


def _nearest_generation_aspect(aspect_ratio: float | None) -> str | None:
    if not aspect_ratio or aspect_ratio <= 0:
        return None
    target = math.log(aspect_ratio)
    return min(
        _SUPPORTED_GENERATION_ASPECTS,
        key=lambda name: abs(target - math.log(_SUPPORTED_GENERATION_ASPECTS[name])),
    )


def _slot_fit(style: dict[str, str], attrs: dict[str, str]) -> str:
    fit = str(attrs.get("data-image-fit") or style.get("object-fit") or "cover").strip().lower()
    return fit if fit in {"cover", "contain"} else "cover"


def _slot_position(style: dict[str, str], attrs: dict[str, str]) -> str:
    position = str(attrs.get("data-image-position") or style.get("object-position") or "").strip()
    return position or "50% 50%"


def _slot_geometry(attrs: dict[str, str]) -> dict[str, Any]:
    style = _style_map(attrs.get("style", ""))
    width = _dimension_px(style.get("width")) or _dimension_px(attrs.get("width"))
    height = _dimension_px(style.get("height")) or _dimension_px(attrs.get("height"))
    aspect_ratio = (width / height) if width and height else None
    return {
        "width_px": width,
        "height_px": height,
        "aspect_ratio": aspect_ratio,
        "fit": _slot_fit(style, attrs),
        "position": _slot_position(style, attrs),
        "generation_aspect_ratio": _nearest_generation_aspect(aspect_ratio),
    }


def _img_style(slot: dict[str, Any]) -> str:
    keep_prefixes = ("grid-",)
    keep_keys = {
        "width", "height", "min-width", "min-height", "max-width", "max-height",
        "position", "left", "top", "right", "bottom", "inset",
        "margin", "margin-left", "margin-top", "margin-right", "margin-bottom",
        "align-self", "justify-self", "place-self",
        "transform", "transform-origin", "border-radius", "box-shadow",
        "clip-path", "aspect-ratio", "z-index",
    }
    style = {
        key: value
        for key, value in _style_map(str(slot.get("style") or "")).items()
        if key in keep_keys or any(key.startswith(prefix) for prefix in keep_prefixes)
    }
    attrs = slot.get("attrs") or {}
    if attrs.get("width") and "width" not in style:
        style["width"] = attrs["width"] if str(attrs["width"]).endswith("px") else f"{attrs['width']}px"
    if attrs.get("height") and "height" not in style:
        style["height"] = attrs["height"] if str(attrs["height"]).endswith("px") else f"{attrs['height']}px"
    style["display"] = "block"
    style["object-fit"] = str(slot.get("fit") or "cover")
    style["object-position"] = str(slot.get("position") or "50% 50%")
    style["box-sizing"] = "border-box"
    return "; ".join(f"{key}: {value}" for key, value in style.items())


def _img_tag(slot: dict[str, Any], asset: dict[str, Any]) -> str:
    src = html.escape(_asset_src(asset), quote=True)
    hint = html.escape(str(slot.get("hint") or asset.get("name") or "image"), quote=True)
    style = html.escape(_img_style(slot), quote=True)
    asset_id = html.escape(str(asset.get("asset_id") or ""), quote=True)
    fit = html.escape(str(slot.get("fit") or "cover"), quote=True)
    position = html.escape(str(slot.get("position") or "50% 50%"), quote=True)
    return (
        f'<img src="{src}" alt="{hint}" style="{style}" '
        f'data-inserted-image="true" data-image-asset-id="{asset_id}" '
        f'data-image-fit="{fit}" data-image-position="{position}" loading="lazy" />'
    )


def apply_image_mappings(
    values: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    base_html = values.get("html_slides_base") or values.get("html_slides") or {}
    slots = extract_placeholder_slots(base_html)
    slot_by_id = {slot["slot_id"]: slot for slot in slots}
    assets = values.get("image_assets") or build_image_assets(
        values.get("materials") or [],
        values.get("thread_id"),
    )
    asset_by_id = {asset["asset_id"]: asset for asset in assets}
    mapping_by_slot = {
        str(mapping.get("slot_id")): str(mapping.get("asset_id"))
        for mapping in mappings
        if mapping.get("slot_id") in slot_by_id and mapping.get("asset_id") in asset_by_id
    }

    updated: dict[int, str] = {}
    for raw_idx, slide_html in base_html.items():
        slide_idx = int(raw_idx)
        pieces: list[str] = []
        cursor = 0
        ordinal = 0
        changed = False
        for match in _PLACEHOLDER_RE.finditer(slide_html):
            slot_id = f"slide-{slide_idx}-slot-{ordinal}"
            pieces.append(slide_html[cursor:match.start()])
            asset_id = mapping_by_slot.get(slot_id)
            if asset_id:
                pieces.append(_img_tag(slot_by_id[slot_id], asset_by_id[asset_id]))
                changed = True
            else:
                pieces.append(match.group(0))
            cursor = match.end()
            ordinal += 1
        pieces.append(slide_html[cursor:])
        updated[slide_idx] = "".join(pieces) if changed else slide_html

    plan = dict(values.get("image_insertion_plan") or {})
    plan["assets"] = assets
    if not plan.get("slots"):
        plan["slots"] = slots
    if "mappings" not in plan:
        plan["mappings"] = []
    plan["unmatched_slots"] = [
        {
            **slot,
            "prompt": _ai_prompt_for_slot(slot),
            "reason": "No approved user image mapping yet.",
        }
        for slot in slots
        if slot["slot_id"] not in mapping_by_slot
    ]
    plan["applied_mappings"] = [
        {"slot_id": slot_id, "asset_id": asset_id}
        for slot_id, asset_id in mapping_by_slot.items()
    ]
    plan["status"] = "applied" if mapping_by_slot else "planned"
    return {
        "html_slides_base": {int(k): v for k, v in base_html.items()},
        "html_slides": updated,
        "image_assets": assets,
        "image_insertion_plan": plan,
        "image_insertion_status": "applied" if mapping_by_slot else "planned",
    }


def generate_slot_image(
    values: dict[str, Any],
    *,
    slot_id: str,
    prompt: str,
) -> dict[str, Any]:
    slots = extract_placeholder_slots(values.get("html_slides_base") or values.get("html_slides") or {})
    slot = next((item for item in slots if item["slot_id"] == slot_id), None)
    if slot is None:
        raise ValueError(f"Unknown image slot: {slot_id}")

    thread_id = values.get("thread_id")
    if not thread_id:
        raise ValueError("Missing thread_id for image generation.")

    safe_slot = re.sub(r"[^A-Za-z0-9_.-]+", "-", slot_id).strip("-")
    filename = f"{safe_slot}-{uuid.uuid4().hex[:8]}.png"
    output_path = store.thread_dir(str(thread_id)) / "images" / filename
    aspect_ratio = slot.get("generation_aspect_ratio")
    result = image_gen.generate_image(prompt, output_path, aspect_ratio=aspect_ratio)
    asset = {
        "asset_id": f"generated-{safe_slot}-{uuid.uuid4().hex[:8]}",
        "uri": str(result["path"]),
        "name": filename,
        "source": "generated",
        "media_type": result.get("mime_type") or "image/png",
        "summary": prompt,
        "prompt": prompt,
        "model": result.get("model"),
        "thread_id": thread_id,
        "requested_aspect_ratio": aspect_ratio,
    }
    assets = [*(values.get("image_assets") or []), asset]
    plan = dict(values.get("image_insertion_plan") or {})
    existing = [
        mapping
        for mapping in (plan.get("applied_mappings") or [])
        if mapping.get("slot_id") != slot_id
    ]
    existing.append({"slot_id": slot_id, "asset_id": asset["asset_id"]})
    next_values = {**values, "image_assets": assets}
    update = apply_image_mappings(next_values, existing)
    update["generated_asset"] = asset
    return update
