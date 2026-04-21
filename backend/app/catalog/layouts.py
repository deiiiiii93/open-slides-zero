"""Layout catalog (pure data).

Implements catalog §1–§5:
  - Legacy grid templates (v1)
  - Eight layout families (v2 decision engine)
  - Layout patterns (content / cover / closing / timeline-steps)
  - Semantic families + content shapes
  - Layout intent mapping (orientation/composition/flow/emphasis)

Decision heuristics (§6), tie-breaks (§7), constraint validation (§10) and
text-expansion (§12) live in sibling modules (scorer.py, validator.py).
"""

from typing import Any, Literal

# ---------------------------------------------------------------------------
# §1 Legacy layout templates (v1) — used by the HTML shell for grid CSS.
# ---------------------------------------------------------------------------

LEGACY_LAYOUTS: dict[str, dict[str, Any]] = {
    "left_right_split": {"grid_template": "1fr 1.5fr", "best_for": "image-text parallel"},
    "top_bottom": {"grid_template": "auto 2fr 1fr", "best_for": "large chart + key takeaways"},
    "three_columns": {"grid_template": "1fr 1fr 1fr", "best_for": "feature comparison"},
    "matrix_quadrant": {"grid_template": "1fr 1fr / 1fr 1fr", "best_for": "2x2 positioning"},
    "radial_core": {"grid_template": "radial", "best_for": "hub-spoke concepts"},
    "flow_timeline": {"grid_template": "1fr 1fr 1fr", "best_for": "process flow / timeline"},
}


# ---------------------------------------------------------------------------
# §2 Eight layout families with default pattern.
# ---------------------------------------------------------------------------

LayoutFamily = Literal[
    "horizontal", "vertical", "grid", "radial",
    "adaptive", "stateful", "paginated", "narrative",
]

FAMILIES: dict[str, dict[str, Any]] = {
    "horizontal": {"default": "chart_left_bullets_right"},
    "vertical":   {"default": "text_top_chart_bottom"},
    "grid":       {"default": "mixed_2x2_focus"},
    "radial":     {"default": "radial_compact"},
    "adaptive":   {"default": "adaptive_single_column"},
    "stateful":   {"default": "state_machine_panel"},
    "paginated":  {"default": "paginated_document"},
    "narrative":  {"default": "narrative_focus"},
}


# ---------------------------------------------------------------------------
# §3 Pattern registry.
#
# Each pattern declares: family, legacy layout mapping, zones (semantic slots),
# and an intent (orientation/composition/emphasis) consumed by the scorer.
# ---------------------------------------------------------------------------

PATTERNS: dict[str, dict[str, Any]] = {
    # --- Content patterns ---
    "chart_left_bullets_right": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["content", "visual"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "visual"},
    },
    "text_top_chart_bottom": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["content", "visual"], "kind": "content",
        "intent": {"orientation": "vertical", "composition": "stack", "emphasis": "visual"},
    },
    "three_parallel_columns": {
        "family": "horizontal", "legacy": "three_columns",
        "zones": ["col-1", "col-2", "col-3"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "columns", "emphasis": "balanced"},
    },
    "mixed_2x2_focus": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["chart", "image", "content"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "grid", "emphasis": "balanced"},
    },
    "radial_compact": {
        "family": "radial", "legacy": "radial_core",
        "zones": ["core", "ring", "notes"], "kind": "content",
        "intent": {"orientation": "radial", "composition": "radial", "emphasis": "center"},
    },
    "linear_or_serpentine_timeline": {
        "family": "narrative", "legacy": "flow_timeline",
        "zones": ["timeline", "content"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "lanes",
                   "flow_axis": "x", "emphasis": "sequence"},
    },
    "adaptive_single_column": {
        "family": "adaptive", "legacy": "top_bottom",
        "zones": ["content"], "kind": "content",
        "intent": {"orientation": "vertical", "composition": "single_column", "emphasis": "content"},
    },
    "state_machine_panel": {
        "family": "stateful", "legacy": "left_right_split",
        "zones": ["states", "diagram"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "balanced"},
    },
    "narrative_focus": {
        "family": "narrative", "legacy": "top_bottom",
        "zones": ["headline", "evidence"], "kind": "content",
        "intent": {"orientation": "vertical", "composition": "stack", "emphasis": "headline"},
    },
    "paginated_document": {
        "family": "paginated", "legacy": "top_bottom",
        "zones": ["content"], "kind": "content",
        "intent": {"orientation": "vertical", "composition": "document",
                   "flow_axis": "y", "emphasis": "content"},
    },
    "bilingual_split": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["lang-a", "lang-b"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "columns", "emphasis": "balanced"},
    },
    "safe_vertical_stack": {
        "family": "adaptive", "legacy": "top_bottom",
        "zones": ["content", "support"], "kind": "content",
        "intent": {"orientation": "vertical", "composition": "stack", "emphasis": "content"},
    },
    "content_f_shape": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["title", "content", "visual", "footer"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "content"},
    },
    "content_card_grid": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["title", "card-1", "card-2", "card-3", "card-4", "card-5", "card-6"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "cards", "emphasis": "balanced"},
    },
    "editorial_thesis_panel": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["eyebrow", "thesis", "proof", "footer"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "headline"},
    },
    "editorial_selection_shortlist": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["title", "shortlist", "rationale", "selected"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "shortlist", "emphasis": "decision"},
    },
    "editorial_reason_cards": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["title", "card-1", "card-2", "card-3"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "cards", "emphasis": "balanced"},
    },
    "editorial_execution_grid": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["title", "step-1", "step-2", "step-3", "step-4"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "grid", "emphasis": "sequence"},
    },
    "data_dashboard": {
        "family": "grid", "legacy": "matrix_quadrant",
        "zones": ["kpi-1", "kpi-2", "kpi-3", "chart", "detail"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "dashboard", "emphasis": "data"},
    },
    "data_split_metric": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["visual", "metric1", "metric2", "metric3", "caption"], "kind": "content",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "data"},
    },
    "editorial_inverted_impact": {
        "family": "grid", "legacy": "three_columns",
        "zones": ["eyebrow", "title", "col-1", "col-2", "col-3"], "kind": "content",
        "intent": {"orientation": "grid", "composition": "columns", "emphasis": "impact"},
    },

    # --- Cover patterns ---
    "cover_center_title": {
        "family": "narrative", "legacy": "top_bottom",
        "zones": ["title", "subtitle", "date"], "kind": "cover",
        "intent": {"orientation": "vertical", "composition": "centered", "emphasis": "headline"},
    },
    "cover_left_title": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["title", "subtitle", "visual"], "kind": "cover",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "headline"},
    },
    "cover_split_image": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["title", "subtitle", "image"], "kind": "cover",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "visual"},
    },
    "cover_bottom_bar": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["visual", "title", "subtitle"], "kind": "cover",
        "intent": {"orientation": "vertical", "composition": "stack", "emphasis": "headline"},
    },
    "cover_full_bleed": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["background", "title", "subtitle"], "kind": "cover",
        "intent": {"orientation": "vertical", "composition": "full_bleed", "emphasis": "visual"},
    },
    "editorial_hero_split": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["eyebrow", "title", "support", "visual"], "kind": "cover",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "headline"},
    },
    "editorial_full_bleed_campaign": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["background", "title", "actions", "funnel"], "kind": "cover",
        "intent": {"orientation": "vertical", "composition": "full_bleed", "emphasis": "visual"},
    },

    # --- Closing patterns ---
    "closing_centered": {
        "family": "narrative", "legacy": "top_bottom",
        "zones": ["title", "contact"], "kind": "closing",
        "intent": {"orientation": "vertical", "composition": "centered", "emphasis": "headline"},
    },
    "closing_split_cta": {
        "family": "horizontal", "legacy": "left_right_split",
        "zones": ["title", "cta"], "kind": "closing",
        "intent": {"orientation": "horizontal", "composition": "split", "emphasis": "headline"},
    },
    "closing_minimal": {
        "family": "narrative", "legacy": "top_bottom",
        "zones": ["title"], "kind": "closing",
        "intent": {"orientation": "vertical", "composition": "centered", "emphasis": "headline"},
    },
    "editorial_manifesto_closer": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["eyebrow", "title", "closer"], "kind": "closing",
        "intent": {"orientation": "vertical", "composition": "centered", "emphasis": "headline"},
    },

    # --- Timeline & steps patterns ---
    "timeline_horizontal": {
        "family": "horizontal", "legacy": "flow_timeline",
        "zones": ["title", "timeline", "nodes"], "kind": "timeline",
        "intent": {"orientation": "horizontal", "composition": "lanes",
                   "flow_axis": "x", "emphasis": "sequence"},
    },
    "timeline_vertical": {
        "family": "vertical", "legacy": "top_bottom",
        "zones": ["title", "timeline", "nodes"], "kind": "timeline",
        "intent": {"orientation": "vertical", "composition": "lanes",
                   "flow_axis": "y", "emphasis": "sequence"},
    },
    "steps_horizontal": {
        "family": "horizontal", "legacy": "top_bottom",
        "zones": ["title", "steps"], "kind": "timeline",
        "intent": {"orientation": "horizontal", "composition": "lanes",
                   "flow_axis": "x", "emphasis": "sequence"},
    },
    "steps_vertical": {
        "family": "vertical", "legacy": "left_right_split",
        "zones": ["title", "steps", "visual"], "kind": "timeline",
        "intent": {"orientation": "vertical", "composition": "lanes",
                   "flow_axis": "y", "emphasis": "sequence"},
    },
}


# ---------------------------------------------------------------------------
# §4 Semantic families + content shapes.
# ---------------------------------------------------------------------------

SEMANTIC_FAMILIES: dict[str, dict[str, Any]] = {
    "cover":      {"slide_types": ["cover"], "default_shape": "freeform_text"},
    "closing":    {"slide_types": ["closing"], "default_shape": "freeform_text"},
    "sequential": {"slide_types": ["timeline", "steps", "flowchart", "funnel", "pyramid"],
                   "default_shape": "ordered_steps"},
    "comparison": {"slide_types": ["comparison", "swot", "quadrant"],
                   "default_shape": "parallel_columns"},
    "hierarchy":  {"slide_types": ["hierarchy", "mindmap"], "default_shape": "tree"},
    "quant":      {"slide_types": ["data", "data_cards", "chart_column",
                                   "chart_bar", "chart_pie", "chart_line"],
                   "default_shape": "chart"},
    "table":      {"slide_types": ["table_grid"], "default_shape": "table"},
    "freeform":   {"slide_types": ["matrix", "content", "bullet_list", "wordcloud"],
                   "default_shape": "freeform_text"},
}

CONTENT_SHAPES: list[str] = [
    "timeline", "ordered_steps", "parallel_cards", "parallel_columns",
    "matrix_2x2", "tree", "radial_branches", "funnel", "pyramid",
    "keyword_cloud", "chart", "table", "freeform_text",
]


# ---------------------------------------------------------------------------
# Safety / density thresholds (§8 continuation + §10.2 font limits).
# ---------------------------------------------------------------------------

MAX_BULLETS_PER_SLIDE = 7
MAX_TEXT_LENGTH = 1150
MAX_TOTAL_ELEMENTS = 17
MAX_ADDITIONAL_CONTINUATIONS = 2

CANVAS_SIZES = {
    "16:9": (960, 540),
    "4:3":  (960, 720),
    "21:9": (960, 411),
}
SAFE_MARGIN = 30

FONT_LIMITS = {
    "very_dense": {"body": (9, 11),  "heading": (16, 20), "display": (24, 28)},
    "dense":      {"body": (10, 12), "heading": (18, 22), "display": (26, 30)},
    "balanced":   {"body": (11, 13), "heading": (20, 24), "display": (28, 32)},
    "sparse":     {"body": (12, 14), "heading": (22, 26), "display": (30, 34)},
}


# ---------------------------------------------------------------------------
# §12 text expansion factors.
# ---------------------------------------------------------------------------

TEXT_EXPANSION: dict[str, float] = {
    "de": 1.30, "fr": 1.15, "ar": 1.20, "he": 1.15,
    "en": 1.00, "zh": 0.90, "ja": 0.80,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_patterns(kind: str | None = None) -> list[str]:
    if kind is None:
        return list(PATTERNS.keys())
    return [pid for pid, p in PATTERNS.items() if p["kind"] == kind]


def get_pattern(pattern_id: str) -> dict[str, Any]:
    if pattern_id not in PATTERNS:
        raise KeyError(f"Unknown pattern_id: {pattern_id}")
    return PATTERNS[pattern_id]


def family_of(pattern_id: str) -> str:
    return get_pattern(pattern_id)["family"]


def default_pattern_for_family(family: str) -> str:
    if family not in FAMILIES:
        raise KeyError(f"Unknown family: {family}")
    return FAMILIES[family]["default"]


def resolve_override(value: str) -> str:
    """§11 — accept pattern id, family name, or legacy layout. Return pattern id."""
    if value in PATTERNS:
        return value
    if value in FAMILIES:
        return FAMILIES[value]["default"]
    for pid, p in PATTERNS.items():
        if p["legacy"] == value:
            return pid
    raise ValueError(f"Cannot resolve layout override: {value!r}")
