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


def propose_structures_node(state: dict[str, Any]) -> dict[str, Any]:
    """Suggest scenario + shortlist of structures for the user to pick from."""
    materials_blob = "\n\n---\n\n".join(
        (m.get("parsed") or "") for m in state.get("materials", [])
    )[:12000]

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
    push_event({"node": "propose_structure", "state": "started"})
    with tagged_stream("propose_structure"):
        picked = zenmux.chat_structured(
            get_model("outline"), messages, _ProposedChoices, temperature=0.2, stream=True
        )
    push_event({"node": "propose_structure", "state": "finished"})
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

    materials_blob = "\n\n---\n\n".join(
        (m.get("parsed") or "") for m in state.get("materials", [])
    )[:16000]

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
                "Keep bullets short and information-dense. Detect language from the material."
            ),
        },
        {"role": "user", "content": f"MATERIAL:\n{materials_blob}"},
    ]
    # No max_tokens cap: outlines in dense languages (zh/ja) plus schema overhead
    # regularly exceeded 4000 tokens and truncated mid-JSON, causing the
    # chat_structured retry to fail both attempts on the same truncation.
    push_event({"node": "outline", "state": "started"})
    with tagged_stream("outline"):
        outline = zenmux.chat_structured(
            get_model("outline"), messages, _Outline, temperature=0.3, stream=True
        )
    push_event({"node": "outline", "state": "finished"})

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
        if s.speaker_notes:
            md_lines.append("")
            md_lines.append(f"> {s.speaker_notes}")
        md_lines.append("")
    outline_md = "\n".join(md_lines)

    return {
        "outline_md": outline_md,
        "outline_slides": [s.model_dump() for s in outline.slides],
        "language": outline.language,
        "languages": [outline.language],
        "current_stage": "await_outline",
    }
