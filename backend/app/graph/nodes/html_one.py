"""Stage I — single-slide HTML composer.

Invoked via LangGraph `Send()` once per slide so that a failure on one slide
retries only that slide. Writes into `html_slides[slide_idx]` which is merged by
the `_merge_html` reducer in state.py.

The system prompt encodes the anti-slop rules verbatim.
"""

from __future__ import annotations

import os
from typing import Any

from ...catalog import layouts as L
from ...catalog.validator import validate_slide_html
from ...llm import zenmux
from ...llm.models import get_lane_model, html_overlay_for_preset
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
Claude-quality composition discipline:
- Choose 2-4 named zones from the requested pattern before writing HTML. Every
  visible element must belong to one zone.
- Convert bullets into designed evidence: numbered keys, proof rows, micro-bars,
  split columns, quote blocks, or marked steps. Use a plain bullet list only when
  it is the clearest structure.
- Add at most one content-earned CSS-only motif when useful: thin rules, bands,
  rings, simple bars, coordinate lines, counters, or typographic anchors. The
  motif must explain the slide idea.
- Use at most two font-family roles per slide: one display/heading face and one
  body/label face, derived from the supplied typography when possible. Do not use
  external font imports or font <link> tags.
- For a 960x540 slide, use a role-based type scale: cover/display 34-48px,
  normal titles 28-40px, section headings 18-26px, body/proof 11.5-15px for
  dense slides or 13-17px for balanced slides, and labels/meta 9-11px.
- Only one decorative numeral or glyph may exceed 64px. Body/proof text must
  stay at or below 20px.
- Prefer font weights 400, 600, and 700, optionally 300. Avoid arbitrary weights
  like 350, 450, 550, or 650.
- Use title line-height 1.1-1.35, body/proof line-height 1.45-1.7, and
  label/meta line-height 1-1.25. Reserve letter-spacing 0.08em-0.18em for
  labels and kickers, not body text.
- Compose for the exact root canvas only. Do not add @media rules, print styles,
  responsive fallback CSS, hover states, transitions, animations, or defensive
  fit-guard sections.
- Prefer editorial restraint: one background field, one text color family, one
  accent family, flat fills, hairline rules, measured whitespace, square or
  near-square edges, and near-zero shadows. Use circular radius only for dots,
  rings, or markers.
- Avoid decorative blobs, generic card piles, hover states, animations, SVG,
  external CSS imports, and external font imports.
- Use semantic, slide-specific class names and short CSS comments for major
  regions. Keep the DOM shallow and purposeful.
- Make density intentional: title hierarchy first, proof second, captions or
  footers last. Fit all text at final pixel size with no truncation, scrolling,
  or hidden overflow.
"""

_TRUNCATION_REASONS = {"length", "max_tokens"}


def _canvas_css(aspect_ratio: str) -> tuple[int, int]:
    return L.CANVAS_SIZES.get(aspect_ratio, L.CANVAS_SIZES["16:9"])


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

{ANTI_SLOP_RULES}

{COMPOSITION_DISCIPLINE}

Design system to apply:
- Palette: {palette}
- Typography: {typography}
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

Before returning the HTML, silently review the slide against the full prompt:
exact canvas size, no overflow/truncation, clear 2-4 zone structure, role-based
type scale, restrained colors/chrome, no external imports, no hover states, no
animations, no responsive fallback CSS, and no unearned motif.
If any check fails, revise the HTML before returning.

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


def _html_max_attempts() -> int:
    return max(1, int(os.getenv("OSZ_HTML_MAX_ATTEMPTS", "2")))


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


def _failure_update(
    *,
    slide_idx: int,
    attempt_count: int,
    reason: str,
    finish_reason: str | None = None,
    errors: list[str] | None = None,
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
    return update


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
    overlay_temperature = overlay.get("temperature")
    temperature = float(overlay_temperature) if isinstance(overlay_temperature, (int, float)) else 0.4
    push_event({
        "node": "html_one",
        "slide_idx": slide_idx,
        "state": "started",
        "model": model,
        "preset_id": preset_id,
        "temperature": temperature,
    })
    previous_html = ""
    last_reason = f"slide {slide_idx}: unknown html generation failure"
    last_finish_reason: str | None = None
    last_errors: list[str] = []

    for attempt in range(1, _html_max_attempts() + 1):
        messages = list(base_messages)
        if attempt > 1:
            messages.extend([
                {"role": "assistant", "content": previous_html},
                {
                    "role": "user",
                    "content": _retry_prompt(last_finish_reason, last_errors),
                },
            ])
        try:
            with tagged_stream(tag):
                result = zenmux.chat_with_metadata(
                    model,
                    messages,
                    temperature=temperature,
                    max_tokens=_html_max_tokens(),
                    timeout=_html_timeout_seconds(),
                    stream=True,
                )
        except Exception as exc:
            last_reason = str(exc)
            last_finish_reason = None
            last_errors = [f"html_one slide {slide_idx}: {exc}"]
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "error",
                "error": str(exc),
                "model": model,
            })
            return _failure_update(
                slide_idx=slide_idx,
                attempt_count=attempt,
                reason=last_reason,
                finish_reason=last_finish_reason,
                errors=last_errors,
            )

        html = _strip_code_fences(result.text)
        validation = validate_slide_html(
            html,
            aspect_ratio=brief["aspect_ratio"],
            density=brief["density"],
        )
        validation_errors = _validation_errors(validation, slide_idx)
        if result.finish_reason in _TRUNCATION_REASONS:
            validation_errors.append(
                f"slide {slide_idx}: finish_reason — response ended with {result.finish_reason}"
            )

        if not validation_errors:
            push_event({
                "node": "html_one",
                "slide_idx": slide_idx,
                "state": "finished",
                "model": model,
            })
            return {"html_slides": {slide_idx: html}}

        previous_html = html
        last_finish_reason = result.finish_reason
        last_errors = validation_errors
        last_reason = validation_errors[0] if validation_errors else (
            f"slide {slide_idx}: html validation failed"
        )
        if attempt >= _html_max_attempts():
            break

    push_event({
        "node": "html_one",
        "slide_idx": slide_idx,
        "state": "error",
        "error": last_reason,
        "model": model,
    })
    return _failure_update(
        slide_idx=slide_idx,
        attempt_count=_html_max_attempts(),
        reason=last_reason,
        finish_reason=last_finish_reason,
        errors=last_errors or [last_reason],
    )
