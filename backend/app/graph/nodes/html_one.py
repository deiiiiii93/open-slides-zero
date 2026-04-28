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
from ...llm.models import get_model
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
    visual_guidance = (
        "\nVisual preference guidance:\n" + "\n".join(guidance_lines) + "\n"
        if guidance_lines else ""
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
- Use inline <style> — no external stylesheet or JS frameworks.
- Use the `{pattern}` layout pattern with zones: {zones}.

{ANTI_SLOP_RULES}

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
    push_event({"node": "html_one", "slide_idx": slide_idx, "state": "started"})
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
                    get_model("html"),
                    messages,
                    temperature=0.4,
                    max_tokens=_html_max_tokens(),
                    timeout=_html_timeout_seconds(),
                    stream=True,
                )
        except Exception as exc:
            last_reason = str(exc)
            last_finish_reason = None
            last_errors = [f"html_one slide {slide_idx}: {exc}"]
            push_event({"node": "html_one", "slide_idx": slide_idx, "state": "error", "error": str(exc)})
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
            push_event({"node": "html_one", "slide_idx": slide_idx, "state": "finished"})
            return {"html_slides": {slide_idx: html}}

        previous_html = html
        last_finish_reason = result.finish_reason
        last_errors = validation_errors
        last_reason = validation_errors[0] if validation_errors else (
            f"slide {slide_idx}: html validation failed"
        )
        if attempt >= _html_max_attempts():
            break

    push_event({"node": "html_one", "slide_idx": slide_idx, "state": "error", "error": last_reason})
    return _failure_update(
        slide_idx=slide_idx,
        attempt_count=_html_max_attempts(),
        reason=last_reason,
        finish_reason=last_finish_reason,
        errors=last_errors or [last_reason],
    )
