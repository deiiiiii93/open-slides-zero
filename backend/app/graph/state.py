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
    out: dict[int, str] = dict(current or {})
    for k, v in (incoming or {}).items():
        out[int(k)] = v
    return out


# ---------------------------------------------------------------------------
# The graph state
# ---------------------------------------------------------------------------

Stage = Literal[
    "ingest", "outline", "await_structure",
    "style", "await_style",
    "layout", "await_layout",
    "consolidate", "html", "ready", "editing",
]


class SlideState(TypedDict, total=False):
    # ---- inputs ----
    materials: list[dict[str, Any]]         # serialized Material list
    expected_pages: int
    language: str                           # primary language code (e.g. "zh", "en")
    languages: list[str]                    # all detected languages
    aspect_ratio: str                       # "16:9" | "4:3" | "21:9"
    density_preference: str                 # "minimal" | "balanced" | "dense" | "very_dense"
    visual_style_preference: str | None     # free-text user hint (e.g. "MBB deck")
    style_reference_image_uri: str | None

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

    # ---- edits ----
    comments: Annotated[list[dict[str, Any]], operator.add]
    pending_edit_ops: list[dict[str, Any]]

    # ---- bookkeeping ----
    thread_id: str
    current_stage: Stage
    errors: Annotated[list[str], operator.add]
