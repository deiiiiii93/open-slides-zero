"""Deterministic layout decision engine (catalog §6–§7).

The LLM proposes 3–5 candidate patterns from the registry; this scorer ranks them
using weighted heuristics over content type, semantic family, content shape, aspect
ratio, item count, text length, and editorial story role. Tie-breaks (§7) handle
content safety, narrative consistency, and density preference.

Pure function — no I/O, no LLM calls, fully testable with golden fixtures.
"""

from dataclasses import dataclass, field
from typing import Any

from .layouts import (
    FAMILIES,
    MAX_TEXT_LENGTH,
    MAX_TOTAL_ELEMENTS,
    PATTERNS,
    TEXT_EXPANSION,
    default_pattern_for_family,
    family_of,
    resolve_override,
)

ALL_FAMILIES: list[str] = list(FAMILIES.keys())


@dataclass
class SlideSignal:
    """Input features for one slide used by the scorer."""
    content_type: str = "content"            # e.g. "comparison", "data", "timeline", "matrix", "table_grid", "cover", "closing"
    semantic_family: str = "freeform"        # key of SEMANTIC_FAMILIES
    content_shape: str = "freeform_text"     # key of CONTENT_SHAPES
    aspect_ratio: str = "16:9"               # "16:9", "4:3", "21:9"
    item_count: int = 0                      # bullet / card count
    text_length: int = 0                     # char count of primary text
    languages: list[str] = field(default_factory=lambda: ["en"])
    is_cover: bool = False
    is_closing: bool = False
    editorial_enabled: bool = False
    story_role: str | None = None            # cover/context/tension/decision/proof/execution/impact/close
    density_preference: str = "balanced"     # "minimal","balanced","dense","very_dense"
    previous_family: str | None = None
    preset_layout_bias: dict[str, list[str]] | None = None  # {"prefer": [pattern_ids], "avoid": [...]}


@dataclass
class ScoreBreakdown:
    family: str
    score: float
    components: dict[str, float]             # per-rule contribution


def score_families(sig: SlideSignal) -> list[ScoreBreakdown]:
    """Return per-family scores sorted high-to-low."""
    scores: dict[str, float] = {f: 0.0 for f in ALL_FAMILIES}
    components: dict[str, dict[str, float]] = {f: {} for f in ALL_FAMILIES}

    def bump(family: str, rule: str, value: float) -> None:
        scores[family] += value
        components[family][rule] = components[family].get(rule, 0.0) + value

    # ---- §6.1 content type ----
    if sig.content_type in ("comparison", "data"):
        bump("horizontal", "content_type", 0.9)
        bump("grid", "content_type", 0.7)
    if sig.content_type in ("gallery", "photo_gallery", "image_gallery"):
        bump("grid", "content_type", 1.1)
        bump("horizontal", "content_type", 0.35)
    if sig.content_type == "timeline":
        bump("vertical", "content_type", 0.8)
        bump("narrative", "content_type", 0.5)
    if sig.content_type == "matrix":
        bump("grid", "content_type", 0.95)
    if sig.content_type == "table_grid":
        bump("vertical", "content_type", 0.8)
        bump("paginated", "content_type", 0.4)

    # ---- §6.2 semantic family ----
    sem = sig.semantic_family
    if sem == "comparison":
        bump("horizontal", "semantic", 0.85)
        bump("grid", "semantic", 0.75)
    elif sem == "quant":
        bump("horizontal", "semantic", 0.8)
        bump("grid", "semantic", 0.65)
    elif sem == "table":
        bump("vertical", "semantic", 0.85)
        bump("paginated", "semantic", 0.55)
    elif sem == "hierarchy":
        bump("radial", "semantic", 0.8)
        bump("vertical", "semantic", 0.4)
    elif sem == "sequential":
        bump("narrative", "semantic", 0.75)
        bump("vertical", "semantic", 0.55)
    elif sem == "gallery":
        bump("grid", "semantic", 1.2)
        bump("horizontal", "semantic", 0.45)

    # ---- §6.3 content shape ----
    shape = sig.content_shape
    if shape in ("parallel_cards", "parallel_columns"):
        bump("grid", "shape", 0.95)
        bump("horizontal", "shape", 0.7)
    elif shape == "matrix_2x2":
        bump("grid", "shape", 1.1)
    elif shape == "timeline":
        bump("vertical", "shape", 0.9)
        bump("narrative", "shape", 0.6)
    elif shape == "ordered_steps":
        bump("vertical", "shape", 0.75)
        bump("narrative", "shape", 0.7)
    elif shape == "tree":
        bump("radial", "shape", 0.8)
        bump("vertical", "shape", 0.45)
    elif shape == "radial_branches":
        bump("radial", "shape", 1.0)
    elif shape == "chart":
        bump("horizontal", "shape", 0.9)
        bump("grid", "shape", 0.6)
    elif shape == "table":
        bump("vertical", "shape", 0.8)
        bump("paginated", "shape", 0.7)
    elif shape == "image_gallery":
        bump("grid", "shape", 1.35)
        bump("horizontal", "shape", 0.5)

    # ---- §6.4 aspect ratio ----
    if sig.aspect_ratio == "21:9":
        bump("horizontal", "aspect", 1.2)
    elif sig.aspect_ratio == "4:3":
        bump("vertical", "aspect", 1.0)
        bump("adaptive", "aspect", 0.7)
    elif sig.aspect_ratio == "16:9":
        bump("horizontal", "aspect", 0.8)
        bump("vertical", "aspect", 0.2)

    # ---- §6.5 item count ----
    n = sig.item_count
    if n <= 3:
        bump("horizontal", "items", 0.6)
    elif n <= 7:
        bump("vertical", "items", 0.6)
        bump("grid", "items", 0.45)
    else:
        bump("vertical", "items", 0.9)
        bump("paginated", "items", 0.8)

    # ---- §6.6 text length (with §12 language expansion) ----
    max_expansion = max((TEXT_EXPANSION.get(lang, 1.0) for lang in sig.languages), default=1.0)
    effective_text = sig.text_length * max_expansion
    if effective_text > 320:
        bump("vertical", "text", 0.8)
        bump("adaptive", "text", 0.7)
    elif effective_text > 160:
        bump("vertical", "text", 0.5)
    else:
        bump("horizontal", "text", 0.5)

    # ---- §6.7 cover / closing short-circuit ----
    if sig.is_cover or sig.is_closing:
        boost = 2.0 if sig.is_cover else 1.8
        for fam in ALL_FAMILIES:
            if fam == "narrative":
                bump(fam, "cover_closing", boost)
            else:
                # Multiply existing score by 0.3 — convert multiplicative rule to a component.
                current = scores[fam]
                delta = current * 0.3 - current
                bump(fam, "cover_closing_dampen", delta)

    # ---- §6.8 editorial story role ----
    if sig.editorial_enabled and sig.story_role:
        role_map = {
            "cover":     [("horizontal", 1.4), ("narrative", 1.0)],
            "context":   [("horizontal", 1.0), ("adaptive", 0.5)],
            "tension":   [("grid", 1.2), ("horizontal", 0.6)],
            "decision":  [("grid", 1.1), ("horizontal", 0.9)],
            "proof":     [("horizontal", 0.9), ("grid", 0.6)],
            "execution": [("grid", 1.0), ("narrative", 0.8)],
            "impact":    [("grid", 1.2), ("narrative", 0.7)],
            "close":     [("vertical", 1.0), ("narrative", 1.2)],
        }
        for fam, weight in role_map.get(sig.story_role, []):
            bump(fam, f"role:{sig.story_role}", weight)

    # ---- §6.9 preset layout bias ----
    # Net contribution per family is capped at [-0.8, +1.0] so the preset can flip
    # close calls but cannot override clear content fit (e.g. image_gallery -> grid
    # still wins even when the preset prefers narrative patterns).
    if sig.preset_layout_bias:
        prefer = sig.preset_layout_bias.get("prefer") or []
        avoid = sig.preset_layout_bias.get("avoid") or []
        per_family_delta: dict[str, float] = {f: 0.0 for f in ALL_FAMILIES}
        per_family_components: dict[str, dict[str, float]] = {f: {} for f in ALL_FAMILIES}
        for pid in prefer:
            if pid not in PATTERNS:
                continue
            fam = PATTERNS[pid]["family"]
            if fam not in per_family_delta:
                continue
            per_family_delta[fam] += 0.6
            per_family_components[fam][f"preset:prefer:{pid}"] = 0.6
        for pid in avoid:
            if pid not in PATTERNS:
                continue
            fam = PATTERNS[pid]["family"]
            if fam not in per_family_delta:
                continue
            per_family_delta[fam] -= 0.4
            per_family_components[fam][f"preset:avoid:{pid}"] = -0.4
        for fam, raw_delta in per_family_delta.items():
            if raw_delta == 0.0:
                continue
            capped = max(-0.8, min(1.0, raw_delta))
            scale = capped / raw_delta if raw_delta else 0.0
            for rule_key, rule_val in per_family_components[fam].items():
                bump(fam, rule_key, rule_val * scale)

    ranked = sorted(
        (ScoreBreakdown(family=f, score=scores[f], components=components[f])
         for f in ALL_FAMILIES),
        key=lambda s: s.score,
        reverse=True,
    )
    return ranked


def pick_family(sig: SlideSignal) -> tuple[str, list[ScoreBreakdown]]:
    """§7 — apply tie-break rules and return (chosen_family, full_ranking)."""
    ranked = score_families(sig)
    if len(ranked) < 2:
        return ranked[0].family, ranked

    top, second = ranked[0], ranked[1]

    # §7.1 — if score gap is small, apply tie-breaks
    if top.score - second.score <= 0.1:
        candidates = [top, second]

        # (1) content safety — if high risk, prefer vertical/adaptive/paginated
        safe_families = {"vertical", "adaptive", "paginated"}
        high_risk = sig.item_count > MAX_TOTAL_ELEMENTS or sig.text_length > MAX_TEXT_LENGTH
        if high_risk:
            safe = [c for c in candidates if c.family in safe_families]
            if safe:
                return safe[0].family, ranked

        # (2) narrative consistency — prefer same family as previous slide
        if sig.previous_family:
            prev = [c for c in candidates if c.family == sig.previous_family]
            if prev:
                return prev[0].family, ranked

        # (3) density preference — minimal prefers vertical/adaptive
        if sig.density_preference == "minimal":
            gentle = [c for c in candidates if c.family in ("vertical", "adaptive")]
            if gentle:
                return gentle[0].family, ranked

    return top.family, ranked


def pick_pattern(
    sig: SlideSignal,
    llm_candidates: list[str] | None = None,
    override: str | None = None,
) -> dict[str, Any]:
    """End-to-end decision.

    Priority:
      1. `override` (resolved via catalog §11 — pattern id, family, or legacy name)
      2. The highest-scoring LLM candidate that belongs to the chosen family
      3. The chosen family's default pattern

    Returns: {"pattern": str, "family": str, "ranking": [...top3 ScoreBreakdowns...]}
    """
    if override:
        pattern_id = resolve_override(override)
        family = family_of(pattern_id)
        _, full = pick_family(sig)
        return {"pattern": pattern_id, "family": family, "ranking": full[:3],
                "source": "override"}

    family, full = pick_family(sig)

    # Choose a pattern: LLM candidate in the same family preferred; else default.
    # Within the family, an LLM candidate that the preset also prefers wins over
    # a generic in-family candidate so the visual direction shapes pattern choice.
    chosen = None
    preferred_set = set((sig.preset_layout_bias or {}).get("prefer") or [])
    if llm_candidates:
        if preferred_set:
            for pid in llm_candidates:
                if (
                    pid in PATTERNS
                    and family_of(pid) == family
                    and pid in preferred_set
                ):
                    chosen = pid
                    break
        if chosen is None:
            for pid in llm_candidates:
                if pid in PATTERNS and family_of(pid) == family:
                    chosen = pid
                    break
    if chosen is None:
        # Fall back: for cover/closing slides, prefer matching cover/closing patterns in family.
        if sig.is_cover:
            for pid, p in PATTERNS.items():
                if p["kind"] == "cover" and p["family"] == family:
                    chosen = pid
                    break
        elif sig.is_closing:
            for pid, p in PATTERNS.items():
                if p["kind"] == "closing" and p["family"] == family:
                    chosen = pid
                    break
    if chosen is None:
        shape_preferred = {
            "image_gallery": "image_gallery_grid",
        }
        preferred = shape_preferred.get(sig.content_shape)
        if preferred and preferred in PATTERNS and family_of(preferred) == family:
            chosen = preferred
    if chosen is None:
        chosen = default_pattern_for_family(family)

    return {"pattern": chosen, "family": family, "ranking": full[:3], "source": "scorer"}
