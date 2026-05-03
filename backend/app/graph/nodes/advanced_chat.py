"""Advanced chat planning helpers.

Advanced mode replaces the separate structure / outline / style HITL gates
with one conversational draft. Once approved, the draft is normalized into the
same state fields the existing layout node already consumes.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ...catalog.scenarios import SCENARIO_DEFINITIONS
from ...catalog.structures import STRUCTURE_DEFINITIONS
from ...llm import zenmux
from ...llm.models import get_model
from ...llm.stream import push_event, tagged_stream

log = logging.getLogger(__name__)


class AdvancedChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    choices: list["AdvancedChatChoice"] = Field(default_factory=list)


class AdvancedChatChoice(BaseModel):
    label: str
    description: str = ""
    message: str


class AdvancedTypography(BaseModel):
    heading_family: str
    body_family: str
    display_family: str | None = None
    rationale: str = ""


class AdvancedPalette(BaseModel):
    primary: str
    secondary: str
    accent: str
    neutral_dark: str
    neutral_light: str
    background: str
    roles: dict[str, str] = Field(default_factory=dict)


class AdvancedVisualStyle(BaseModel):
    tone: str = Field(description="editorial | mbb | academic | keynote | product")
    palette: AdvancedPalette
    typography: AdvancedTypography
    density: str = Field(description="minimal | balanced | dense | very_dense")
    imagery_policy: str
    motion_policy: str = "static"
    rationale: str = ""


class AdvancedOutlineSlide(BaseModel):
    title: str
    role: str
    bullets: list[str] = Field(default_factory=list, max_length=8)
    image_slots: list[str] = Field(default_factory=list, max_length=6)
    speaker_notes: str = ""


class AdvancedChatDraft(BaseModel):
    scenario_id: str
    structure_id: str
    language: str = "en"
    summary: str = ""
    outline_slides: list[AdvancedOutlineSlide] = Field(min_length=2, max_length=40)
    visual_style: AdvancedVisualStyle


def empty_advanced_chat_draft(state: dict[str, Any]) -> dict[str, Any]:
    """Return a lightweight placeholder draft for the initial interrupt."""
    return {
        "scenario_id": state.get("scenario_id") or "",
        "structure_id": state.get("structure_id") or "",
        "language": state.get("language") or "en",
        "summary": "",
        "outline_md": "",
        "outline_slides": [],
        "visual_style_md": "",
        "visual_style": {},
    }


def init_advanced_chat_node(state: dict[str, Any]) -> dict[str, Any]:
    """Prepare the chat gate and seed it with proactive planner guidance."""
    existing_messages = state.get("advanced_chat_messages") or []
    existing_draft = state.get("advanced_chat_draft") or empty_advanced_chat_draft(state)
    if existing_messages:
        return {
            "current_stage": "advanced_chat",
            "advanced_chat_messages": existing_messages,
            "advanced_chat_draft": existing_draft,
        }

    model = get_model("advanced_chat")
    push_event({"node": "advanced_chat", "state": "started", "model": model})
    fallback_message = initial_guidance_fallback()
    try:
        seed_messages = [{
            "role": "user",
            "content": (
                "Start the advanced planning conversation now. Analyze the material, "
                "recommend a narrative structure and visual style direction, and ask "
                "one focused question that helps the user confirm or adjust the plan. "
                "Do not wait for the user to tell you where to begin."
            ),
        }]
        with tagged_stream("advanced_chat"):
            assistant = zenmux.chat(
                model,
                conversation_messages(state, seed_messages, include_materials=True),
                temperature=0.45,
                stream=True,
            )
        messages = [{"role": "assistant", "content": assistant}]
    except Exception as exc:  # noqa: BLE001
        log.exception("advanced chat initial guidance failed")
        messages = [{"role": "assistant", "content": fallback_message}]
        push_event({
            "node": "advanced_chat",
            "state": "error",
            "model": model,
            "error": f"initial guidance failed: {exc}",
        })

    try:
        draft = zenmux.chat_structured(
            model,
            draft_extraction_messages(state, messages),
            AdvancedChatDraft,
            temperature=0.2,
            stream=False,
        )
        draft_payload = advanced_draft_payload(draft)
    except Exception as exc:  # noqa: BLE001
        log.exception("advanced chat initial draft extraction failed")
        draft_payload = existing_draft
        push_event({
            "node": "advanced_chat",
            "state": "error",
            "model": model,
            "error": f"initial draft extraction failed: {exc}",
        })

    messages[-1]["choices"] = choices_from_text(messages[-1]["content"]) or suggested_choices(draft_payload)
    push_event({"node": "advanced_chat", "state": "finished", "model": model})
    return {
        "current_stage": "advanced_chat",
        "advanced_chat_messages": messages,
        "advanced_chat_draft": draft_payload,
    }


def suggested_choices(draft: dict[str, Any] | None) -> list[dict[str, str]]:
    """Clickable choice boxes for the next user turn.

    Keep these deterministic and compact. The LLM supplies the conversational
    rationale; these buttons give the user fast ways to decide structure/style.
    """
    draft = draft or {}
    structure_id = str(draft.get("structure_id") or "").strip()
    style = draft.get("visual_style") if isinstance(draft.get("visual_style"), dict) else {}
    tone = str((style or {}).get("tone") or "").strip()
    outline_slides = draft.get("outline_slides") if isinstance(draft.get("outline_slides"), list) else []
    has_draft = bool(structure_id and style and outline_slides)

    if has_draft:
        style_label = f"{tone} style" if tone else "current style"
        return [
            {
                "label": "Use this direction",
                "description": f"Keep {structure_id} and the {style_label}.",
                "message": (
                    "Use this recommended structure and style. Tighten the draft only "
                    "where needed, then keep it ready for layout."
                ),
            },
            {
                "label": "More executive",
                "description": "Sharper answer-first story with restrained boardroom visuals.",
                "message": (
                    "Make the deck more executive: use an answer-first structure, "
                    "crisper slide titles, denser evidence, and a restrained premium style."
                ),
            },
            {
                "label": "More visual",
                "description": "More editorial rhythm, stronger imagery, less text density.",
                "message": (
                    "Make the deck more visual and editorial: add stronger visual moments, "
                    "clearer hierarchy, and reduce text density where possible."
                ),
            },
            {
                "label": "Cleaner product style",
                "description": "Product clarity, lighter palette, simpler slide system.",
                "message": (
                    "Keep the story clear but shift the visual style toward clean product "
                    "clarity: lighter palette, simple typography, and practical layouts."
                ),
            },
        ]

    return [
        {
            "label": "Answer-first executive",
            "description": "Lead with the recommendation, then prove it.",
            "message": (
                "Use an answer-first executive structure. Lead with the recommendation, "
                "then prove it with evidence and an execution path. Use a restrained "
                "strategic visual style."
            ),
        },
        {
            "label": "Build the case",
            "description": "Use context, tension, answer, proof.",
            "message": (
                "Use a tension-building structure. Start with context, make the problem "
                "or opportunity clear, then reveal the recommendation and proof. Use an "
                "editorial analytical style."
            ),
        },
        {
            "label": "Compare options",
            "description": "Frame alternatives and make a decision.",
            "message": (
                "Use a comparison-led structure. Show the main options, evaluate tradeoffs, "
                "make the decision, and close with next steps. Use a clean analytical style."
            ),
        },
        {
            "label": "Visual story",
            "description": "Make it narrative and image-led.",
            "message": (
                "Use a more visual narrative structure. Build the story through scenes, "
                "evidence moments, and image-led slides, with an expressive editorial style."
            ),
        },
    ]


def choices_from_text(content: str) -> list[dict[str, str]]:
    """Extract simple A/B/C/D choices the model wrote in markdown."""
    choices: list[dict[str, str]] = []
    for raw_line in content.splitlines():
        line = _choice_line(raw_line)
        match = re.match(
            r"^(?:\*\*)?\(?([A-Da-d])\)?(?:[.)])?(?:\*\*)?[\s:：\-–—]+(.+)$",
            line,
        )
        if not match:
            continue
        option_id = match.group(1).upper()
        text = _clean_choice_text(match.group(2))
        if not text:
            continue
        choices.append({
            "label": option_id,
            "description": text[:140],
            "message": f"Choose option {option_id}: {text}",
        })
        if len(choices) >= 4:
            break
    return choices if len(choices) >= 2 else []


def _clean_choice_text(value: str) -> str:
    value = re.sub(r"\*\*", "", value)
    value = re.sub(r"`", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -–—:")


def _choice_line(raw_line: str) -> str:
    line = raw_line.strip()
    while line.startswith(">"):
        line = line[1:].strip()
    line = re.sub(r"^[-*+]\s+", "", line)
    return line


def initial_guidance_fallback() -> str:
    return (
        "## Let’s lock the deck direction\n\n"
        "I’ll guide this in two decisions before layout:\n\n"
        "1. **Narrative structure**: choose whether this should lead with the answer, "
        "build tension before the answer, compare options, or tell a chronological story.\n"
        "2. **Visual style**: choose whether this should feel executive, editorial, "
        "product-clean, academic, or more expressive.\n\n"
        "Reply with the audience and the decision you want the deck to drive. If you "
        "already know the style, add that too."
    )


def digest_blob(state: dict[str, Any]) -> str:
    digest = state.get("materials_digest") or []
    if digest:
        return "\n\n---\n\n".join(
            f"## {d.get('name') or d.get('kind') or 'material'}\n{d.get('retained_text') or ''}"
            for d in digest
            if d.get("retained_text")
        )
    return "\n\n---\n\n".join(
        (m.get("parsed") or "") for m in state.get("materials", [])
    )


def catalog_context() -> str:
    scenarios = "\n".join(
        f"- {s['id']}: {s['name_en']} ({s['name_zh']}) — structures={s['structures']}"
        for s in SCENARIO_DEFINITIONS
    )
    structures = "\n".join(
        f"- {sid}: {sdef['name_en']} — {sdef['description_en']}"
        for sid, sdef in STRUCTURE_DEFINITIONS.items()
        if not sdef.get("legacy")
    )
    return f"SCENARIOS:\n{scenarios}\n\nSTRUCTURES:\n{structures}"


def system_prompt(state: dict[str, Any]) -> str:
    return (
        "You are the advanced planning copilot for a slide-deck agent. Help the user "
        "settle structure, slide outline, and visual style in one concise conversation. "
        "You are not a passive chatbot: actively recommend the next best structure and "
        "style, explain tradeoffs briefly, and ask one focused question per turn when "
        "a user decision is still needed. Always keep the response in markdown. Prefer "
        "decisive recommendations over long option lists, but honor explicit user "
        "preferences.\n\n"
        "When you propose a plan, make sure it can be converted into:\n"
        "- one valid scenario_id and structure_id from the catalog\n"
        "- a slide outline with titles, roles, bullets, optional image_slots, and notes\n"
        "- a visual style system with palette, typography, density, imagery, and rationale\n\n"
        "For every planning turn, include:\n"
        "- **Recommendation**: the current best structure/style choice\n"
        "- **Why**: one concise reason grounded in the source material\n"
        "- **Your call**: the specific decision or adjustment you need from the user\n\n"
        "When the user needs to choose, write the choices as explicit markdown options "
        "using letter labels, such as '- **(A) ...**' and '- **(B) ...**'. These "
        "lettered options become clickable buttons in the UI, so they must match the "
        "specific decision you are asking about. Do not rely on generic canned choices.\n\n"
        "Do not advance to layout yourself; the user will click Continue to layout.\n\n"
        f"Expected slides: {state.get('expected_pages', 10)}\n"
        f"Aspect ratio: {state.get('aspect_ratio', '16:9')}\n"
        f"Density preference: {state.get('density_preference', 'balanced')}\n"
        f"Language hint: {state.get('language', 'en')}\n"
        f"Visual preference hint: {state.get('visual_style_preference') or '(none)'}"
    )


def conversation_messages(
    state: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    include_materials: bool,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = [{"role": "system", "content": system_prompt(state)}]
    out.append({"role": "user", "content": catalog_context()})
    if include_materials:
        out.append({"role": "user", "content": f"MATERIAL DIGEST:\n{digest_blob(state)}"})
    current_draft = state.get("advanced_chat_draft") or {}
    if current_draft:
        out.append({
            "role": "user",
            "content": (
                "CURRENT STRUCTURED DRAFT JSON:\n"
                f"{json.dumps(current_draft, ensure_ascii=False)[:12000]}"
            ),
        })
    for message in messages:
        role = "assistant" if message.get("role") == "assistant" else "user"
        out.append({"role": role, "content": str(message.get("content") or "")})
    return out


def draft_extraction_messages(
    state: dict[str, Any],
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Extract the current best complete slide-plan draft from the advanced "
                "planning conversation. Use only valid catalog ids. If the user has not "
                "specified a detail, choose the strongest reasonable default from the "
                "material and conversation. Return a complete draft; do not leave fields "
                "empty."
            ),
        },
        *conversation_messages(state, messages, include_materials=True),
        {
            "role": "user",
            "content": (
                "Now extract the current best complete draft as JSON. This is an "
                "extraction step, not a chat reply. Return only the JSON object."
            ),
        },
    ]


def _outline_markdown(draft: AdvancedChatDraft) -> str:
    structure = STRUCTURE_DEFINITIONS.get(draft.structure_id, {})
    scenario = next(
        (s for s in SCENARIO_DEFINITIONS if s["id"] == draft.scenario_id),
        {"name_en": draft.scenario_id},
    )
    md_lines = [
        f"# Outline ({structure.get('name_en', draft.structure_id)} / {scenario['name_en']})",
        f"Language: {draft.language}",
        "",
    ]
    if draft.summary:
        md_lines.extend([draft.summary, ""])
    for i, slide in enumerate(draft.outline_slides, 1):
        md_lines.append(f"## {i}. {slide.title}")
        md_lines.append(f"_role: {slide.role}_")
        for bullet in slide.bullets:
            md_lines.append(f"- {bullet}")
        if slide.image_slots:
            md_lines.append("")
            md_lines.append("_image_slots:_")
            for slot in slide.image_slots:
                md_lines.append(f"- {slot}")
        if slide.speaker_notes:
            md_lines.append("")
            md_lines.append(f"> {slide.speaker_notes}")
        md_lines.append("")
    return "\n".join(md_lines)


def _style_markdown(style: AdvancedVisualStyle) -> str:
    pal = style.palette
    md_lines = [
        "# Visual Style",
        f"Tone: **{style.tone}**  |  Density: **{style.density}**",
        "",
        "## Palette",
        f"- primary: `{pal.primary}`",
        f"- secondary: `{pal.secondary}`",
        f"- accent: `{pal.accent}`",
        f"- neutral_dark: `{pal.neutral_dark}`",
        f"- neutral_light: `{pal.neutral_light}`",
        f"- background: `{pal.background}`",
        *[f"- {k}: `{v}`" for k, v in (pal.roles or {}).items()],
        "",
        "## Typography",
        f"- heading: {style.typography.heading_family}",
        f"- body: {style.typography.body_family}",
        f"- display: {style.typography.display_family or style.typography.heading_family}",
        f"_rationale: {style.typography.rationale}_",
        "",
        "## Imagery Policy",
        style.imagery_policy,
        "",
        "## Motion",
        style.motion_policy,
        "",
        "## Rationale",
        style.rationale,
    ]
    return "\n".join(md_lines)


def advanced_draft_payload(draft: AdvancedChatDraft | dict[str, Any]) -> dict[str, Any]:
    parsed = coerce_advanced_chat_draft(draft)
    outline_slides = [slide.model_dump() for slide in parsed.outline_slides]
    visual_style = parsed.visual_style.model_dump()
    return {
        "scenario_id": parsed.scenario_id,
        "structure_id": parsed.structure_id,
        "language": parsed.language,
        "summary": parsed.summary,
        "outline_md": _outline_markdown(parsed),
        "outline_slides": outline_slides,
        "visual_style_md": _style_markdown(parsed.visual_style),
        "visual_style": visual_style,
    }


def coerce_advanced_chat_draft(draft: AdvancedChatDraft | dict[str, Any]) -> AdvancedChatDraft:
    if isinstance(draft, AdvancedChatDraft):
        parsed = draft
    else:
        try:
            parsed = AdvancedChatDraft.model_validate(draft)
        except ValidationError as exc:
            raise ValueError(f"Advanced chat draft is incomplete: {exc}") from exc
    _validate_catalog_ids(parsed)
    return parsed


def _validate_catalog_ids(draft: AdvancedChatDraft) -> None:
    scenario_ids = {s["id"] for s in SCENARIO_DEFINITIONS}
    if draft.scenario_id not in scenario_ids:
        raise ValueError(f"Unknown scenario_id in advanced draft: {draft.scenario_id}")
    structure = STRUCTURE_DEFINITIONS.get(draft.structure_id)
    if not structure or structure.get("legacy"):
        raise ValueError(f"Unknown structure_id in advanced draft: {draft.structure_id}")


def commit_advanced_chat_draft(draft: AdvancedChatDraft | dict[str, Any]) -> dict[str, Any]:
    payload = advanced_draft_payload(draft)
    return {
        "scenario_id": payload["scenario_id"],
        "structure_id": payload["structure_id"],
        "outline_md": payload["outline_md"],
        "outline_slides": payload["outline_slides"],
        "language": payload["language"],
        "languages": [payload["language"]],
        "visual_style_md": payload["visual_style_md"],
        "visual_style": payload["visual_style"],
        "advanced_chat_draft": payload,
        "current_stage": "layout",
    }
