"""Stage C + E — propose structure candidates and generate the outline markdown.

`propose_structures_node` runs before HITL (D) to let the user pick.
`outline_node` runs after the user has chosen scenario + structure.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...catalog.scenarios import SCENARIO_DEFINITIONS, structures_for
from ...catalog.structures import STRUCTURE_DEFINITIONS
from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream


class _ProposedChoices(BaseModel):
    recommended_scenario_id: str
    candidate_structure_ids: list[str] = Field(min_length=1, max_length=4)
    rationale: str


def _digest_blob(state: dict[str, Any]) -> str:
    """Build the materials view consumed by structure / outline.

    Reads from the budgeted `materials_digest` produced by digest_materials_node.
    Falls back to raw `materials` only if digest is missing (e.g., legacy
    threads created before the digest node existed).
    """
    digest = state.get("materials_digest") or []
    if digest:
        return "\n\n---\n\n".join(
            f"## {d.get('name') or d.get('kind') or 'material'}\n{d['retained_text']}"
            for d in digest
            if d.get("retained_text")
        )
    return "\n\n---\n\n".join(
        (m.get("parsed") or "") for m in state.get("materials", [])
    )


def propose_structures_node(state: dict[str, Any]) -> dict[str, Any]:
    """Suggest scenario + shortlist of structures for the user to pick from."""
    materials_blob = _digest_blob(state)

    scenario_catalog = "\n".join(
        f"- {s['id']}: {s['name_en']} ({s['name_zh']}) — structures={s['structures']}"
        for s in SCENARIO_DEFINITIONS
    )
    structure_catalog = "\n".join(
        f"- {sid}: {sdef['name_en']} — {sdef['description_en']}"
        for sid, sdef in STRUCTURE_DEFINITIONS.items()
        if not sdef.get("legacy")
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior presentation strategist. Given raw material, pick the most "
                "fitting business scenario and shortlist 2-3 narrative structures that best "
                "match the material. Only select scenario.structures entries that actually fit."
            ),
        },
        {
            "role": "user",
            "content": (
                f"MATERIAL:\n{materials_blob}\n\n"
                f"SCENARIOS:\n{scenario_catalog}\n\n"
                f"STRUCTURES:\n{structure_catalog}\n\n"
                f"Expected pages: {state.get('expected_pages', 10)}"
            ),
        },
    ]
    model = get_model("outline")
    push_event({"node": "propose_structure", "state": "started", "model": model})
    with tagged_stream("propose_structure"):
        picked = zenmux.chat_structured(
            model, messages, _ProposedChoices, temperature=0.2, stream=True
        )
    push_event({"node": "propose_structure", "state": "finished", "model": model})
    return {
        "scenario_id": picked.recommended_scenario_id,
        "structure_candidates": picked.candidate_structure_ids,
        "current_stage": "await_structure",
    }


# --------------------------------------------------------------------------- #


class _OutlineSlide(BaseModel):
    title: str
    role: str = Field(description="cover | context | tension | decision | proof | execution | impact | close | closing")
    bullets: list[str] = Field(default_factory=list, max_length=8)
    image_slots: list[str] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Concise image-placeholder prompt hints. ONLY populate when the chosen "
            "structure is Gallery, or when the source materials explicitly include "
            "images that must be displayed. For all other structures (hero_journey, "
            "scqa, problem_solution, mece, comparison, timeline, narrative, etc.) "
            "leave this list empty. Populating it forces the slide into image-grid "
            "layout downstream, so do not use it as a generic 'this might benefit "
            "from a visual' hint."
        ),
    )
    speaker_notes: str = ""


class _Outline(BaseModel):
    language: str = Field(description="ISO code: zh, en, ja, ...")
    slides: list[_OutlineSlide] = Field(min_length=2, max_length=40)
    summary: str = ""


def outline_node(state: dict[str, Any]) -> dict[str, Any]:
    scenario_id = state["scenario_id"]
    structure_id = state["structure_id"]
    pages = state.get("expected_pages", 10)
    structure = STRUCTURE_DEFINITIONS[structure_id]
    scenario = next(s for s in SCENARIO_DEFINITIONS if s["id"] == scenario_id)

    # Defend against invalid pairings.
    if structure_id not in structures_for(scenario_id):
        # Allowed but warn via a speaker note; we don't block the user.
        pass

    materials_blob = _digest_blob(state)
    if structure_id == "gallery":
        image_slot_guidance = (
            "\nGallery-specific requirements:\n"
            "- Build an image-led album/gallery outline, not a text-heavy report.\n"
            "- For every non-closing slide, populate image_slots with 1-4 concise "
            "image placeholder descriptions.\n"
            "- Keep captions, scene context, dates, places, tags, and source notes in bullets.\n"
            "- Keep image-slot instructions out of bullets; put them only in image_slots.\n"
            "- Do not write placeholder copy such as 'Add image here' in bullets or titles.\n"
        )
    else:
        image_slot_guidance = (
            "\nimage_slots rule for this non-Gallery structure:\n"
            "- Leave image_slots EMPTY for every slide. Do not invent image hints.\n"
            "- Layouts here are text/data driven; populating image_slots forces the "
            "slide into an image-grid layout, which is wrong for this structure.\n"
            "- Exception: if the source material itself contains images that the "
            "user clearly wants displayed verbatim, you may add a single concise "
            "image_slot referencing them.\n"
        )

    revision_feedback = (state.get("outline_revision_feedback") or "").strip()
    prior_outline = (state.get("outline_md") or "").strip()
    revision_block = ""
    if revision_feedback:
        revision_block = (
            "\n\nOUTLINE REVISION REQUEST:\n"
            "Regenerate the outline by applying these human review comments. "
            "Keep the selected scenario and structure unless the comments explicitly "
            "require a change.\n\n"
            f"Human comments:\n{revision_feedback}\n\n"
            f"Prior outline:\n{prior_outline[:6000] or '(none)'}"
        )

    messages = [
        {
            "role": "system",
            "content": (
                f"You are an outline creator. Build a {pages}-page slide outline that strictly "
                f"follows the chosen structure. Every slide must have a clear role.\n\n"
                f"Scenario: {scenario['name_en']}\n"
                f"Structure: {structure['name_en']} — {structure['description_en']}\n"
                f"Tone: {structure['tone_en']}\n"
                f"Focus: {structure['focus_en']}\n"
                f"Evidence: {structure['evidence_en']}\n"
                f"Slide mix: {structure['slide_mix_en']}\n\n"
                f"{image_slot_guidance}"
                "Keep bullets short and information-dense. Detect language from the material."
            ),
        },
        {"role": "user", "content": f"MATERIAL:\n{materials_blob}{revision_block}"},
    ]
    # No max_tokens cap: outlines in dense languages (zh/ja) plus schema overhead
    # regularly exceeded 4000 tokens and truncated mid-JSON, causing the
    # chat_structured retry to fail both attempts on the same truncation.
    model = get_model("outline")
    push_event({"node": "outline", "state": "started", "model": model})
    with tagged_stream("outline"):
        outline = zenmux.chat_structured(
            model, messages, _Outline, temperature=0.3, stream=True
        )
    push_event({"node": "outline", "state": "finished", "model": model})

    md_lines = [
        f"# Outline ({structure['name_en']} / {scenario['name_en']})",
        f"Language: {outline.language}",
        "",
    ]
    for i, s in enumerate(outline.slides, 1):
        md_lines.append(f"## {i}. {s.title}")
        md_lines.append(f"_role: {s.role}_")
        for b in s.bullets:
            md_lines.append(f"- {b}")
        if s.image_slots:
            md_lines.append("")
            md_lines.append("_image_slots:_")
            for slot in s.image_slots:
                md_lines.append(f"- {slot}")
        if s.speaker_notes:
            md_lines.append("")
            md_lines.append(f"> {s.speaker_notes}")
        md_lines.append("")
    outline_md = "\n".join(md_lines)

    return {
        "outline_md": outline_md,
        "outline_slides": [s.model_dump() for s in outline.slides],
        "outline_revision_feedback": None,
        "language": outline.language,
        "languages": [outline.language],
        "current_stage": "await_outline",
    }
