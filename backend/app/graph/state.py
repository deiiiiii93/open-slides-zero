"""SlideState TypedDict + auxiliary dataclasses.

The state lives in the LangGraph checkpointer; filesystem markdown is a derived
mirror. All fields optional so partial forks / regenerate-from-stage work cleanly.

Reducers:
  html_slides is a dict[int, str] merged slide-by-slide so the Send() fan-out
  can commit slides independently without clobbering peers.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Leaf models (Pydantic for validation + JSON mode with LLMs)
# ---------------------------------------------------------------------------


class Material(BaseModel):
    kind: Literal["text", "file", "image"]
    uri: str                                # "text:..." | local path | "image:path"
    name: str | None = None                 # original upload filename, if any
    parsed: str | None = None               # extracted text (filled by ingest)
    note: str | None = None


class ScoreEntry(BaseModel):
    family: str
    score: float
    components: dict[str, float] = Field(default_factory=dict)


class SlideLayout(BaseModel):
    slide_idx: int
    title: str
    pattern: str                            # catalog pattern id
    family: str
    zones: list[str] = Field(default_factory=list)
    wireframe: str = ""                     # ASCII sketch
    content_shape: str = "freeform_text"
    story_role: str | None = None
    ranking_top3: list[ScoreEntry] = Field(default_factory=list)
    notes: str = ""


class Comment(BaseModel):
    slide_idx: int
    box: dict[str, float] | None = None     # {x,y,w,h} in 0..1, optional
    text: str
    resolved: bool = False
    created_at: str | None = None


class EditOp(BaseModel):
    target_stage: Literal["outline", "style", "layout", "html", "image_only"]
    patch_fragment: dict[str, Any] = Field(default_factory=dict)
    affected_slides: list[int] = Field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# Reducer helpers
# ---------------------------------------------------------------------------

def _merge_html(current: dict[int, str] | None, incoming: dict[int, str] | None) -> dict[int, str]:
    """Merge a Send() fan-out partial result into the full slide map."""
    if incoming == {}:
        return {}
    out: dict[int, str] = dict(current or {})
    for k, v in (incoming or {}).items():
        out[int(k)] = v
    return out


def _merge_html_failures(
    current: list[dict[str, Any]] | None,
    incoming: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge per-slide html failures while allowing explicit clearing via []."""
    if incoming is None:
        return list(current or [])
    if incoming == []:
        return []
    out = {
        int(item["slide_idx"]): dict(item)
        for item in (current or [])
    }
    for item in incoming:
        out[int(item["slide_idx"])] = dict(item)
    return [out[idx] for idx in sorted(out)]


# ---------------------------------------------------------------------------
# The graph state
# ---------------------------------------------------------------------------

Stage = Literal[
    "ingest", "outline", "await_structure",
    "advanced_chat",
    "await_outline", "playground",
    "style", "await_style",
    "layout", "await_layout",
    "consolidate", "html", "await_image_insertion", "ready", "editing",
]


class SlideState(TypedDict, total=False):
    # ---- inputs ----
    deck_name: str
    materials: list[dict[str, Any]]         # serialized Material list
    agent_mode: Literal["default", "advanced"]   # "advanced" enables RAG digest + index
    expected_pages: int
    language: str                           # primary language code (e.g. "zh", "en")
    languages: list[str]                    # all detected languages
    aspect_ratio: str                       # "16:9" | "4:3" | "21:9"
    density_preference: str                 # "minimal" | "balanced" | "dense" | "very_dense"
    visual_style_preference: str | None     # free-text user hint (e.g. "MBB deck")
    visual_style_preset_id: str | None      # optional upstream visual direction preset
    visual_style_preset_label: str | None
    visual_style_preset_prompt: str | None
    visual_style_preset_style_bias: dict[str, str] | None
    visual_style_preset_layout_bias: dict[str, Any] | None
    visual_style_preset_html_rules: list[str] | None
    style_reference_image_uri: str | None
    image_urls: list[str]
    creator_prompt: str | None              # lane-specific extra instructions
    parent_thread_id: str | None            # playground base deck for a lane thread
    lane_id: str | None                     # playground lane id within parent
    lane_model_overrides: dict[str, str] | None  # per-run style/layout/html model ids

    # ---- B': condensed materials (digest) ----
    materials_digest: list[dict[str, Any]]  # per-material {name, kind, retained_text, ...}
    materials_index: dict[str, Any] | None  # RAG manifest (advanced mode); None otherwise

    # ---- C' / F': advanced chat planning ----
    advanced_chat_messages: list[dict[str, Any]]
    advanced_chat_draft: dict[str, Any]

    # ---- C / D: structure choice ----
    scenario_id: str
    structure_id: str
    structure_candidates: list[str]

    # ---- E ----
    outline_md: str
    outline_slides: list[dict[str, Any]]    # parsed slide list {title, bullets, role}

    # ---- F ----
    visual_style_md: str
    visual_style: dict[str, Any]            # parsed {palette, typography, density, ...}

    # ---- G ----
    layouts: list[dict[str, Any]]           # SlideLayout serialized

    # ---- H ----
    consolidated_brief_md: str
    brief: dict[str, Any]                   # structured merge consumed by the html fan-out

    # ---- I — fan-out reducer merges per-slide results ----
    html_slides: Annotated[dict[int, str], _merge_html]
    html_slides_base: Annotated[dict[int, str], _merge_html]
    html_failures: Annotated[list[dict[str, Any]], _merge_html_failures]
    pending_html_retry_slides: list[int]

    # ---- optional image insertion ----
    image_assets: list[dict[str, Any]]
    image_insertion_plan: dict[str, Any]
    image_insertion_status: str
    image_generation_errors: list[dict[str, Any]]

    # ---- edits ----
    comments: Annotated[list[dict[str, Any]], operator.add]
    pending_edit_ops: list[dict[str, Any]]

    # ---- bookkeeping ----
    thread_id: str
    current_stage: Stage
    errors: Annotated[list[str], operator.add]
