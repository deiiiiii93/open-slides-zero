"""Post-generation HTML constraint validator (catalog §10).

Error rules (§10.3) MUST pass — violations block emit. Warning rules (§10.4) are
logged but non-blocking. Called after html_one produces a slide string.
"""

from __future__ import annotations

from html import unescape
import re
from dataclasses import dataclass, field
from typing import Literal

from .layouts import CANVAS_SIZES, FONT_LIMITS

Severity = Literal["error", "warning"]


@dataclass
class Issue:
    rule: str
    severity: Severity
    message: str


@dataclass
class ValidationResult:
    ok: bool
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def all_issues(self) -> list[Issue]:
        return self.errors + self.warnings


BANNED_FONT_PATTERNS = [
    r"\bInter\b", r"\bRoboto\b", r"\bArial\b", r"\bFraunces\b",
    r"font-family:\s*system-ui", r"-apple-system",
]

FILLER_TEXT_PATTERNS = [
    r"\blorem\s+ipsum\b",
    r"\bplaceholder\s+text\b",
    r"\bdummy\s+content\b",
    r"\bsample\s+text\b",
    r"\btbd\b",
    r"\byour\s+text\s+here\b",
]


def _font_sizes_in_px(html: str) -> list[int]:
    out: list[int] = []
    for m in re.finditer(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*px", html, flags=re.I):
        out.append(int(float(m.group(1))))
    return out


def _has_balanced_braces(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _visible_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", unescape(html)).strip()


def validate_slide_html(
    html: str,
    *,
    aspect_ratio: str = "16:9",
    density: str = "balanced",
) -> ValidationResult:
    errors: list[Issue] = []
    warnings: list[Issue] = []

    canvas = CANVAS_SIZES.get(aspect_ratio, CANVAS_SIZES["16:9"])
    width, height = canvas
    normalized = html.replace(" ", "")

    # ---- §10.3 error rules ----
    if not re.search(rf"width\s*:\s*{width}px\b", html, flags=re.I):
        errors.append(Issue("canvas_width", "error",
                            f"Root element must declare width {width}px."))
    if not re.search(rf"height\s*:\s*{height}px\b", html, flags=re.I):
        errors.append(Issue("canvas_height", "error",
                            f"Root element must declare height {height}px."))
    if "overflow:hidden" not in normalized:
        errors.append(Issue("overflow_hidden", "error",
                            "Root element must have overflow:hidden as a safety net."))
    if re.search(r"overflow\s*:\s*(auto|scroll)", html, flags=re.I):
        errors.append(Issue("no_scroll", "error",
                            "Avoid overflow:auto/scroll — slides must fit the canvas."))
    if re.search(r"slide\s+\d+\s+of\s+\d+", html, flags=re.I):
        errors.append(Issue("no_pagination_text", "error",
                            "Remove 'Slide X of Y' pagination text."))
    if re.search(r"pagination[-_ ]?dot", html, flags=re.I):
        errors.append(Issue("no_pagination_dots", "error",
                            "Remove pagination dot indicators."))
    if "<style" in html.lower() and "</style>" not in html.lower():
        errors.append(Issue("missing_style_close", "error",
                            "HTML output is incomplete: missing </style>."))
    if "<body" in html.lower() and "</body>" not in html.lower():
        errors.append(Issue("missing_body_close", "error",
                            "HTML output is incomplete: missing </body>."))
    if "<html" in html.lower() and "</html>" not in html.lower():
        errors.append(Issue("missing_html_close", "error",
                            "HTML output is incomplete: missing </html>."))
    style_blocks = re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.I | re.S)
    if style_blocks and not _has_balanced_braces(style_blocks[-1]):
        errors.append(Issue("unbalanced_css_braces", "error",
                            "HTML output is incomplete: CSS braces are unbalanced."))

    # ---- §10.4 warnings ----
    if "box-sizing:border-box" not in normalized:
        warnings.append(Issue("box_sizing", "warning",
                              "Prefer box-sizing:border-box globally."))

    limits = FONT_LIMITS.get(density, FONT_LIMITS["balanced"])
    ceiling = limits["display"][1]
    floor = limits["body"][0]
    sizes = _font_sizes_in_px(html)
    if any(s > ceiling for s in sizes):
        warnings.append(Issue("font_size_ceiling", "warning",
                              f"Font sizes above display ceiling ({ceiling}px) detected."))
    below_floor = [s for s in sizes if s < floor]
    if len(below_floor) >= 3:
        warnings.append(Issue("font_size_floor", "warning",
                              f"{len(below_floor)} font-size values below body floor ({floor}px)."))

    for m in re.finditer(r"<img\b[^>]*>", html, flags=re.I):
        tag = m.group(0)
        if "width=" not in tag or "height=" not in tag:
            if "width:" not in tag and "height:" not in tag:
                warnings.append(Issue("image_dimensions", "warning",
                                      "Image placeholder missing explicit width/height."))
        if "data-prompt-hint" not in tag:
            warnings.append(Issue("image_prompt_hint", "warning",
                                  "Image placeholder missing data-prompt-hint."))
        if "object-fit" not in tag:
            warnings.append(Issue("image_object_fit", "warning",
                                  "Image placeholder should have object-fit:cover."))

    if re.search(r"text-overflow\s*:\s*ellipsis", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid text-overflow:ellipsis."))
    if re.search(r"-webkit-line-clamp", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid -webkit-line-clamp."))
    if re.search(r"white-space\s*:\s*nowrap", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid white-space:nowrap."))

    # Banned fonts (anti-slop)
    for pat in BANNED_FONT_PATTERNS:
        if re.search(pat, html):
            warnings.append(Issue("banned_font", "warning",
                                  f"Banned/overused font pattern detected: {pat}"))

    visible_text = _visible_text(html)
    for pat in FILLER_TEXT_PATTERNS:
        if re.search(pat, visible_text, flags=re.I):
            warnings.append(Issue("filler_text", "warning",
                                  f"Potential filler text detected: {pat}"))

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
