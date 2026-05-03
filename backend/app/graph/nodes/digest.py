"""Stage B' — condense materials before structure / outline see them.

Two modes:
  - default:  deterministic passthrough or proportional truncation; only invokes
              an LLM condense pass when total bytes exceed a hard ceiling.
  - advanced: chunk + embed + persist a per-thread index, then build the digest
              by diverse retrieval (greedy MMR over embeddings). The persisted
              index is the foundation for future material Q&A features.

`materials_digest` is the single source of truth for downstream propose_structure
and outline nodes — they no longer slice raw materials.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

from ...artifacts.store import thread_dir
from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream

log = logging.getLogger(__name__)

# Char-based budgets — mirrors the rest of the codebase (no tiktoken dep).
TARGET_BUDGET = 80_000
HARD_CEILING = 200_000
MIN_FLOOR = 1_500          # smallest slice any material is allowed in proportional mode
CHUNK_SIZE = 1_500
CHUNK_OVERLAP = 200


# --------------------------------------------------------------------------- #
# Public node
# --------------------------------------------------------------------------- #


def digest_materials_node(state: dict[str, Any]) -> dict[str, Any]:
    materials = state.get("materials", []) or []
    mode = state.get("agent_mode") or "default"

    push_event({"node": "digest_materials", "state": "started", "mode": mode})
    if not materials:
        push_event({"node": "digest_materials", "state": "finished", "mode": mode, "empty": True})
        return {"materials_digest": [], "materials_index": None}

    if mode == "advanced":
        try:
            digest, index = _advanced_mode(materials, state.get("thread_id"))
        except Exception as exc:  # noqa: BLE001
            log.exception("Advanced digest failed; falling back to default mode")
            digest = _default_mode(materials)
            for entry in digest:
                entry["note"] = _join_note(entry.get("note"), f"advanced_failed: {exc}")
            index = None
    else:
        digest = _default_mode(materials)
        index = None

    push_event(
        {
            "node": "digest_materials",
            "state": "finished",
            "mode": mode,
            "total_chars": sum(d["retained_chars"] for d in digest),
            "materials": len(digest),
        }
    )
    return {"materials_digest": digest, "materials_index": index}


# --------------------------------------------------------------------------- #
# Default mode
# --------------------------------------------------------------------------- #


def _default_mode(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sized = [(m, len(m.get("parsed") or "")) for m in materials]
    total = sum(n for _, n in sized)

    if total <= TARGET_BUDGET:
        return [_passthrough(m) for m, _ in sized]

    if total <= HARD_CEILING:
        return _proportional(sized, TARGET_BUDGET)

    return _condense_oversized(sized)


def _passthrough(m: dict[str, Any]) -> dict[str, Any]:
    text = m.get("parsed") or ""
    return _entry(m, text, was_condensed=False, note=None)


_TRIM_SKIP_THRESHOLD = 500  # don't bother trimming if savings would be smaller than this


def _proportional(
    sized: list[tuple[dict[str, Any], int]],
    budget: int,
) -> list[dict[str, Any]]:
    """Allocate the budget proportionally; head+tail trim per material."""
    total = sum(n for _, n in sized)
    out: list[dict[str, Any]] = []
    for m, n in sized:
        if n == 0:
            out.append(_entry(m, "", was_condensed=False, note=None))
            continue
        share = max(MIN_FLOOR, int(budget * n / total))
        if n - share <= _TRIM_SKIP_THRESHOLD:
            out.append(_entry(m, m["parsed"], was_condensed=False, note=None))
            continue
        out.append(_trim_entry(m, share, note="head_tail_trim"))
    return out


def _trim_entry(m: dict[str, Any], share: int, *, note: str) -> dict[str, Any]:
    text = m["parsed"]
    return _entry(m, _trim_text(text, share), was_condensed=False, note=note)


def _trim_text(text: str, share: int) -> str:
    """Head+tail trim that respects the requested char budget exactly."""
    n = len(text)
    if n <= share:
        return text
    elided = n - share  # placeholder for the marker math below
    marker = f"\n\n[…{elided} chars elided…]\n\n"
    body = max(0, share - len(marker))
    head_n = body // 2
    tail_n = body - head_n
    elided = n - head_n - tail_n
    marker = f"\n\n[…{elided} chars elided…]\n\n"
    # Recompute body once more to absorb any drift in marker length.
    body = max(0, share - len(marker))
    head_n = body // 2
    tail_n = body - head_n
    return text[:head_n] + marker + (text[-tail_n:] if tail_n else "")


def _condense_oversized(
    sized: list[tuple[dict[str, Any], int]],
) -> list[dict[str, Any]]:
    """LLM-condense the largest materials until total <= TARGET_BUDGET.

    Small materials (<= MIN_FLOOR * 2 chars) pass through verbatim. We condense
    one material at a time, largest first, replacing its retained_text with the
    condensed summary. If the LLM call fails, fall back to head+tail trim for
    that material and continue.
    """
    # Start from passthrough; mutate retained_text for materials we condense.
    entries = [_passthrough(m) for m, _ in sized]
    sizes = [(idx, n) for idx, (_, n) in enumerate(sized)]
    sizes.sort(key=lambda x: x[1], reverse=True)

    model = get_model("digest")

    for idx, original_n in sizes:
        if _total_retained(entries) <= TARGET_BUDGET:
            break
        if original_n <= MIN_FLOOR * 2:
            continue
        # Per-material target: keep proportional share of the budget, with floor.
        share = max(MIN_FLOOR, int(TARGET_BUDGET * original_n / max(1, sum(n for _, n in sized))))
        try:
            with tagged_stream("digest_materials"):
                condensed = _llm_condense(model, entries[idx]["retained_text"], share)
            entries[idx]["retained_text"] = condensed
            entries[idx]["retained_chars"] = len(condensed)
            entries[idx]["was_condensed"] = True
            entries[idx]["note"] = _join_note(entries[idx].get("note"), "llm_condensed")
        except Exception as exc:  # noqa: BLE001
            log.exception("LLM condense failed for material %s", entries[idx].get("name"))
            fallback = _trim_text(entries[idx]["retained_text"], share)
            entries[idx]["retained_text"] = fallback
            entries[idx]["retained_chars"] = len(fallback)
            entries[idx]["note"] = _join_note(
                entries[idx].get("note"), f"condense_failed: {exc}; head_tail_trim"
            )

    # If we are still over budget after condensing every large material (e.g.
    # condense returned long output), do a final proportional trim across the
    # condensed set to guarantee the budget.
    if _total_retained(entries) > TARGET_BUDGET:
        resized = [(idx, len(e["retained_text"])) for idx, e in enumerate(entries)]
        total_now = sum(n for _, n in resized)
        for idx, n in resized:
            if total_now <= TARGET_BUDGET:
                break
            if n <= MIN_FLOOR:
                continue
            share = max(MIN_FLOOR, int(TARGET_BUDGET * n / total_now))
            if n > share:
                trimmed = _trim_text(entries[idx]["retained_text"], share)
                entries[idx]["retained_text"] = trimmed
                entries[idx]["retained_chars"] = len(trimmed)
                entries[idx]["note"] = _join_note(entries[idx].get("note"), "post_condense_trim")
                total_now = sum(len(e["retained_text"]) for e in entries)

    return entries


def _llm_condense(model: str, text: str, target_chars: int) -> str:
    """Single-shot condense. Returns plain text (markdown-friendly)."""
    target_words = max(200, target_chars // 6)  # rough chars→words for English; CJK runs denser
    messages = [
        {
            "role": "system",
            "content": (
                "You are a meticulous condenser preparing source material for a "
                "downstream slide-outline writer. Compress the input while "
                "preserving structure: keep all section headings, bullet-point "
                "facts, dates, names, numbers, and quoted statements. Compress "
                "verbose prose. Do not add commentary, do not invent facts, do "
                "not refuse. Output the condensed text only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Condense the following material to roughly {target_words} words "
                f"(~{target_chars} characters). Preserve structure and key data.\n\n"
                f"---\n{text}\n---"
            ),
        },
    ]
    return zenmux.chat(model, messages, temperature=0.2, stream=True)


# --------------------------------------------------------------------------- #
# Advanced mode (RAG)
# --------------------------------------------------------------------------- #


def _advanced_mode(
    materials: list[dict[str, Any]],
    thread_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Chunk + embed materials, persist an index, build digest via diverse retrieval."""
    embed_model = get_model("embeddings")

    # 1. Chunk every material.
    per_material_chunks: list[list[dict[str, Any]]] = []
    flat_texts: list[str] = []
    flat_origin: list[tuple[int, int]] = []  # (material_idx, chunk_idx_within_material)
    for mi, m in enumerate(materials):
        text = m.get("parsed") or ""
        chunks = _chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        per_material_chunks.append(chunks)
        for ci, ch in enumerate(chunks):
            flat_texts.append(ch["text"])
            flat_origin.append((mi, ci))

    if not flat_texts:
        return [_passthrough(m) for m in materials], None

    # 2. Embed in batches.
    with tagged_stream("digest_materials"):
        vectors = zenmux.embeddings(embed_model, flat_texts)
    if len(vectors) != len(flat_texts):
        raise RuntimeError(
            f"embedding count mismatch: got {len(vectors)} vectors for {len(flat_texts)} chunks"
        )

    # Attach embeddings back to chunks.
    for vec, (mi, ci) in zip(vectors, flat_origin):
        per_material_chunks[mi][ci]["embedding"] = vec

    # 3. Persist index manifest + embeddings.
    manifest = _persist_index(thread_id, materials, per_material_chunks)

    # 4. Build digest via diverse retrieval inside each material, sized by share.
    sized = [(m, len(m.get("parsed") or "")) for m in materials]
    total = sum(n for _, n in sized)
    digest: list[dict[str, Any]] = []
    for mi, (m, n) in enumerate(sized):
        chunks = per_material_chunks[mi]
        if n == 0 or not chunks:
            digest.append(_entry(m, "", was_condensed=False, note=None))
            continue
        share = max(MIN_FLOOR, int(TARGET_BUDGET * n / max(1, total)))
        if n <= share:
            digest.append(_entry(m, m["parsed"], was_condensed=False, note=None))
            continue
        picked = _diverse_pick(chunks, share)
        retained = "\n\n…\n\n".join(c["text"] for c in picked)
        digest.append(
            _entry(
                m,
                retained,
                was_condensed=True,
                note=f"rag_picked={len(picked)}/{len(chunks)}",
            )
        )

    return digest, manifest


def _chunk_text(text: str, size: int, overlap: int) -> list[dict[str, Any]]:
    """Sliding-window chunking with paragraph-boundary preference.

    Returns a list of {id, char_start, char_end, text}. Paragraph breaks within
    the window are preferred as split points; falls back to a hard cut if a
    paragraph is longer than `size`.
    """
    if not text:
        return []
    chunks: list[dict[str, Any]] = []
    start = 0
    n = len(text)
    cid = 0
    while start < n:
        end = min(n, start + size)
        if end < n:
            # Prefer the last paragraph break in the window.
            split = text.rfind("\n\n", start, end)
            if split > start + size // 2:
                end = split
        body = text[start:end]
        chunks.append(
            {
                "id": f"c{cid}",
                "char_start": start,
                "char_end": end,
                "text": body,
            }
        )
        cid += 1
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _diverse_pick(
    chunks: list[dict[str, Any]],
    char_budget: int,
    *,
    diversity: float = 0.5,
) -> list[dict[str, Any]]:
    """Greedy MMR (max marginal relevance) over chunk embeddings.

    Treats the centroid of all chunks as the "query" so picks favor coverage
    of the document rather than relevance to a specific question. Stops when
    the cumulative char count exceeds char_budget. Result preserves the
    original chunk order so the retained text reads top-to-bottom.
    """
    if not chunks:
        return []
    embeddings = [c.get("embedding") or [] for c in chunks]
    if not all(embeddings):
        # No embeddings → fall back to head+tail of chunks.
        return _head_tail_chunks(chunks, char_budget)

    centroid = _centroid(embeddings)
    centroid_sims = [_cosine(centroid, e) for e in embeddings]

    chosen: list[int] = []
    chosen_set: set[int] = set()
    used_chars = 0
    while True:
        best_idx = -1
        best_score = -math.inf
        for i, _ in enumerate(chunks):
            if i in chosen_set:
                continue
            relevance = centroid_sims[i]
            if chosen:
                novelty = 1.0 - max(_cosine(embeddings[i], embeddings[j]) for j in chosen)
            else:
                novelty = 1.0
            score = (1 - diversity) * relevance + diversity * novelty
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx < 0:
            break
        # Stop before adding a chunk that would exceed the budget — but always
        # keep at least one chunk so we never return an empty digest.
        chunk_len = len(chunks[best_idx]["text"])
        if chosen and used_chars + chunk_len > char_budget:
            break
        chosen.append(best_idx)
        chosen_set.add(best_idx)
        used_chars += chunk_len
        if used_chars >= char_budget or len(chosen) == len(chunks):
            break

    chosen.sort()
    return [chunks[i] for i in chosen]


def _head_tail_chunks(chunks: list[dict[str, Any]], char_budget: int) -> list[dict[str, Any]]:
    if not chunks:
        return []
    out: list[dict[str, Any]] = []
    used = 0
    # Take from the head until budget half is reached.
    for c in chunks:
        if used >= char_budget // 2:
            break
        out.append(c)
        used += len(c["text"])
    # Then take from the tail.
    for c in reversed(chunks):
        if c in out or used >= char_budget:
            break
        out.append(c)
        used += len(c["text"])
    out.sort(key=lambda c: c["char_start"])
    return out


def _persist_index(
    thread_id: str | None,
    materials: list[dict[str, Any]],
    per_material_chunks: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Write manifest + embeddings under ./threads/{thread_id}/index/.

    Manifest is the truth in checkpointed state; on-disk files are a derived
    cache (mirrors the existing artifacts pattern). If thread_id is missing
    (uncommon — only in tests / playground forks), skip disk write but still
    return an in-memory manifest.
    """
    manifest: dict[str, Any] = {
        "version": 1,
        "embedding_model": get_model("embeddings"),
        "materials": [],
    }

    index_dir: Path | None = None
    if thread_id:
        index_dir = thread_dir(thread_id) / "index"
        index_dir.mkdir(parents=True, exist_ok=True)

    for mi, (m, chunks) in enumerate(zip(materials, per_material_chunks)):
        material_id = f"m{mi}"
        chunk_meta: list[dict[str, Any]] = []
        if index_dir is not None and chunks:
            chunks_path = index_dir / f"{material_id}.chunks.jsonl"
            with chunks_path.open("w", encoding="utf-8") as f:
                for c in chunks:
                    f.write(
                        json.dumps(
                            {
                                "id": c["id"],
                                "char_start": c["char_start"],
                                "char_end": c["char_end"],
                                "text": c["text"],
                                "embedding": c["embedding"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            rel_path = os.path.relpath(chunks_path, index_dir.parent)
        else:
            rel_path = None
        for c in chunks:
            chunk_meta.append(
                {
                    "id": c["id"],
                    "char_start": c["char_start"],
                    "char_end": c["char_end"],
                }
            )
        manifest["materials"].append(
            {
                "material_id": material_id,
                "name": m.get("name"),
                "kind": m.get("kind"),
                "chunks": chunk_meta,
                "chunks_path": rel_path,
            }
        )

    if index_dir is not None:
        (index_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return manifest


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _entry(
    m: dict[str, Any],
    retained_text: str,
    *,
    was_condensed: bool,
    note: str | None,
) -> dict[str, Any]:
    return {
        "name": m.get("name"),
        "kind": m.get("kind"),
        "uri": m.get("uri"),
        "retained_text": retained_text,
        "original_chars": len(m.get("parsed") or ""),
        "retained_chars": len(retained_text),
        "was_condensed": was_condensed,
        "note": note,
    }


def _join_note(existing: str | None, new: str) -> str:
    if not existing:
        return new
    return f"{existing}; {new}"


def _total_retained(entries: list[dict[str, Any]]) -> int:
    return sum(e["retained_chars"] for e in entries)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    n = len(vectors)
    return [x / n for x in acc]
