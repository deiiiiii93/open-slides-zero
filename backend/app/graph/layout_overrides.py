"""Shared helpers for deterministic layout overrides."""

from __future__ import annotations

from typing import Any, Mapping

from ..catalog import layouts as L
from ..catalog.scorer import SlideSignal, pick_pattern


def apply_layout_overrides(
    layouts: list[dict[str, Any]] | None,
    overrides: Mapping[int | str, str] | None,
) -> list[dict[str, Any]]:
    """Apply explicit per-slide pattern overrides using the catalog scorer."""
    out = [dict(layout) for layout in (layouts or [])]
    if not overrides:
        return out

    for slide_idx_raw, override in overrides.items():
        try:
            slide_idx = int(slide_idx_raw)
        except (TypeError, ValueError):
            continue
        if slide_idx < 0 or slide_idx >= len(out):
            continue

        decision = pick_pattern(SlideSignal(), override=override)
        pattern_id = decision["pattern"]
        out[slide_idx] = {
            **out[slide_idx],
            "pattern": pattern_id,
            "family": L.family_of(pattern_id),
            "zones": L.get_pattern(pattern_id)["zones"],
        }

    return out
