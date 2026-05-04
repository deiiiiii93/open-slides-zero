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

IMAGE_SLOT_LABEL_RE = re.compile(
    r"\b(add\s+image\s+here|replace\s+with\s+image)\b",
    flags=re.I,
)

START_TAG_RE = re.compile(r"<[a-z][\w:-]*\b[^>]*>", flags=re.I)


def _attr_value(tag: str, name: str) -> str | None:
    pattern = (
        rf"\b{re.escape(name)}\s*=\s*"
        r"(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))"
    )
    m = re.search(pattern, tag, flags=re.I)
    if not m:
        return None
    for group in m.groups():
        if group is not None:
            return unescape(group)
    return ""


def _style_has_dimension(style: str, prop: str) -> bool:
    return bool(re.search(rf"(?:^|;)\s*{prop}\s*:\s*[^;]+", style, flags=re.I))


def _img_has_explicit_dimensions(tag: str) -> bool:
    style = _attr_value(tag, "style") or ""
    has_width = _attr_value(tag, "width") is not None or _style_has_dimension(style, "width")
    has_height = _attr_value(tag, "height") is not None or _style_has_dimension(style, "height")
    return has_width and has_height


def _slot_has_inline_dimensions(tag: str) -> bool:
    style = _attr_value(tag, "style") or ""
    return _style_has_dimension(style, "width") and _style_has_dimension(style, "height")


def _tag_name(tag: str) -> str:
    m = re.match(r"<\s*([a-z][\w:-]*)", tag, flags=re.I)
    return m.group(1).lower() if m else ""


def _inner_html_for_start_tag(html: str, start_match: re.Match[str]) -> str:
    tag = start_match.group(0)
    name = _tag_name(tag)
    if not name:
        return ""
    close = re.search(rf"</\s*{re.escape(name)}\s*>", html[start_match.end():], flags=re.I)
    if not close:
        return ""
    return html[start_match.end():start_match.end() + close.start()]


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
        src = _attr_value(tag, "src")
        if src is None or not src.strip():
            errors.append(Issue("image_src_required", "error",
                                "Do not emit empty <img> placeholders; use a visible data-image-placeholder slot."))
            continue
        if not _img_has_explicit_dimensions(tag):
            warnings.append(Issue("image_dimensions", "warning",
                                  "Image missing explicit width/height."))
        if "object-fit" not in tag:
            warnings.append(Issue("image_object_fit", "warning",
                                  "Image should have object-fit:cover."))

    for m in START_TAG_RE.finditer(html):
        tag = m.group(0)
        if (_attr_value(tag, "data-image-placeholder") or "").lower() != "true":
            continue
        if not (_attr_value(tag, "data-prompt-hint") or "").strip():
            errors.append(Issue("image_placeholder_hint", "error",
                                "Image placeholder slot missing data-prompt-hint."))
        if not _slot_has_inline_dimensions(tag):
            errors.append(Issue("image_placeholder_dimensions", "error",
                                "Image placeholder slot must declare inline width and height."))
        body = _inner_html_for_start_tag(html, m)
        visible = _visible_text(body)
        if not IMAGE_SLOT_LABEL_RE.search(visible):
            errors.append(Issue("image_placeholder_label", "error",
                                "Image placeholder slot must visibly say 'Add image here' or 'Replace with image'."))

    if re.search(r"text-overflow\s*:\s*ellipsis", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid text-overflow:ellipsis."))
    if re.search(r"-webkit-line-clamp", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid -webkit-line-clamp."))
    if re.search(r"white-space\s*:\s*nowrap", html, flags=re.I):
        warnings.append(Issue("no_text_truncation", "warning",
                              "Avoid white-space:nowrap."))

    if re.search(r"@media\b", html, flags=re.I):
        warnings.append(Issue("responsive_css_in_canvas", "warning",
                              "Avoid @media rules — slides compose for the exact canvas only."))
    if re.search(r":\s*hover\b", html, flags=re.I):
        warnings.append(Issue("non_static_chrome", "warning",
                              "Avoid :hover states — slides are static."))
    if re.search(r"\b(transition|animation)\s*:", html, flags=re.I):
        warnings.append(Issue("non_static_chrome", "warning",
                              "Avoid transitions/animations — slides are static."))
    if re.search(r"@keyframes\b", html, flags=re.I):
        warnings.append(Issue("non_static_chrome", "warning",
                              "Avoid @keyframes — slides are static."))

    weight_offscale = [
        m.group(1)
        for m in re.finditer(r"font-weight\s*:\s*(\d{3})\b", html, flags=re.I)
        if m.group(1) not in {"300", "400", "600", "700"}
    ]
    if weight_offscale:
        warnings.append(Issue("font_weight_offscale", "warning",
                              f"Off-scale font-weight values: {sorted(set(weight_offscale))}. "
                              "Prefer 300/400/600/700."))

    for m in re.finditer(r"<link\b[^>]*>", html, flags=re.I):
        tag = m.group(0)
        rel = (_attr_value(tag, "rel") or "").lower()
        href = (_attr_value(tag, "href") or "").lower()
        if rel == "stylesheet" or "fonts" in href or href.startswith(("http://", "https://", "//")):
            errors.append(Issue("external_resource_link", "error",
                                "Remove external <link> tags — slides must be self-contained."))
            break
    if re.search(r"@import\s+url", html, flags=re.I):
        errors.append(Issue("external_resource_link", "error",
                            "Remove @import url() — slides must be self-contained."))

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
