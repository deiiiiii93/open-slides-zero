"""Stage I — single-slide HTML composer.

Invoked via LangGraph `Send()` once per slide so that a failure on one slide
retries only that slide. Writes into `html_slides[slide_idx]` which is merged by
the `_merge_html` reducer in state.py.

The system prompt encodes the anti-slop rules verbatim.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...catalog import layouts as L
from ...catalog.validator import validate_slide_html
from ...llm import zenmux
from ...llm.models import (
    get_lane_model,
    get_lane_thinking_effort,
    get_model,
    html_overlay_for_preset,
)
from ...llm.stream import push_event, tagged_stream


ANTI_SLOP_RULES = """\
Avoid AI-slop tropes (these are non-negotiable):
- NO aggressive gradient backgrounds.
- NO emoji unless explicitly part of the brand.
- NO containers using rounded corners with a left-border accent color.
- NO imagery drawn inline via SVG. For empty image positions, use explicit
  image-slot divs, not empty/broken <img> tags.
- NO overused font families (Inter, Roboto, Arial, Fraunces, system-ui, -apple-system).
- Do not add filler content. Never pad a design with placeholder text, dummy
  sections, or informational material just to fill space. Every element should
  earn its place. The only permitted placeholder copy is the required image-slot
  affordance ("Add image here" plus a short "Suggested: ..." hint).
- One thousand no's for every yes.
- DO use `text-wrap: pretty`, CSS grid, modern advanced CSS (subgrid, container
  queries, :has()) where they actually help readability.
- Commit to a single bold aesthetic direction — do not hedge.
"""

COMPOSITION_DISCIPLINE = """\
Highend-quality composition discipline:
- Choose 2-4 named zones from the requested pattern before writing HTML. Every
  visible element must belong to one zone.
- Convert bullets into designed evidence: proof rows, micro-bars,
  split columns, quote blocks, or marked steps. Use a plain bullet list only when
  it is the clearest structure. These are zone contents, not motifs.
- Avoid use boring Arabic numerals (such as circled numbers, plain numbers) to express numbered keys.
- Add at most one content-earned CSS-only motif when useful: thin rules, bands,
  rings, simple bars, coordinate lines, counters, or typographic anchors. A motif
  is a small decorative anchor, not a structural layout pattern. Proof rows,
  split columns, and quote blocks do not count toward the motif limit.
- Use at most three font-family roles per slide.
- Use Exactly the font families from typography in context. Do not use font family other than the brief gives.
- For a 960x540 slide, use a role-based type scale: cover/display 34-48px,
  normal titles 28-40px, section headings 18-26px, body/proof 11.5-15px for
  dense slides or 13-17px for balanced slides, and labels/meta 9-11px.
- Only one decorative numeral or glyph may exceed 64px (and never above ~120px
  for a 540px-tall canvas). Body/proof text must stay at or below 20px.
- Prefer font weights 400, 600, and 700, optionally 300. Avoid arbitrary weights
  like 350, 450, 550, or 650.
- Reserve proper line spacing to keep visual comfortable.
- Compose for the exact root canvas only. Do not add @media rules, print styles,
  responsive fallback CSS, hover states, transitions, animations, or defensive
  fit-guard sections.
- Prefer editorial restraint: one background field, one text color family, one
  accent family, flat fills, hairline rules, measured whitespace, square or
  near-square edges, and near-zero shadows. Use circular radius only for dots,
  rings, or markers.
- Avoid decorative blobs, generic card piles, hover states, animations, SVG,
  external CSS imports.
- Use semantic, slide-specific class names and short CSS comments for major
  regions. Keep the DOM shallow and purposeful.
- Make density intentional: title hierarchy first, proof second, captions or
  footers last. Fit all text at final pixel size with no truncation, scrolling,
  or hidden overflow.

Before returning the HTML, silently review the slide against this prompt:
exact canvas size, no overflow/truncation, clear 2-4 zone structure,
role-based type scale, restrained colors/chrome, no external imports,
no hover states, no animations, no responsive fallback CSS, and no unearned
motif. If any check fails, revise the HTML before returning.

Before returning cover/closing HTML, verify:
- It is not merely centered title + bullets.
- It has explicit named zones in CSS/DOM comments or class names.
- It has one, and only one, earned decorative motif.
- Bullets have been transformed into proof rows, quote/callout, evidence strip,
  CTA, or footer metadata.
- The final slide contains a conclusion or action, not only a recap.
"""

ROLE_SPECIFIC_COMPOSITION_GUIDANCE = """\
Role-specific composition guidance:

If Role is "cover":
- Treat the slide as a designed opening moment, not a normal content slide.
- Use 3-4 named zones: eyebrow/meta, display title, proof/support, and one
  visual/footer zone.
- Convert bullets into short proof rows, subtitle fragments, or a compact
  2-column evidence strip. Do not leave them as a plain list unless the pattern
  explicitly demands it.
- Add exactly one content-earned visual motif tied to the deck topic or style:
  thin rules, vertical accent band, rings, corner marks, watermark glyph/word,
  large faint character, image panel, or brand/date lockup.
- Add small editorial metadata such as theme, chapter, source, date, or keynote
  label when it reinforces the cover.
- Highlight 1-2 key words in the title with the accent color or display font
  treatment.

If Role is "closing" or "close":
- Treat the slide as synthesis plus next action, not just another title slide.
- Use 3-4 named zones: closing label, final thesis, recap/proof, and
  CTA/contact/footer.
- Reuse the deck's established motif from the cover, but simplify it.
- Convert bullets into recap proof rows, decision columns, quote block, or
  contact/action cards.
- Include one clear closing action, quote, or final line. Make it visually
  distinct but restrained.
- The closing slide should feel resolved: strong title hierarchy, fewer
  elements than a dense content slide, and a footer/contact/meta treatment when
  appropriate.
"""

_TRUNCATION_REASONS = {"length", "max_tokens"}
_MAX_METADATA_ISSUES = 5
_MAX_METADATA_ISSUE_CHARS = 300


class _HtmlCritique(BaseModel):
    decision: Literal["accept", "revise"]
    issues: list[str] = Field(default_factory=list)
    revision_instructions: str = ""


def _canvas_css(aspect_ratio: str) -> tuple[int, int]:
    return L.CANVAS_SIZES.get(aspect_ratio, L.CANVAS_SIZES["16:9"])


def _typography_role_contract(typography: dict[str, Any]) -> str:
    heading = typography.get("heading_family") or typography.get("heading")
    body = typography.get("body_family") or typography.get("body")
    display = typography.get("display_family") or typography.get("display")

    lines = [
        "Typography role contract:",
    ]
    if body:
        lines.append(
            f"- body_family: `{body}` — use this exact CSS stack for the root/default "
            "font and for paragraphs, proof text, captions, labels, and metadata."
        )
    if heading:
        lines.append(
            f"- heading_family: `{heading}` — use this exact CSS stack for normal "
            "slide titles, section headings, and structural labels."
        )
    if display and display != heading:
        lines.append(
            f"- display_family: `{display}` — this role is not optional when present. "
            "Use this exact CSS stack at least once on the slide: for the primary h1 "
            "on cover, opener, section, closing, or impact slides, or for exactly one "
            "content-earned typographic anchor/glyph on dense evidence slides."
        )
        lines.append(
            "- Do not silently replace display_family with heading_family because it "
            "feels safer; if the slide is too dense for a display title, reserve "
            "display_family for one controlled anchor only."
        )
    elif display:
        lines.append(
            f"- display_family: `{display}` — same as heading_family; use it for "
            "display-scale moments without adding another font role."
        )
    else:
        lines.append(
            "- display_family: not provided; use heading_family for display-scale moments."
        )
    return "\n".join(lines)


def _slide_prompt(
    brief_slide: dict[str, Any],
    brief: dict[str, Any],
    feedback: str | None = None,
    creator_prompt: str | None = None,
) -> list[dict[str, Any]]:
    width, height = _canvas_css(brief["aspect_ratio"])
    style = brief["style"]
    palette = style.get("palette", {})
    typography = style.get("typography", {})
    typography_contract = _typography_role_contract(typography)
    pattern = brief_slide["pattern"]
    zones = brief_slide["zones"]
    guidance_lines: list[str] = []
    if brief.get("visual_style_preference"):
        guidance_lines.append(f"- Free-text style hint: {brief['visual_style_preference']}")
    if brief.get("visual_style_preset_label") and brief.get("visual_style_preset_prompt"):
        guidance_lines.append(
            f"- Selected preset ({brief['visual_style_preset_label']}): "
            f"{brief['visual_style_preset_prompt']}"
        )
    html_rules = brief.get("visual_style_preset_html_rules") or []
    if brief.get("visual_style_preset_label") and html_rules:
        guidance_lines.append("- Direction-specific HTML rules:")
        guidance_lines.extend(f"  - {rule}" for rule in html_rules)
    visual_guidance = (
        "\nVisual preference guidance:\n" + "\n".join(guidance_lines) + "\n"
        if guidance_lines else ""
    )
    image_slots = [
        str(slot).strip()
        for slot in (brief_slide.get("image_slots") or [])
        if str(slot).strip()
    ]
    image_slot_system_guidance = (
        "- When Image slot guidance is provided, render exactly one visible "
        "data-image-placeholder slot per requested image slot. Use the slot text as "
        "the data-prompt-hint and Suggested text.\n"
        if image_slots else ""
    )
    image_slot_user_guidance = (
        "\nImage slot guidance (render as visible image placeholders):\n"
        + "\n".join(f"- {slot}" for slot in image_slots)
        if image_slots else "\nImage slot guidance: (none)"
    )

    system = f"""\
You are an HTML/CSS slide composer. Output ONE complete self-contained HTML
document for a single slide. Hard constraints:

- Root <div> must be EXACTLY {width}px wide and {height}px tall,
  with overflow:hidden, box-sizing:border-box, position:relative.
- No pagination text ("Slide X of Y") or pagination dots.
- No overflow:auto or overflow:scroll anywhere.
- For image positions without a real image source, DO NOT emit <img>. Emit a
  visible image slot:
  <div data-image-placeholder="true" data-prompt-hint="..." style="width:...px;height:...px;...">
    <span>Add image here</span>
    <span>Suggested: concise image description</span>
  </div>
  The placeholder div itself must include inline width and height, centered text,
  subtle fill, dashed/bordered treatment, and box-sizing:border-box.
- Use <img> only for real, non-empty src values; real images must have explicit
  width/height attributes or inline width/height styles, and object-fit:cover.
- Image-slot placeholders are allowed and expected when the slide asks for imagery.
{image_slot_system_guidance}- Do not render image-slot guidance as ordinary body copy; it is only for
  placeholder prompt hints and captions.
- Use inline <style> — no external stylesheet or JS frameworks.
- Use the `{pattern}` layout pattern with zones: {zones}.

{ROLE_SPECIFIC_COMPOSITION_GUIDANCE}

{ANTI_SLOP_RULES}

{COMPOSITION_DISCIPLINE}

Design system to apply:
- Palette: {palette}
- Typography: {typography}
{typography_contract}
- Density: {brief['density']}
- Tone: {style.get('tone', 'editorial')}
{visual_guidance}

Language of text: {brief['language']}.
"""
    feedback_block = (
        f"\n\nUSER FEEDBACK (apply to this slide): {feedback}\n"
        if feedback else ""
    )
    user = f"""Slide content to render:

Title: {brief_slide['title']}
Role: {brief_slide['role']}
Bullets: {brief_slide['bullets']}
Speaker notes (do NOT render verbatim, only for your reasoning): {brief_slide.get('speaker_notes', '')}
{image_slot_user_guidance}
ASCII layout wireframe for reference:
{brief_slide.get('wireframe', '')}{feedback_block}

Return ONLY the complete HTML document. Begin with <!DOCTYPE html>.
"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if creator_prompt:
        messages.append({
            "role": "user",
            "content": (
                "CREATOR PLAYGROUND LANE EXTRA INSTRUCTIONS:\n"
                f"{creator_prompt.strip()}\n\n"
                "Treat these as direct user instructions for this lane."
            ),
        })
    return messages


def _html_max_tokens() -> int:
    return max(1, int(os.getenv("OSZ_HTML_MAX_TOKENS", "12000")))


def _html_timeout_seconds() -> float:
    return max(1.0, float(os.getenv("OSZ_HTML_TIMEOUT_SECONDS", "180")))


def _positive_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(1, value)


def _html_react_max_cycles() -> int:
    return (
        _positive_int_env("OSZ_HTML_REACT_MAX_CYCLES")
        or _positive_int_env("OSZ_HTML_MAX_ATTEMPTS")
        or 3
    )


def _strip_code_fences(html: str) -> str:
    html = html.strip()
    if html.startswith("```"):
        first_nl = html.find("\n")
        if first_nl != -1:
            html = html[first_nl + 1:]
        if html.endswith("```"):
            html = html[:-3]
    return html.strip()


def _validation_errors(
    validation: Any,
    slide_idx: int,
) -> list[str]:
    return [
        f"slide {slide_idx}: {issue.rule} — {issue.message}"
        for issue in validation.errors
    ]


def _validation_warnings(
    validation: Any,
    slide_idx: int,
) -> list[str]:
    return [
        f"slide {slide_idx}: {issue.rule} — {issue.message}"
        for issue in validation.warnings
    ]


def _cap_metadata_text(value: Any) -> str:
    text = str(value).strip()
    if len(text) <= _MAX_METADATA_ISSUE_CHARS:
        return text
    return text[: _MAX_METADATA_ISSUE_CHARS - 1].rstrip() + "…"


def _cap_metadata_issues(issues: list[Any]) -> list[str]:
    return [
        _cap_metadata_text(issue)
        for issue in issues[:_MAX_METADATA_ISSUES]
        if str(issue).strip()
    ]


def _cycle_metadata(
    *,
    cycle: int,
    composer_finish_reason: str | None,
    validation_error_count: int = 0,
    validation_warning_count: int = 0,
    critic_decision: str | None = None,
    critic_issues: list[Any] | None = None,
    stop_reason: str,
) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "composer_finish_reason": composer_finish_reason,
        "validation_error_count": validation_error_count,
        "validation_warning_count": validation_warning_count,
        "critic_decision": critic_decision,
        "critic_issues": _cap_metadata_issues(critic_issues or []),
        "stop_reason": stop_reason,
    }


def _html_generation_metadata(
    *,
    slide_idx: int,
    status: Literal["succeeded", "failed"],
    model: str | None = None,
    critic_model: str | None = None,
    reasoning_effort: str | None = None,
    preset_id: str | None = None,
    temperature: float | None = None,
    max_cycles: int = 0,
    cycles: list[dict[str, Any]] | None = None,
    accepted_by: str | None = None,
    final_finish_reason: str | None = None,
    final_error: str | None = None,
) -> dict[str, Any]:
    cycle_records = list(cycles or [])
    return {
        "slide_idx": slide_idx,
        "status": status,
        "model": model,
        "critic_model": critic_model,
        "reasoning_effort": reasoning_effort,
        "preset_id": preset_id,
        "temperature": temperature,
        "max_cycles": max_cycles,
        "cycles_run": len(cycle_records),
        "accepted_by": accepted_by,
        "final_finish_reason": final_finish_reason,
        "final_error": _cap_metadata_text(final_error) if final_error else None,
        "cycles": cycle_records,
    }


def _with_html_metadata(
    slide_idx: int,
    metadata: dict[str, Any],
    update: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(update or {})
    out["html_generation_metadata"] = {slide_idx: metadata}
    return out


def _retry_prompt(
    finish_reason: str | None,
    validation_errors: list[str],
) -> str:
    reasons: list[str] = []
    if finish_reason:
        reasons.append(f"finish_reason={finish_reason}")
    if validation_errors:
        reasons.extend(validation_errors)
    detail = "\n".join(f"- {reason}" for reason in reasons) if reasons else "- output was incomplete"
    return (
        "Your previous HTML was incomplete or invalid. Re-render the entire slide as one "
        "fully closed HTML document, including a complete <style> block and closing "
        "</body></html> tags. Return the full document only.\n"
        f"Issues to fix:\n{detail}"
    )


def _critique_prompt(critique: _HtmlCritique) -> str:
    issues = [str(issue).strip() for issue in critique.issues if str(issue).strip()]
    instructions = critique.revision_instructions.strip()
    detail_lines = [f"- {issue}" for issue in issues]
    if instructions:
        detail_lines.append(f"- Revision instructions: {instructions}")
    detail = "\n".join(detail_lines) if detail_lines else "- Make only concrete quality fixes."
    return (
        "Your previous HTML passed hard validation, but the critic requested a "
        "quality revision. Re-render the entire slide as one fully closed HTML "
        "document. Preserve all factual content, keep the requested layout pattern, "
        "and fix only the concrete issues below. Return the full document only.\n"
        f"Critic observations:\n{detail}"
    )


def _critic_messages(
    *,
    brief_slide: dict[str, Any],
    brief: dict[str, Any],
    html: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    warning_text = "\n".join(f"- {warning}" for warning in warnings) if warnings else "- None"
    style = brief.get("style") or {}
    typography = style.get("typography") or {}
    typography_contract = _typography_role_contract(typography)
    system = f"""\
You are an exacting critic for generated HTML/CSS slide quality.
Review the slide and decide whether it should be accepted or revised.

Use decision="revise" only for concrete issues that materially improve the slide:
- visible overflow, crowding, weak hierarchy, awkward zone use, unreadable type,
  generic card/chrome treatment, unearned decoration, validator warnings, or
  violations of the anti-slop/composition rules.
- Treat the typography role contract as binding. If display_family is present
  and distinct from heading_family but unused, request a concrete revision.
- Do not request changes for subjective taste alone.
- Do not rewrite or return HTML. Return structured critique only.

{ANTI_SLOP_RULES}

{COMPOSITION_DISCIPLINE}
"""
    user = f"""\
Slide metadata:
- Title: {brief_slide.get('title')}
- Role: {brief_slide.get('role')}
- Pattern: {brief_slide.get('pattern')}
- Zones: {brief_slide.get('zones')}
- Bullets: {brief_slide.get('bullets')}
- Density: {brief.get('density')}
- Tone: {style.get('tone', 'editorial')}
- Typography: {typography}

{typography_contract}

Validator warnings:
{warning_text}

Generated HTML to review:
```html
{html}
```
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _failure_update(
    *,
    slide_idx: int,
    attempt_count: int,
    reason: str,
    finish_reason: str | None = None,
    errors: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = {
        "html_failures": [{
            "slide_idx": slide_idx,
            "attempt_count": attempt_count,
            "reason": reason,
            "finish_reason": finish_reason,
        }]
    }
    if errors:
        update["errors"] = errors
    return _with_html_metadata(
        slide_idx,
        metadata
        or _html_generation_metadata(
            slide_idx=slide_idx,
            status="failed",
            max_cycles=attempt_count,
            final_finish_reason=finish_reason,
            final_error=reason,
        ),
        update,
    )


def html_one_node(state: dict[str, Any]) -> dict[str, Any]:
    """Invoked via Send({'slide_idx': i, 'brief': brief}).

    LangGraph passes the Send payload as the state for this invocation.
    """
    brief = state.get("brief") or state.get("_brief")
    slide_idx = int(state.get("slide_idx", -1))
    if not brief:
        reason = f"html_one: missing brief for slide {slide_idx}"
        return _failure_update(
            slide_idx=slide_idx,
            attempt_count=1,
            reason=reason,
            errors=[reason],
        )

    slides = brief["slides"]
    slide = next((s for s in slides if s["slide_idx"] == slide_idx), None)
    if slide is None:
        reason = f"html_one: slide index {slide_idx} not in brief"
        return _failure_update(
            slide_idx=slide_idx,
            attempt_count=1,
            reason=reason,
            errors=[reason],
        )

    feedback = state.get("feedback")
    creator_prompt = brief.get("creator_prompt") or state.get("creator_prompt")
    base_messages = _slide_prompt(slide, brief, feedback=feedback, creator_prompt=creator_prompt)
    tag = f"html:{slide_idx}"
    preset_id = brief.get("visual_style_preset_id")
    overlay = html_overlay_for_preset(preset_id)
    overlay_model = overlay.get("model")
    fallback_model = overlay_model if isinstance(overlay_model, str) and overlay_model else None
    model = get_lane_model(brief, "html", "html", fallback_model=fallback_model)
    critic_model = get_model("html.critic")
    reasoning_effort = get_lane_thinking_effort(brief, "html")
    overlay_temperature = overlay.get("temperature")
    temperature = float(overlay_temperature) if isinstance(overlay_temperature, (int, float)) else 0.4
    max_cycles = _html_react_max_cycles()
    event: dict[str, Any] = {
        "node": "html_one",
        "slide_idx": slide_idx,
        "state": "started",
        "model": model,
        "critic_model": critic_model,
        "preset_id": preset_id,
        "temperature": temperature,
        "max_cycles": max_cycles,
    }
    if reasoning_effort:
        event["reasoning_effort"] = reasoning_effort
    push_event(event)
    previous_html = ""
    last_valid_html: str | None = None
    last_critique: _HtmlCritique | None = None
    last_reason = f"slide {slide_idx}: unknown html generation failure"
    last_finish_reason: str | None = None
    last_errors: list[str] = []
    cycles_metadata: list[dict[str, Any]] = []

    for cycle in range(1, max_cycles + 1):
        push_event({
            "node": "html_one",
            "slide_idx": slide_idx,
            "state": "cycle_started",
            "cycle": cycle,
            "max_cycles": max_cycles,
            "model": model,
        })
        messages = list(base_messages)
        if cycle > 1:
            revision_prompt = (
                _critique_prompt(last_critique)
                if last_critique is not None
                else _retry_prompt(last_finish_reason, last_errors)
            )
            messages.extend([
                {"role": "assistant", "content": previous_html},
                {"role": "user", "content": revision_prompt},
            ])
        try:
            with tagged_stream(tag):
                result = zenmux.chat_with_metadata(
                    model,
                    messages,
                    temperature=temperature,
                    max_tokens=_html_max_tokens(),
                    timeout=_html_timeout_seconds(),
                    reasoning_effort=reasoning_effort,
                    stream=True,
                )
        except Exception as exc:
            last_reason = str(exc)
            last_finish_reason = None
            last_errors = [f"html_one slide {slide_idx}: {exc}"]
            cycles_metadata.append(
                _cycle_metadata(
                    cycle=cycle,
                    composer_finish_reason=None,
                    critic_issues=[str(exc)],
                    stop_reason="composer_error",
                )
            )
            if last_valid_html is not None:
                push_event({
                    "node": "html_one",
                    "slide_idx": slide_idx,
                    "state": "finished",
                    "model": model,
                    "cycle": cycle,
                    "accepted_by": "last_valid_after_composer_error",
                    "error": str(exc),
                })
                metadata = _html_generation_metadata(
                    slide_idx=slide_idx,
                    status="succeeded",
                    model=model,
                    critic_model=critic_model,
                    reasoning_effort=reasoning_effort,
                    preset_id=preset_id,
                    temperature=temperature,
                    max_cycles=max_cycles,
                    cycles=cycles_metadata,
                    accepted_by="last_valid_after_composer_error",
                    final_error=str(exc),
                )
                return _with_html_metadata(
                    slide_idx,
                    metadata,
                    {"html_slides": {slide_idx: last_valid_html}},
                )
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "error",
                "error": str(exc),
                "model": model,
            })
            return _failure_update(
                slide_idx=slide_idx,
                attempt_count=cycle,
                reason=last_reason,
                finish_reason=last_finish_reason,
                errors=last_errors,
                metadata=_html_generation_metadata(
                    slide_idx=slide_idx,
                    status="failed",
                    model=model,
                    critic_model=critic_model,
                    reasoning_effort=reasoning_effort,
                    preset_id=preset_id,
                    temperature=temperature,
                    max_cycles=max_cycles,
                    cycles=cycles_metadata,
                    final_error=last_reason,
                ),
            )

        html = _strip_code_fences(result.text)
        validation = validate_slide_html(
            html,
            aspect_ratio=brief["aspect_ratio"],
            density=brief["density"],
        )
        validation_errors = _validation_errors(validation, slide_idx)
        warnings = _validation_warnings(validation, slide_idx)
        if result.finish_reason in _TRUNCATION_REASONS:
            validation_errors.append(
                f"slide {slide_idx}: finish_reason — response ended with {result.finish_reason}"
            )

        if validation_errors:
            cycles_metadata.append(
                _cycle_metadata(
                    cycle=cycle,
                    composer_finish_reason=result.finish_reason,
                    validation_error_count=len(validation_errors),
                    validation_warning_count=len(warnings),
                    stop_reason=(
                        "truncated"
                        if result.finish_reason in _TRUNCATION_REASONS
                        else "validation_failed"
                    ),
                )
            )
            previous_html = html
            last_critique = None
            last_finish_reason = result.finish_reason
            last_errors = validation_errors
            last_reason = validation_errors[0] if validation_errors else (
                f"slide {slide_idx}: html validation failed"
            )
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "cycle_invalid",
                "cycle": cycle,
                "error": last_reason,
                "model": model,
            })
            if cycle >= max_cycles:
                if last_valid_html is not None:
                    push_event({
                        "node": "html_one",
                        "slide_idx": slide_idx,
                        "state": "finished",
                        "model": model,
                        "cycle": cycle,
                        "accepted_by": "last_valid_after_invalid_revision",
                    })
                    metadata = _html_generation_metadata(
                        slide_idx=slide_idx,
                        status="succeeded",
                        model=model,
                        critic_model=critic_model,
                        reasoning_effort=reasoning_effort,
                        preset_id=preset_id,
                        temperature=temperature,
                        max_cycles=max_cycles,
                        cycles=cycles_metadata,
                        accepted_by="last_valid_after_invalid_revision",
                        final_finish_reason=result.finish_reason,
                        final_error=last_reason,
                    )
                    return _with_html_metadata(
                        slide_idx,
                        metadata,
                        {"html_slides": {slide_idx: last_valid_html}},
                    )
                break
            continue

        last_valid_html = html
        push_event({
            "node": "html_one",
            "slide_idx": slide_idx,
            "state": "critic_started",
            "cycle": cycle,
            "model": critic_model,
            "warning_count": len(warnings),
        })
        try:
            critique = zenmux.chat_structured(
                critic_model,
                _critic_messages(
                    brief_slide=slide,
                    brief=brief,
                    html=html,
                    warnings=warnings,
                ),
                _HtmlCritique,
                temperature=0.1,
                max_tokens=1400,
                timeout=_html_timeout_seconds(),
                max_attempts=2,
            )
        except Exception as exc:
            cycles_metadata.append(
                _cycle_metadata(
                    cycle=cycle,
                    composer_finish_reason=result.finish_reason,
                    validation_error_count=0,
                    validation_warning_count=len(warnings),
                    critic_decision="error",
                    critic_issues=[str(exc)],
                    stop_reason="critic_error",
                )
            )
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "critic_error",
                "cycle": cycle,
                "error": str(exc),
                "model": critic_model,
            })
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "finished",
                "model": model,
                "cycle": cycle,
                "accepted_by": "critic_error",
            })
            metadata = _html_generation_metadata(
                slide_idx=slide_idx,
                status="succeeded",
                model=model,
                critic_model=critic_model,
                reasoning_effort=reasoning_effort,
                preset_id=preset_id,
                temperature=temperature,
                max_cycles=max_cycles,
                cycles=cycles_metadata,
                accepted_by="critic_error",
                final_finish_reason=result.finish_reason,
                final_error=str(exc),
            )
            return _with_html_metadata(
                slide_idx,
                metadata,
                {"html_slides": {slide_idx: html}},
            )

        issues = [str(issue).strip() for issue in critique.issues if str(issue).strip()]
        revision_instructions = critique.revision_instructions.strip()
        if critique.decision == "accept" or not (issues or revision_instructions):
            cycles_metadata.append(
                _cycle_metadata(
                    cycle=cycle,
                    composer_finish_reason=result.finish_reason,
                    validation_error_count=0,
                    validation_warning_count=len(warnings),
                    critic_decision=critique.decision,
                    critic_issues=issues,
                    stop_reason="critic_accept",
                )
            )
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "critic_accept",
                "cycle": cycle,
                "model": critic_model,
            })
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "finished",
                "model": model,
                "cycle": cycle,
                "accepted_by": "critic",
            })
            metadata = _html_generation_metadata(
                slide_idx=slide_idx,
                status="succeeded",
                model=model,
                critic_model=critic_model,
                reasoning_effort=reasoning_effort,
                preset_id=preset_id,
                temperature=temperature,
                max_cycles=max_cycles,
                cycles=cycles_metadata,
                accepted_by="critic",
                final_finish_reason=result.finish_reason,
            )
            return _with_html_metadata(
                slide_idx,
                metadata,
                {"html_slides": {slide_idx: html}},
            )

        push_event({
            "node": "html_one",
            "slide_idx": slide_idx,
            "state": "critic_revise",
            "cycle": cycle,
            "model": critic_model,
            "issue_count": len(issues),
        })
        if cycle >= max_cycles:
            cycles_metadata.append(
                _cycle_metadata(
                    cycle=cycle,
                    composer_finish_reason=result.finish_reason,
                    validation_error_count=0,
                    validation_warning_count=len(warnings),
                    critic_decision=critique.decision,
                    critic_issues=issues,
                    stop_reason="max_cycles",
                )
            )
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "finished",
                "model": model,
                "cycle": cycle,
                "accepted_by": "max_cycles",
            })
            metadata = _html_generation_metadata(
                slide_idx=slide_idx,
                status="succeeded",
                model=model,
                critic_model=critic_model,
                reasoning_effort=reasoning_effort,
                preset_id=preset_id,
                temperature=temperature,
                max_cycles=max_cycles,
                cycles=cycles_metadata,
                accepted_by="max_cycles",
                final_finish_reason=result.finish_reason,
            )
            return _with_html_metadata(
                slide_idx,
                metadata,
                {"html_slides": {slide_idx: html}},
            )

        cycles_metadata.append(
            _cycle_metadata(
                cycle=cycle,
                composer_finish_reason=result.finish_reason,
                validation_error_count=0,
                validation_warning_count=len(warnings),
                critic_decision=critique.decision,
                critic_issues=issues,
                stop_reason="critic_revise",
            )
        )
        previous_html = html
        last_critique = critique
        last_finish_reason = None
        last_errors = []
        last_reason = issues[0] if issues else (
            revision_instructions or f"slide {slide_idx}: critic requested revision"
        )

    push_event({
        "node": "html_one",
        "slide_idx": slide_idx,
        "state": "error",
        "error": last_reason,
        "model": model,
    })
    return _failure_update(
        slide_idx=slide_idx,
        attempt_count=max_cycles,
        reason=last_reason,
        finish_reason=last_finish_reason,
        errors=last_errors or [last_reason],
        metadata=_html_generation_metadata(
            slide_idx=slide_idx,
            status="failed",
            model=model,
            critic_model=critic_model,
            reasoning_effort=reasoning_effort,
            preset_id=preset_id,
            temperature=temperature,
            max_cycles=max_cycles,
            cycles=cycles_metadata,
            final_finish_reason=last_finish_reason,
            final_error=last_reason,
        ),
    )
