// Client-side exporters for deck output.
//
// Three formats:
//   exportHtmlSingle  — one self-contained .html file with iframes per slide
//   exportHtmlZip     — .zip with slide_NN.html files + a small index.html
//   exportPptx        — editable .pptx via pptxgenjs v4 walking each slide's DOM
//
// All three run fully in the browser; no backend endpoints are required.

import JSZip from "jszip";
import PptxGenJS from "pptxgenjs";
import type { DeckState } from "./api";

// Keep in sync with DeckCanvas.tsx:17-21. Duplicated rather than shared because
// it's four lines and importing DeckCanvas just for a constant is overkill.
const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

// PPI constant: 96 CSS pixels per inch (standard devtools assumption).
const PPI = 96;

// ---------------- Shared helpers ----------------

function getCanvasSize(deck: DeckState): [number, number] {
  const ar = (deck.values?.aspect_ratio as string) ?? "16:9";
  return CANVAS[ar] ?? CANVAS["16:9"];
}

function sanitizeFileName(name: string): string {
  return name.replace(/[\\/:*?"<>|\x00-\x1f]+/g, "_").trim() || "deck";
}

function getDeckName(deck: DeckState): string {
  const raw = (deck.values?.deck_name as string | undefined) || `deck-${deck.thread_id.slice(0, 6)}`;
  return sanitizeFileName(raw);
}

function getSlideEntries(deck: DeckState): Array<[number, string]> {
  const slides = (deck.values?.html_slides ?? {}) as Record<string | number, string>;
  return Object.entries(slides)
    .map(([k, v]) => [Number(k), v] as [number, string])
    .filter(([k, v]) => Number.isInteger(k) && typeof v === "string" && v.length > 0)
    .sort((a, b) => a[0] - b[0]);
}

export function hasExportableSlides(deck: DeckState | null): boolean {
  if (!deck) return false;
  return getSlideEntries(deck).length > 0;
}

function escapeAttr(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---------------- HTML (single file) ----------------

export async function exportHtmlSingle(deck: DeckState): Promise<void> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const [w, h] = getCanvasSize(deck);
  const deckName = getDeckName(deck);

  const slides = entries
    .map(([idx, html]) => {
      const srcdoc = escapeAttr(html);
      return `    <section class="slide">
      <h2>Slide ${idx + 1}</h2>
      <iframe width="${w}" height="${h}" srcdoc="${srcdoc}" loading="lazy"></iframe>
    </section>`;
    })
    .join("\n");

  const doc = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeText(deckName)}</title>
  <style>
    body { margin: 0; padding: 24px; background: #f5f5f5; font-family: Georgia, serif; color: #111; }
    h1 { margin: 0 0 16px; }
    .slide { margin: 0 0 24px; }
    .slide h2 { margin: 0 0 6px; font-size: 14px; font-weight: 600; color: #555; }
    iframe { border: 1px solid #ccc; background: #fff; display: block; }
  </style>
</head>
<body>
  <h1>${escapeText(deckName)}</h1>
${slides}
</body>
</html>
`;

  const blob = new Blob([doc], { type: "text/html;charset=utf-8" });
  downloadBlob(blob, `${deckName}.html`);
}

// ---------------- HTML (zip of slides) ----------------

function buildZipIndexHtml(
  entries: Array<[number, string]>,
  deckName: string,
  canvas: [number, number],
): string {
  const [w, h] = canvas;
  const items = entries
    .map(([idx]) => {
      const file = `slide_${pad(idx + 1)}.html`;
      return `    <li><a href="${file}" target="slide-view">Slide ${idx + 1}</a></li>`;
    })
    .join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeText(deckName)}</title>
  <style>
    body { margin: 0; font-family: Georgia, serif; display: grid; grid-template-columns: 180px 1fr; min-height: 100vh; }
    nav { background: #f5f5f5; border-right: 1px solid #e5e5e5; padding: 16px; overflow-y: auto; }
    nav h1 { font-size: 16px; margin: 0 0 12px; }
    nav ul { list-style: none; padding: 0; margin: 0; }
    nav a { display: block; padding: 6px 8px; border-radius: 4px; color: #111; text-decoration: none; font-size: 13px; }
    nav a:hover { background: #e5e5e5; }
    main { padding: 16px; }
    iframe { width: ${w}px; height: ${h}px; border: 1px solid #ccc; background: #fff; }
  </style>
</head>
<body>
  <nav>
    <h1>${escapeText(deckName)}</h1>
    <ul>
${items}
    </ul>
  </nav>
  <main>
    <iframe name="slide-view" src="slide_${pad((entries[0]?.[0] ?? 0) + 1)}.html"></iframe>
  </main>
</body>
</html>
`;
}

export async function exportHtmlZip(deck: DeckState): Promise<void> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const deckName = getDeckName(deck);
  const canvas = getCanvasSize(deck);

  const zip = new JSZip();
  for (const [idx, html] of entries) {
    zip.file(`slide_${pad(idx + 1)}.html`, html);
  }
  zip.file("index.html", buildZipIndexHtml(entries, deckName, canvas));

  const blob = await zip.generateAsync({ type: "blob" });
  downloadBlob(blob, `${deckName}.zip`);
}

// ---------------- PPTX (editable) ----------------

// Wait for an iframe's srcdoc content to actually be parsed and for fonts to load.
async function waitForIframeReady(f: HTMLIFrameElement, timeoutMs = 10000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const doc = f.contentDocument;
    if (
      doc &&
      doc.readyState === "complete" &&
      doc.body &&
      ((doc.URL ?? "").startsWith("about:srcdoc") || doc.body.childElementCount > 0)
    ) {
      break;
    }
    await new Promise((r) => setTimeout(r, 30));
  }
  try {
    const fonts = f.contentDocument?.fonts;
    if (fonts && "ready" in fonts) {
      await Promise.race([fonts.ready, new Promise((res) => setTimeout(res, 3000))]);
    }
  } catch {
    // Cross-origin guard — sandboxed iframes can throw; ignore.
  }
}

// --- Color helpers ---

function cssColorToHex(color: string): string {
  if (!color || color === "transparent" || color === "inherit" || color === "initial") return "";
  if (color.startsWith("#")) return color.replace("#", "").toUpperCase();
  if (color.startsWith("rgb")) {
    const rgba = color.match(/(\d+(\.\d+)?)/g);
    if (rgba && rgba.length >= 3) {
      const r = parseInt(rgba[0], 10).toString(16).padStart(2, "0");
      const g = parseInt(rgba[1], 10).toString(16).padStart(2, "0");
      const b = parseInt(rgba[2], 10).toString(16).padStart(2, "0");
      return (r + g + b).toUpperCase();
    }
  }
  return "";
}

function isEffectivelyTransparent(colorStr: string): boolean {
  if (!colorStr || colorStr === "transparent" || colorStr === "inherit" || colorStr === "initial") return true;
  if (colorStr.startsWith("rgba")) {
    const rgba = colorStr.match(/(\d+(\.\d+)?)/g);
    if (rgba && rgba.length >= 4) {
      return parseFloat(rgba[3]) <= 0.01;
    }
  }
  return false;
}

function getMixedTransparency(colorStr: string, cssOpacity: string): number | undefined {
  let alpha = 1;
  const elementOpacity = parseFloat(cssOpacity);
  if (!isNaN(elementOpacity)) alpha *= elementOpacity;
  if (colorStr && colorStr.startsWith("rgba")) {
    const rgba = colorStr.match(/(\d+(\.\d+)?)/g);
    if (rgba && rgba.length >= 4) {
      alpha *= parseFloat(rgba[3]);
    }
  }
  if (alpha >= 1) return undefined;
  return Math.round((1 - alpha) * 100);
}

function parseColor(colorStr: string, opacityStr = "1"): { hex: string; transparency?: number } | null {
  const hex = cssColorToHex(colorStr);
  if (!hex) return null;
  const transparency = getMixedTransparency(colorStr, opacityStr);
  return { hex, transparency };
}

/** Render individual border-line shapes for each non-uniform side. */
function renderBorderLines(
  slide: PptxGenJS.Slide,
  pos: { x: number; y: number; w: number; h: number },
  borderTopW: number,
  borderRightW: number,
  borderBottomW: number,
  borderLeftW: number,
  color: { hex: string; transparency?: number } | null,
  dash: string,
): void {
  if (!color) return;
  const lineOpts = {
    color: color.hex,
    transparency: color.transparency,
    width: 0, // set per-side below
    dashType: dash as any,
  };
  if (borderTopW > 0) {
    slide.addShape("rect" as PptxGenJS.ShapeType, {
      x: pos.x, y: pos.y, w: pos.w, h: pxToIn(borderTopW),
      fill: { color: color.hex, transparency: color.transparency },
      line: { ...lineOpts, width: borderTopW * 0.75 },
    });
  }
  if (borderBottomW > 0) {
    slide.addShape("rect" as PptxGenJS.ShapeType, {
      x: pos.x, y: pos.y + pos.h - pxToIn(borderBottomW), w: pos.w, h: pxToIn(borderBottomW),
      fill: { color: color.hex, transparency: color.transparency },
      line: { ...lineOpts, width: borderBottomW * 0.75 },
    });
  }
  if (borderLeftW > 0) {
    slide.addShape("rect" as PptxGenJS.ShapeType, {
      x: pos.x, y: pos.y, w: pxToIn(borderLeftW), h: pos.h,
      fill: { color: color.hex, transparency: color.transparency },
      line: { ...lineOpts, width: borderLeftW * 0.75 },
    });
  }
  if (borderRightW > 0) {
    slide.addShape("rect" as PptxGenJS.ShapeType, {
      x: pos.x + pos.w - pxToIn(borderRightW), y: pos.y, w: pxToIn(borderRightW), h: pos.h,
      fill: { color: color.hex, transparency: color.transparency },
      line: { ...lineOpts, width: borderRightW * 0.75 },
    });
  }
}

/** Check whether a pseudo-element contributes visible content. */
function hasVisiblePseudo(el: HTMLElement, pseudo: "::before" | "::after"): boolean {
  try {
    const s = window.getComputedStyle(el, pseudo);
    if (!s || s.content === "none" || s.content === '""' || s.content === "''") return false;
    const hasVisual =
      cssColorToHex(s.backgroundColor) !== "" ||
      parseFloat(s.width) > 0 ||
      parseFloat(s.height) > 0 ||
      parseFloat(s.borderTopWidth) > 0;
    return hasVisual;
  } catch {
    return false;
  }
}

/** Render a ::before or ::after pseudo-element as a shape. Position is approximated from parent rect + CSS offsets. */
function renderPseudoElement(
  el: HTMLElement,
  pseudo: "::before" | "::after",
  ctx: PptxContext,
  parentRect: DOMRect,
): void {
  try {
    const s = window.getComputedStyle(el, pseudo);
    if (!s) return;
    const bg = parseColor(s.backgroundColor, s.opacity);
    const borderW = parseFloat(s.borderTopWidth) || 0;
    const borderColor = parseColor(s.borderTopColor, s.opacity);
    if (!bg && borderW === 0) return;

    const parentW = parentRect.width;
    const parentH = parentRect.height;

    // Resolve dimensions
    let wPx = parseFloat(s.width) || 0;
    let hPx = parseFloat(s.height) || 0;
    if (s.width === "auto" || s.width === "" || wPx === 0) wPx = parentW;
    if (s.height === "auto" || s.height === "" || hPx === 0) hPx = parentH;

    // Resolve position offsets
    let leftPx = 0, topPx = 0;
    if (s.position === "absolute" || s.position === "fixed") {
      leftPx = parseFloat(s.left) || 0;
      topPx = parseFloat(s.top) || 0;
      if (s.left === "auto" && s.right !== "auto") {
        leftPx = parentW - wPx - (parseFloat(s.right) || 0);
      }
      if (s.top === "auto" && s.bottom !== "auto") {
        topPx = parentH - hPx - (parseFloat(s.bottom) || 0);
      }
    } else if (s.position === "relative") {
      leftPx = parseFloat(s.left) || 0;
      topPx = parseFloat(s.top) || 0;
    }

    const xPx = parentRect.left - ctx.rootRect.left + leftPx;
    const yPx = parentRect.top - ctx.rootRect.top + topPx;
    const x = pxToIn(xPx);
    const y = pxToIn(yPx);
    const w = pxToIn(wPx);
    const h = pxToIn(hPx);
    if (w <= 0 || h <= 0) return;

    const shapeType = getShapeType(wPx, hPx, s.borderRadius);
    const rectRadius = shapeType === "roundRect"
      ? getRectRadius(wPx, hPx, s.borderRadius)
      : undefined;

    const opts: Record<string, unknown> = { x, y, w, h };
    if (bg) opts.fill = { color: bg.hex, transparency: bg.transparency };
    if (borderW > 0 && borderColor) {
      const dash = s.borderStyle === "dashed" ? "dash" : s.borderStyle === "dotted" ? "dot" : "solid";
      opts.line = { width: borderW * 0.75, color: borderColor.hex, dashType: dash };
    }
    const clipPoints = parseClipPathPolygon(s.clipPath, wPx, hPx);
    const matrix = parseTransformMatrix(s.transform);
    const hasSkew = matrix !== null && !isPureRotationMatrix(matrix);
    if (clipPoints) {
      const custOpts: Record<string, unknown> = { ...opts };
      const pts = clipPoints.map((p) => ({ x: pxToIn(p.x), y: pxToIn(p.y) }));
      custOpts.points = [
        { x: pts[0].x, y: pts[0].y, moveTo: true },
        ...pts.slice(1).map((p) => ({ x: p.x, y: p.y })),
        { close: true },
      ];
      ctx.slide.addShape("custGeom" as PptxGenJS.ShapeType, custOpts);
    } else if (hasSkew && matrix) {
      const { vertices, aabbW, aabbH, minX, minY } = transformedRectVertices(matrix, wPx, hPx);
      const skewOpts: Record<string, unknown> = {
        x: pxToIn(xPx + minX),
        y: pxToIn(yPx + minY),
        w: pxToIn(aabbW),
        h: pxToIn(aabbH),
      };
      if (bg) skewOpts.fill = { color: bg.hex, transparency: bg.transparency };
      if (borderW > 0 && borderColor) {
        const dash = s.borderStyle === "dashed" ? "dash" : s.borderStyle === "dotted" ? "dot" : "solid";
        skewOpts.line = { width: borderW * 0.75, color: borderColor.hex, dashType: dash };
      }
      skewOpts.points = [
        { x: pxToIn(vertices[0].x), y: pxToIn(vertices[0].y), moveTo: true },
        ...vertices.slice(1).map((v) => ({ x: pxToIn(v.x), y: pxToIn(v.y) })),
        { close: true },
      ];
      ctx.slide.addShape("custGeom" as PptxGenJS.ShapeType, skewOpts);
    } else {
      if (rectRadius !== undefined) opts.rectRadius = rectRadius;
      const pseudoRot = getRotation(s.transform);
      if (pseudoRot !== undefined) opts.rotate = pseudoRot;
      ctx.slide.addShape(shapeType as PptxGenJS.ShapeType, opts);
    }
  } catch {
    // Ignore pseudo-element access errors
  }
}

// --- DOM to PPTX mapping ---

interface PptxContext {
  slide: PptxGenJS.Slide;
  rootRect: DOMRect;
  pptx: PptxGenJS;
}

function hasHtmlBoxMetrics(node: Element): node is Element & { offsetWidth: number; offsetHeight: number } {
  return typeof (node as { offsetWidth?: unknown }).offsetWidth === "number" &&
    typeof (node as { offsetHeight?: unknown }).offsetHeight === "number";
}

/** Convert px to inches at 96 PPI. */
function pxToIn(px: number): number {
  return px / PPI;
}

/** Get element position relative to slide root, in inches. */
function getInches(rect: DOMRect, rootRect: DOMRect): { x: number; y: number; w: number; h: number } {
  return {
    x: pxToIn(rect.left - rootRect.left),
    y: pxToIn(rect.top - rootRect.top),
    w: pxToIn(rect.width),
    h: pxToIn(rect.height),
  };
}

/** Extract rotation angle from CSS transform matrix. */
function getRotation(transform: string): number | undefined {
  if (!transform || transform === "none") return undefined;
  const match = transform.match(/matrix\(([^)]+)\)/);
  if (match) {
    const vals = match[1].split(",").map((v) => parseFloat(v.trim()));
    if (vals.length >= 4) {
      const angle = Math.round(Math.atan2(vals[1], vals[0]) * (180 / Math.PI));
      return angle !== 0 ? angle : undefined;
    }
  }
  const m3d = transform.match(/matrix3d\(([^)]+)\)/);
  if (m3d) {
    const vals = m3d[1].split(",").map((v) => parseFloat(v.trim()));
    if (vals.length >= 16) {
      const angle = Math.round(Math.atan2(vals[1], vals[0]) * (180 / Math.PI));
      return angle !== 0 ? angle : undefined;
    }
  }
  const rotateMatch = transform.match(/rotate\(([-\d.]+)(deg|rad)?\)/);
  if (rotateMatch) {
    let angle = parseFloat(rotateMatch[1]);
    if (rotateMatch[2] === "rad") angle = angle * (180 / Math.PI);
    return angle !== 0 ? angle : undefined;
  }
  return undefined;
}

/**
 * Detect transforms that make `getBoundingClientRect()` return a larger AABB
 * than the element's unrotated box (rotation, skew). Pure translate or pure
 * axis-aligned scale leaves the AABB equal to the rendered box, so we keep
 * using the rect in those cases.
 */
function transformInflatesBoundingBox(transform: string): boolean {
  if (!transform || transform === "none") return false;
  const match = transform.match(/matrix\(([^)]+)\)/);
  if (match) {
    const v = match[1].split(",").map((s) => parseFloat(s.trim()));
    if (v.length >= 4) {
      return Math.abs(v[1]) > 0.001 || Math.abs(v[2]) > 0.001;
    }
  }
  const m3d = transform.match(/matrix3d\(([^)]+)\)/);
  if (m3d) {
    const v = m3d[1].split(",").map((s) => parseFloat(s.trim()));
    if (v.length >= 16) {
      // 2D rotation/skew components within the 4x4 matrix are at indices 1 and 4.
      return Math.abs(v[1]) > 0.001 || Math.abs(v[4]) > 0.001;
    }
  }
  return false;
}

/**
 * 2D affine matrix extracted from a CSS `transform` computed value
 * (`matrix(a, b, c, d, e, f)` or the 2D subset of `matrix3d(...)`).
 */
interface AffineMatrix { a: number; b: number; c: number; d: number; e: number; f: number }

function parseTransformMatrix(transform: string): AffineMatrix | null {
  if (!transform || transform === "none") return null;
  const m = transform.match(/matrix\(([^)]+)\)/);
  if (m) {
    const v = m[1].split(",").map((s) => parseFloat(s.trim()));
    if (v.length >= 6 && v.every(Number.isFinite)) {
      return { a: v[0], b: v[1], c: v[2], d: v[3], e: v[4], f: v[5] };
    }
  }
  const m3d = transform.match(/matrix3d\(([^)]+)\)/);
  if (m3d) {
    const v = m3d[1].split(",").map((s) => parseFloat(s.trim()));
    // matrix3d is column-major; the 2D subset is at indices 0,1 (col 1), 4,5 (col 2), 12,13 (col 4 translate).
    if (v.length >= 16 && v.every(Number.isFinite)) {
      return { a: v[0], b: v[1], c: v[4], d: v[5], e: v[12], f: v[13] };
    }
  }
  return null;
}

/**
 * True when the matrix is a rotation (optionally with uniform scale) — i.e. it
 * preserves angles. A pure rotation has `c === -b` and `a === d`. Anything
 * else (skew, non-uniform scale + rotation) distorts the rectangle into a
 * non-rectangular quadrilateral.
 */
function isPureRotationMatrix(m: AffineMatrix): boolean {
  return Math.abs(m.c + m.b) < 0.001 && Math.abs(m.a - m.d) < 0.001;
}

interface PolygonPoint { x: number; y: number }

/**
 * Apply a CSS transform matrix (origin = element center) to the four corners
 * of a `widthPx × heightPx` rectangle, then shift the result so the AABB's
 * top-left is at (0, 0). Returns the four local-space vertices and the AABB
 * dimensions — exactly what pptxgenjs needs for a `custGeom` parallelogram.
 */
function transformedRectVertices(
  m: AffineMatrix,
  widthPx: number,
  heightPx: number,
): { vertices: PolygonPoint[]; aabbW: number; aabbH: number; minX: number; minY: number } {
  const cx = widthPx / 2;
  const cy = heightPx / 2;
  const corners = [
    { x: 0, y: 0 },
    { x: widthPx, y: 0 },
    { x: widthPx, y: heightPx },
    { x: 0, y: heightPx },
  ];
  const moved = corners.map((p) => ({
    x: m.a * (p.x - cx) + m.c * (p.y - cy) + cx + m.e,
    y: m.b * (p.x - cx) + m.d * (p.y - cy) + cy + m.f,
  }));
  const minX = Math.min(...moved.map((p) => p.x));
  const minY = Math.min(...moved.map((p) => p.y));
  const maxX = Math.max(...moved.map((p) => p.x));
  const maxY = Math.max(...moved.map((p) => p.y));
  return {
    vertices: moved.map((p) => ({ x: p.x - minX, y: p.y - minY })),
    aabbW: maxX - minX,
    aabbH: maxY - minY,
    minX,
    minY,
  };
}

/**
 * Parse `clip-path: polygon(x y, x y, ...)` into shape-local pixel points.
 * Accepts percentages (`50%`) and pixel lengths (`12px` / bare numbers).
 * Returns null if the element has no clip-path polygon or fewer than 3 points.
 */

function parseClipPathPolygon(
  clipPath: string,
  widthPx: number,
  heightPx: number,
): PolygonPoint[] | null {
  if (!clipPath || clipPath === "none") return null;
  const match = clipPath.match(/polygon\(\s*([^)]+)\s*\)/);
  if (!match) return null;
  const pairs = match[1].split(",").map((s) => s.trim()).filter(Boolean);
  const points: PolygonPoint[] = [];
  for (const pair of pairs) {
    const tokens = pair.split(/\s+/).filter(Boolean);
    if (tokens.length < 2) return null;
    const x = resolveClipCoord(tokens[0], widthPx);
    const y = resolveClipCoord(tokens[1], heightPx);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    points.push({ x, y });
  }
  return points.length >= 3 ? points : null;
}

function resolveClipCoord(token: string, refPx: number): number {
  const t = token.trim();
  if (t.endsWith("%")) return (parseFloat(t) / 100) * refPx;
  if (t.endsWith("px")) return parseFloat(t);
  return parseFloat(t);
}

/**
 * Compute the shape box (in inches) to pass to PPTX. For rotated / skewed
 * elements we substitute the element's intrinsic (offset) box centered on
 * the AABB center, so PPTX draws the real shape and then rotates it — rather
 * than inheriting the inflated AABB as the shape size.
 */
function getShapeBox(
  node: Element,
  rect: DOMRect,
  rootRect: DOMRect,
  transform: string,
): { x: number; y: number; w: number; h: number; widthPx: number; heightPx: number } {
  if (
    transformInflatesBoundingBox(transform) &&
    hasHtmlBoxMetrics(node) &&
    node.offsetWidth > 0 &&
    node.offsetHeight > 0
  ) {
    const wPx = node.offsetWidth;
    const hPx = node.offsetHeight;
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    return {
      x: pxToIn(centerX - wPx / 2 - rootRect.left),
      y: pxToIn(centerY - hPx / 2 - rootRect.top),
      w: pxToIn(wPx),
      h: pxToIn(hPx),
      widthPx: wPx,
      heightPx: hPx,
    };
  }
  return {
    x: pxToIn(rect.left - rootRect.left),
    y: pxToIn(rect.top - rootRect.top),
    w: pxToIn(rect.width),
    h: pxToIn(rect.height),
    widthPx: rect.width,
    heightPx: rect.height,
  };
}

/** Check if element is effectively invisible. */
function isHidden(style: CSSStyleDeclaration): boolean {
  return style.display === "none" || style.visibility === "hidden" || style.opacity === "0";
}

/** Determine if an element is a block-level container (not inline text styling). */
function isBlockElement(tag: string, style: CSSStyleDeclaration): boolean {
  const blockTags = ["DIV", "SECTION", "ARTICLE", "HEADER", "FOOTER", "ASIDE", "MAIN", "NAV", "P", "H1", "H2", "H3", "H4", "H5", "H6", "LI", "FIGURE", "BLOCKQUOTE"];
  if (blockTags.includes(tag)) return true;
  const display = style.display;
  return display === "block" || display === "flex" || display === "grid" || display === "inline-block";
}

/** Determine shape type from border-radius. */
function getShapeType(widthPx: number, heightPx: number, borderRadius: string): string {
  const radiusVal = parseFloat(borderRadius) || 0;
  if (radiusVal <= 0) return "rect";

  const minSide = Math.min(widthPx, heightPx);
  const isPercentage = borderRadius.includes("%");
  const radiusPx = isPercentage ? (radiusVal / 100) * minSide : radiusVal;

  const isSquare = Math.abs(widthPx - heightPx) < 1.5;
  const isFullRound = radiusPx >= minSide / 2 - 0.5;

  if (isFullRound && (isPercentage || isSquare)) {
    return "ellipse";
  }
  return "roundRect";
}

/** Build rectRadius for roundRect (0.0 to 1.0). */
function getRectRadius(widthPx: number, heightPx: number, borderRadius: string): number | undefined {
  const radiusVal = parseFloat(borderRadius) || 0;
  if (radiusVal <= 0) return undefined;

  const minSide = Math.min(widthPx, heightPx);
  const isPercentage = borderRadius.includes("%");
  const radiusPx = isPercentage ? (radiusVal / 100) * minSide : radiusVal;

  // PptxGenJS rectRadius is a ratio: corner radius / half of the shortest side.
  // Valid range: 0.0 to 1.0 (1.0 = full pill / capsule shape).
  return Math.min(radiusPx / (minSide / 2), 1.0);
}

/** Collect text runs from an element tree, stopping at block child boundaries. */
interface TextRun {
  text: string;
  options: {
    fontSize?: number;
    fontFace?: string;
    color?: string;
    bold?: boolean;
    italic?: boolean;
    underline?: boolean;
    strike?: boolean;
    highlight?: string;
    breakLine?: boolean;
    bullet?: unknown;
  };
}

function collectTextRuns(node: Node, baseStyle: CSSStyleDeclaration, scale = 1): TextRun[] {
  const runs: TextRun[] = [];

  function walk(n: Node, inheritedStyle: CSSStyleDeclaration) {
    if (n.nodeType === Node.TEXT_NODE) {
      let text = n.textContent || "";
      text = text.replace(/[\n\r\t]+/g, " ").replace(/\s{2,}/g, " ");
      if (!text.trim()) return;

      const pxSize = parseFloat(inheritedStyle.fontSize) || 14;
      const ptSize = pxSize * 0.75 * scale;
      const bgHex = cssColorToHex(inheritedStyle.backgroundColor);

      runs.push({
        text,
        options: {
          fontSize: ptSize > 0 ? ptSize : undefined,
          fontFace: inheritedStyle.fontFamily?.split(",")[0].replace(/['"]/g, "").trim() || undefined,
          color: cssColorToHex(inheritedStyle.color) || undefined,
          bold: parseInt(inheritedStyle.fontWeight) >= 600 || inheritedStyle.fontWeight === "bold",
          italic: inheritedStyle.fontStyle === "italic",
          underline: inheritedStyle.textDecoration.includes("underline"),
          strike: inheritedStyle.textDecoration.includes("line-through"),
          highlight: (bgHex && !isEffectivelyTransparent(inheritedStyle.backgroundColor)) ? bgHex : undefined,
        },
      });
      return;
    }

    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as Element;
    const tag = el.tagName;

    // Skip hidden elements
    const style = window.getComputedStyle(el);
    if (isHidden(style)) return;

    // Stop at block children — they'll be handled separately
    if (el !== node && isBlockElement(tag, style)) return;

    // Handle <br>
    if (tag === "BR") {
      if (runs.length > 0) {
        runs.push({ text: "", options: { breakLine: true } });
      }
      return;
    }

    // For inline elements, use their computed style
    const effectiveStyle = tag === "SPAN" || tag === "B" || tag === "STRONG" || tag === "I" || tag === "EM" || tag === "U" || tag === "A" || tag === "SMALL" || tag === "FONT"
      ? style
      : inheritedStyle;

    el.childNodes.forEach((child) => walk(child, effectiveStyle));
  }

  node.childNodes.forEach((child) => walk(child, baseStyle));
  return runs;
}

/** Process a single DOM element into PPTX shapes. */
function processElement(node: Element, ctx: PptxContext, depth = 0): void {
  const style = window.getComputedStyle(node);
  if (isHidden(style)) return;

  const rect = node.getBoundingClientRect();
  if (rect.width < 0.5 || rect.height < 0.5) return;

  const tag = node.tagName;
  const box = getShapeBox(node, rect, ctx.rootRect, style.transform);
  const pos = { x: box.x, y: box.y, w: box.w, h: box.h };
  const rotation = getRotation(style.transform);
  const matrix = parseTransformMatrix(style.transform);
  // Non-rotation transforms (skew, or rotation combined with skew / non-uniform
  // scale) distort the rectangle into a parallelogram. PPTX has no skew, so we
  // bake the geometry into a `custGeom` polygon rather than drawing a rect.
  const hasSkew = matrix !== null && !isPureRotationMatrix(matrix);

  // Skip script/style tags
  if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT") return;

  // Render ::before pseudo-element if it has visible content.
  if (hasVisiblePseudo(node as HTMLElement, "::before")) {
    renderPseudoElement(node as HTMLElement, "::before", ctx, rect);
  }

  // --- IMG ---
  if (tag === "IMG") {
    const img = node as HTMLImageElement;
    const rawSrc = img.getAttribute("src");
    // Skip blank placeholders — in an iframe empty src resolves to about:srcdoc.
    if (!rawSrc || rawSrc.trim() === "" || img.src.startsWith("about:")) {
      return;
    }
    if (img.src) {
      const opacity = parseFloat(style.opacity);
      const objectFit = style.objectFit;
      const imgOpts: Record<string, unknown> = {
        path: img.src,
        x: pos.x,
        y: pos.y,
        w: pos.w,
        h: pos.h,
        rotate: rotation,
        transparency: !isNaN(opacity) && opacity < 1 ? Math.round((1 - opacity) * 100) : undefined,
      };
      if (objectFit === "cover") {
        imgOpts.sizing = { type: "cover", w: pos.w, h: pos.h };
      } else if (objectFit === "contain") {
        imgOpts.sizing = { type: "contain", w: pos.w, h: pos.h };
      }
      ctx.slide.addImage(imgOpts);
    }
    return; // leaf — don't recurse into children
  }

  // --- SVG ---
  if (tag === "SVG") {
    try {
      const svgXml = new XMLSerializer().serializeToString(node);
      const svgData = "data:image/svg+xml;base64," + btoa(encodeURIComponent(svgXml).replace(/%([0-9A-F]{2})/g, (_, p1) => String.fromCharCode(parseInt(p1, 16))));
      ctx.slide.addImage({
        data: svgData,
        x: pos.x,
        y: pos.y,
        w: pos.w,
        h: pos.h,
        rotate: rotation,
      });
    } catch {
      // SVG serialization failed — skip
    }
    return;
  }

  // --- TABLE ---
  if (tag === "TABLE") {
    processTable(node as HTMLTableElement, ctx, pos);
    return; // leaf — table cells handled inside
  }

  // --- CANVAS ---
  if (tag === "CANVAS") {
    try {
      const canvas = node as HTMLCanvasElement;
      const dataUrl = canvas.toDataURL("image/png");
      ctx.slide.addImage({
        data: dataUrl,
        x: pos.x,
        y: pos.y,
        w: pos.w,
        h: pos.h,
        rotate: rotation,
      });
    } catch {
      // Tainted canvas — skip
    }
    return;
  }

  // --- LISTS (UL / OL) ---
  if (tag === "UL" || tag === "OL") {
    processList(node as HTMLElement, ctx, pos, style, rotation);
    return;
  }

  // --- TEXT + SHAPE handling for block elements ---
  const hasBgImage = style.backgroundImage && style.backgroundImage !== "none";
  const hasBg = cssColorToHex(style.backgroundColor) !== "" || hasBgImage;
  const hasBorder = parseFloat(style.borderWidth) > 0 && cssColorToHex(style.borderColor) !== "";
  const textRuns = collectTextRuns(node, style);
  const hasText = textRuns.length > 0 && textRuns.some((r) => r.text.trim().length > 0);

  // Determine if this element warrants its own shape
  const isBlock = isBlockElement(tag, style) || tag === "BUTTON" || tag === "INPUT" || tag === "TEXTAREA";
  const shouldRender = isBlock || hasBg || hasBorder || tag === "A" || tag === "SPAN";

  if (!shouldRender && !hasText) {
    // Just recurse into children
    Array.from(node.children).forEach((child) => processElement(child, ctx, depth + 1));
    return;
  }

  // Build shape options. Use the intrinsic box size so rotated elements don't
  // pick up the inflated AABB dimensions.
  const shapeType = getShapeType(box.widthPx, box.heightPx, style.borderRadius);
  const rectRadius = shapeType === "roundRect"
    ? getRectRadius(box.widthPx, box.heightPx, style.borderRadius)
    : undefined;

  const bgColor = parseColor(style.backgroundColor, style.opacity);

  // Read individual border sides to handle asymmetric borders correctly.
  const borderTopW = parseFloat(style.borderTopWidth) || 0;
  const borderRightW = parseFloat(style.borderRightWidth) || 0;
  const borderBottomW = parseFloat(style.borderBottomWidth) || 0;
  const borderLeftW = parseFloat(style.borderLeftWidth) || 0;
  const maxBorderWidth = Math.max(borderTopW, borderRightW, borderBottomW, borderLeftW);
  // Only apply a uniform shape border when all sides match.
  const isUniformBorder =
    maxBorderWidth > 0 &&
    borderTopW === maxBorderWidth &&
    borderRightW === maxBorderWidth &&
    borderBottomW === maxBorderWidth &&
    borderLeftW === maxBorderWidth;
  const borderColor = parseColor(style.borderTopColor, style.opacity);

  const shapeOpts: Record<string, unknown> = {
    x: pos.x,
    y: pos.y,
    w: pos.w,
    h: pos.h,
    rotate: rotation,
  };

  if (bgColor) {
    shapeOpts.fill = { color: bgColor.hex, transparency: bgColor.transparency };
  }

  const dash = style.borderStyle === "dashed" ? "dash" : style.borderStyle === "dotted" ? "dot" : "solid";
  if (isUniformBorder && borderColor) {
    shapeOpts.line = {
      width: maxBorderWidth * 0.75,
      color: borderColor.hex,
      dashType: dash,
    };
  }

  if (rectRadius !== undefined) {
    shapeOpts.rectRadius = rectRadius;
  }

  if (hasText) {
    // Determine text alignment
    let align: "left" | "center" | "right" | "justify" = "left";
    if (style.textAlign === "center") align = "center";
    else if (style.textAlign === "right" || style.textAlign === "end") align = "right";
    else if (style.textAlign === "justify") align = "justify";

    let valign: "top" | "middle" | "bottom" = "top";
    if (style.alignItems === "center" || style.verticalAlign === "middle") valign = "middle";
    else if (style.verticalAlign === "bottom") valign = "bottom";
    // If flex with center justify, center horizontally
    if (style.display.includes("flex") && style.justifyContent === "center") align = "center";

    // Padding as margin in pt.
    // PptxGenJS text bodyProp order: margin[0]=left, [1]=right, [2]=bottom, [3]=top.
    const pt = (v: string) => (parseFloat(v || "0") || 0) * 0.75;
    const margin: [number, number, number, number] = [
      pt(style.paddingLeft),
      pt(style.paddingRight),
      pt(style.paddingBottom),
      pt(style.paddingTop),
    ];

    // If shape has background or border, combine text with shape
    if (bgColor || borderColor) {
      const textOpts: Record<string, unknown> = {
        ...shapeOpts,
        shape: shapeType,
        align,
        valign,
        margin,
        wrap: true,
        fit: "shrink",
      };
      ctx.slide.addText(textRuns as any, textOpts);
    } else {
      // Text only — no shape background
      ctx.slide.addText(textRuns as any, {
        x: pos.x,
        y: pos.y,
        w: pos.w,
        h: pos.h,
        rotate: rotation,
        align,
        valign,
        margin,
        wrap: true,
        fit: "shrink",
      });
    }
  } else if (bgColor || borderColor || hasBg) {
    // If the element uses `clip-path: polygon(...)` (trapezoids, triangles,
    // arrow-ish panels), emit a custom-geometry shape so the sloped / angled
    // edges survive. Otherwise fall back to the rect / roundRect path.
    const clipPoints = parseClipPathPolygon(style.clipPath, box.widthPx, box.heightPx);
    if (clipPoints) {
      const custOpts: Record<string, unknown> = { ...shapeOpts };
      delete custOpts.rectRadius;
      const pts = clipPoints.map((p) => ({ x: pxToIn(p.x), y: pxToIn(p.y) }));
      custOpts.points = [
        { x: pts[0].x, y: pts[0].y, moveTo: true },
        ...pts.slice(1).map((p) => ({ x: p.x, y: p.y })),
        { close: true },
      ];
      ctx.slide.addShape("custGeom" as PptxGenJS.ShapeType, custOpts);
    } else if (
      hasSkew &&
      hasHtmlBoxMetrics(node) &&
      node.offsetWidth > 0 &&
      node.offsetHeight > 0 &&
      matrix
    ) {
      // Skewed (or skew+rotated) shape: emit a parallelogram whose vertices
      // are the transformed corners of the untransformed box, sitting inside
      // the browser's AABB. `rotate` is NOT applied — the rotation, if any,
      // is already baked into the vertex positions.
      const { vertices } = transformedRectVertices(matrix, node.offsetWidth, node.offsetHeight);
      const skewOpts: Record<string, unknown> = {
        x: pxToIn(rect.left - ctx.rootRect.left),
        y: pxToIn(rect.top - ctx.rootRect.top),
        w: pxToIn(rect.width),
        h: pxToIn(rect.height),
      };
      if (bgColor) skewOpts.fill = { color: bgColor.hex, transparency: bgColor.transparency };
      if (isUniformBorder && borderColor) {
        skewOpts.line = { width: maxBorderWidth * 0.75, color: borderColor.hex, dashType: dash };
      }
      skewOpts.points = [
        { x: pxToIn(vertices[0].x), y: pxToIn(vertices[0].y), moveTo: true },
        ...vertices.slice(1).map((v) => ({ x: pxToIn(v.x), y: pxToIn(v.y) })),
        { close: true },
      ];
      ctx.slide.addShape("custGeom" as PptxGenJS.ShapeType, skewOpts);
    } else {
      // Shape with no text (keep shape even for image/gradient backgrounds we can't fill)
      ctx.slide.addShape(shapeType as PptxGenJS.ShapeType, shapeOpts);
    }
  }

  // Render individual border lines for non-uniform borders.
  if (!isUniformBorder && maxBorderWidth > 0 && borderColor) {
    renderBorderLines(ctx.slide, pos, borderTopW, borderRightW, borderBottomW, borderLeftW, borderColor, dash);
  }

  // Recurse into child block elements (but not if we already consumed the whole subtree as text)
  if (!hasText) {
    Array.from(node.children).forEach((child) => processElement(child, ctx, depth + 1));
  } else {
    // We consumed inline text; recurse into block children and inline visual children
    Array.from(node.children).forEach((child) => {
      const childStyle = window.getComputedStyle(child);
      const childTag = child.tagName;
      if (isHidden(childStyle)) return;
      if (isBlockElement(childTag, childStyle)) {
        processElement(child, ctx, depth + 1);
      } else if (
        childTag === "IMG" ||
        childTag === "SVG" ||
        childTag === "PICTURE" ||
        childTag === "CANVAS" ||
        childStyle.backgroundColor !== "rgba(0, 0, 0, 0)" ||
        parseFloat(childStyle.borderTopWidth) > 0
      ) {
        processElement(child as HTMLElement, ctx, depth + 1);
      }
    });
  }

  // Render ::after pseudo-element if it has visible content.
  if (hasVisiblePseudo(node as HTMLElement, "::after")) {
    renderPseudoElement(node as HTMLElement, "::after", ctx, rect);
  }
}

/** Process a UL / OL element into PPTX text boxes with bullets. */
function processList(
  listNode: HTMLElement,
  ctx: PptxContext,
  pos: { x: number; y: number; w: number; h: number },
  listStyle: CSSStyleDeclaration,
  rotation: number | undefined,
): void {
  const isOrdered = listNode.tagName === "OL";
  const listBg = parseColor(listStyle.backgroundColor, listStyle.opacity);
  const listBorderW = parseFloat(listStyle.borderWidth) || 0;
  const listBorderColor = parseColor(listStyle.borderColor, listStyle.opacity);

  // Render list container background/border if present.
  if (listBg || (listBorderW > 0 && listBorderColor)) {
    const shapeType = getShapeType(pos.w * PPI, pos.h * PPI, listStyle.borderRadius);
    const rectRadius = shapeType === "roundRect"
      ? getRectRadius(pos.w * PPI, pos.h * PPI, listStyle.borderRadius)
      : undefined;
    const shapeOpts: Record<string, unknown> = { x: pos.x, y: pos.y, w: pos.w, h: pos.h, rotate: rotation };
    if (listBg) shapeOpts.fill = { color: listBg.hex, transparency: listBg.transparency };
    if (listBorderW > 0 && listBorderColor) {
      const dash = listStyle.borderStyle === "dashed" ? "dash" : listStyle.borderStyle === "dotted" ? "dot" : "solid";
      shapeOpts.line = { width: listBorderW * 0.75, color: listBorderColor.hex, dashType: dash };
    }
    if (rectRadius !== undefined) shapeOpts.rectRadius = rectRadius;
    ctx.slide.addShape(shapeType as PptxGenJS.ShapeType, shapeOpts);
  }

  const liChildren = Array.from(listNode.children).filter((c) => c.tagName === "LI");
  liChildren.forEach((li, liIdx) => {
    const liStyle = window.getComputedStyle(li);
    if (isHidden(liStyle)) return;
    const liRect = li.getBoundingClientRect();
    if (liRect.width < 0.5 || liRect.height < 0.5) return;
    const liPos = getInches(liRect, ctx.rootRect);

    let align: "left" | "center" | "right" = "left";
    if (liStyle.textAlign === "center") align = "center";
    else if (liStyle.textAlign === "right" || liStyle.textAlign === "end") align = "right";

    let valign: "top" | "middle" | "bottom" = "top";
    if (liStyle.alignItems === "center" || liStyle.verticalAlign === "middle") valign = "middle";
    else if (liStyle.verticalAlign === "bottom") valign = "bottom";

    // PptxGenJS text bodyProp order: margin[0]=left, [1]=right, [2]=bottom, [3]=top.
    const pt = (v: string) => (parseFloat(v || "0") || 0) * 0.75;
    const margin: [number, number, number, number] = [
      pt(liStyle.paddingLeft),
      pt(liStyle.paddingRight),
      pt(liStyle.paddingBottom),
      pt(liStyle.paddingTop),
    ];

    const runs = collectTextRuns(li, liStyle);
    if (runs.length === 0) {
      // No direct text runs — children are likely blockified (grid/flex items,
      // or nested structured markup). Fall back to processing each child as an
      // independent element so the LI's content isn't silently dropped.
      Array.from(li.children).forEach((child) => {
        processElement(child, ctx, 0);
      });
      return;
    }

    // Add bullet metadata to the first text run.
    const listStyleType = liStyle.listStyleType || (isOrdered ? "decimal" : "disc");
    if (isOrdered || listStyleType !== "none") {
      const bullet: Record<string, unknown> = { type: isOrdered ? "number" : "bullet" };
      if (!isOrdered) {
        let code = "2022"; // disc
        if (listStyleType === "circle") code = "25CB";
        else if (listStyleType === "square") code = "25A0";
        bullet.characterCode = code;
      }
      if (isOrdered) {
        // Continue numbering across separate text boxes.
        bullet.numberStartAt = liIdx + 1;
      }
      // Indent = visual offset from list left edge to LI content left edge.
      const parentRect = listNode.getBoundingClientRect();
      const visualIndentPx = liRect.left - parentRect.left;
      if (visualIndentPx > 0) bullet.indent = visualIndentPx * 0.75;
      runs[0].options.bullet = bullet as any;
    }

    ctx.slide.addText(runs as any, {
      x: liPos.x,
      y: liPos.y,
      w: liPos.w,
      h: liPos.h,
      align,
      valign,
      margin,
      wrap: true,
      fit: "shrink",
    });

    // Process any nested lists inside this LI.
    Array.from(li.children).forEach((child) => {
      if (child.tagName === "UL" || child.tagName === "OL") {
        const childRect = child.getBoundingClientRect();
        if (childRect.width < 0.5 || childRect.height < 0.5) return;
        const childStyle = window.getComputedStyle(child);
        if (isHidden(childStyle)) return;
        const childPos = getInches(childRect, ctx.rootRect);
        processList(child as HTMLElement, ctx, childPos, childStyle, rotation);
      }
    });
  });
}

/** Process a TABLE element into a PPTX table. */
function processTable(table: HTMLTableElement, ctx: PptxContext, pos: { x: number; y: number; w: number; h: number }): void {
  const rows = Array.from(table.querySelectorAll("tr"));
  if (rows.length === 0) return;

  // Compute column widths from first row
  const colWidthsPx: number[] = [];
  let maxCols = 0;
  rows.forEach((row) => {
    let cols = 0;
    Array.from(row.children).forEach((cell) => {
      cols += parseInt(cell.getAttribute("colspan") || "1");
    });
    if (cols > maxCols) maxCols = cols;
  });

  // First pass: gather max width per logical column
  const firstRow = rows[0];
  if (firstRow) {
    let colIdx = 0;
    Array.from(firstRow.children).forEach((cell) => {
      const cellRect = cell.getBoundingClientRect();
      const span = parseInt(cell.getAttribute("colspan") || "1");
      const avgW = cellRect.width / span;
      for (let i = 0; i < span && colIdx + i < maxCols; i++) {
        colWidthsPx[colIdx + i] = Math.max(colWidthsPx[colIdx + i] || 0, avgW);
      }
      colIdx += span;
    });
  }

  // Normalize column widths to fit table width
  const totalColW = colWidthsPx.reduce((s, v) => s + (v || 0), 0);
  const colW: number[] = colWidthsPx.map((px) =>
    totalColW > 0 ? (px || 0) / totalColW * pos.w : pos.w / maxCols
  );

  // Row heights
  const rowH: number[] = rows.map((row) => {
    const rowRect = row.getBoundingClientRect();
    return pxToIn(rowRect.height);
  });

  // Build table data
  const tableData: { text: string; options: Record<string, unknown> }[][] = [];
  rows.forEach((row) => {
    const rowData: { text: string; options: Record<string, unknown> }[] = [];
    const cells = Array.from(row.querySelectorAll("td, th"));
    cells.forEach((cell) => {
      const cellStyle = window.getComputedStyle(cell);

      let align: "left" | "center" | "right" = "left";
      if (cellStyle.textAlign === "center") align = "center";
      else if (cellStyle.textAlign === "right" || cellStyle.textAlign === "end") align = "right";

      let valign: "top" | "middle" | "bottom" = "top";
      if (cellStyle.verticalAlign === "middle") valign = "middle";
      else if (cellStyle.verticalAlign === "bottom") valign = "bottom";

      const cellBg = parseColor(cellStyle.backgroundColor, cellStyle.opacity);
      const cellBorderW = parseFloat(cellStyle.borderWidth) || 0;
      const cellBorderColor = parseColor(cellStyle.borderColor, cellStyle.opacity);

      const pt = (v: string) => (parseFloat(v || "0") || 0) * 0.75;
      const margin: [number, number, number, number] = [
        pt(cellStyle.paddingTop),
        pt(cellStyle.paddingRight),
        pt(cellStyle.paddingBottom),
        pt(cellStyle.paddingLeft),
      ];

      const pxSize = parseFloat(cellStyle.fontSize) || 14;
      const ptSize = pxSize * 0.75;

      const opts: Record<string, unknown> = {
        align,
        valign,
        margin,
        wrap: true,
        fontSize: ptSize,
        fontFace: cellStyle.fontFamily?.split(",")[0].replace(/['"]/g, "").trim() || undefined,
        color: cssColorToHex(cellStyle.color) || undefined,
        bold: parseInt(cellStyle.fontWeight) >= 600 || cellStyle.fontWeight === "bold",
        italic: cellStyle.fontStyle === "italic",
        underline: cellStyle.textDecoration.includes("underline"),
        strike: cellStyle.textDecoration.includes("line-through"),
      };

      if (cellBg) {
        opts.fill = { color: cellBg.hex, transparency: cellBg.transparency };
      }

      if (cellBorderW > 0 && cellBorderColor) {
        const dash = cellStyle.borderStyle === "dashed" ? "dash" : cellStyle.borderStyle === "dotted" ? "dot" : "solid";
        const border = { pt: cellBorderW * 0.75, color: cellBorderColor.hex, type: dash };
        opts.border = [border, border, border, border];
      }

      const rowspan = parseInt(cell.getAttribute("rowspan") || "1");
      const colspan = parseInt(cell.getAttribute("colspan") || "1");
      if (rowspan > 1) opts.rowspan = rowspan;
      if (colspan > 1) opts.colspan = colspan;

      rowData.push({ text: cell.textContent || "", options: opts });
    });
    if (rowData.length) tableData.push(rowData);
  });

  const tableStyle = window.getComputedStyle(table);
  const tableBg = parseColor(tableStyle.backgroundColor, tableStyle.opacity);

  ctx.slide.addTable(tableData, {
    x: pos.x,
    y: pos.y,
    w: pos.w,
    colW: colW.length > 0 ? colW : undefined,
    rowH: rowH.length > 0 ? rowH : undefined,
    fill: tableBg ? { color: tableBg.hex, transparency: tableBg.transparency } : undefined,
    border: { type: "none" }, // cell borders handled per-cell
  });
}

/** Main export function. */
export async function exportPptx(deck: DeckState): Promise<void> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const [w, h] = getCanvasSize(deck);
  const deckName = getDeckName(deck);

  // Mount all slide iframes off-screen but rendered.
  const host = document.createElement("div");
  host.style.cssText =
    "position:fixed;left:-20000px;top:0;width:0;height:0;overflow:visible;pointer-events:none;";
  host.setAttribute("aria-hidden", "true");
  document.body.appendChild(host);

  const frames: HTMLIFrameElement[] = entries.map(([, html]) => {
    const f = document.createElement("iframe");
    f.width = String(w);
    f.height = String(h);
    f.style.width = `${w}px`;
    f.style.height = `${h}px`;
    f.style.border = "0";
    f.srcdoc = html;
    host.appendChild(f);
    return f;
  });

  try {
    await Promise.all(frames.map((f) => waitForIframeReady(f)));

    const pptx = new PptxGenJS();

    // Define custom layout matching exact canvas dimensions — no letterboxing.
    const widthIn = w / PPI;
    const heightIn = h / PPI;
    pptx.defineLayout({ name: "CUSTOM", width: widthIn, height: heightIn });
    pptx.layout = "CUSTOM";

    let slidesAdded = 0;
    frames.forEach((f, i) => {
      const doc = f.contentDocument;
      const body = doc?.body ?? doc?.documentElement;
      if (!body || body.childElementCount === 0) {
        console.warn(`PPTX export: skipping slide ${entries[i][0] + 1} (no accessible body)`);
        return;
      }

      const slide = pptx.addSlide();
      slidesAdded++;

      // Find the slide root element — the first child that looks like the slide container.
      // Slide HTML has a root div at exactly canvas size; body margins would offset it.
      let rootEl: Element = body;
      for (const child of Array.from(body.children)) {
        const r = child.getBoundingClientRect();
        // Heuristic: child that is at least 80% of canvas size and is a DIV
        if (child.tagName === "DIV" && r.width >= w * 0.8 && r.height >= h * 0.8) {
          rootEl = child;
          break;
        }
      }
      const rootRect = rootEl.getBoundingClientRect();

      // Emit the root element's own background / border / radius so it's preserved.
      // We do this directly (not via processElement) to avoid double-processing children.
      if (rootEl !== body) {
        const rootStyle = window.getComputedStyle(rootEl);
        const rootBg = parseColor(rootStyle.backgroundColor, rootStyle.opacity);
        const rootBorderTopW = parseFloat(rootStyle.borderTopWidth) || 0;
        const rootBorderRightW = parseFloat(rootStyle.borderRightWidth) || 0;
        const rootBorderBottomW = parseFloat(rootStyle.borderBottomWidth) || 0;
        const rootBorderLeftW = parseFloat(rootStyle.borderLeftWidth) || 0;
        const rootMaxBorderW = Math.max(rootBorderTopW, rootBorderRightW, rootBorderBottomW, rootBorderLeftW);
        const rootIsUniformBorder =
          rootMaxBorderW > 0 &&
          rootBorderTopW === rootMaxBorderW &&
          rootBorderRightW === rootMaxBorderW &&
          rootBorderBottomW === rootMaxBorderW &&
          rootBorderLeftW === rootMaxBorderW;
        const rootBorderColor = parseColor(rootStyle.borderTopColor, rootStyle.opacity);
        if (rootBg || (rootIsUniformBorder && rootBorderColor)) {
          const shapeType = getShapeType(rootRect.width, rootRect.height, rootStyle.borderRadius);
          const rectRadius = shapeType === "roundRect"
            ? getRectRadius(rootRect.width, rootRect.height, rootStyle.borderRadius)
            : undefined;
          const shapeOpts: Record<string, unknown> = {
            x: 0, y: 0, w: pxToIn(rootRect.width), h: pxToIn(rootRect.height),
          };
          if (rootBg) shapeOpts.fill = { color: rootBg.hex, transparency: rootBg.transparency };
          if (rootIsUniformBorder && rootBorderColor) {
            const dash = rootStyle.borderStyle === "dashed" ? "dash" : rootStyle.borderStyle === "dotted" ? "dot" : "solid";
            shapeOpts.line = { width: rootMaxBorderW * 0.75, color: rootBorderColor.hex, dashType: dash };
          }
          if (rectRadius !== undefined) shapeOpts.rectRadius = rectRadius;
          slide.addShape(shapeType as PptxGenJS.ShapeType, shapeOpts);
        }
      }

      // Process children of the root element.
      Array.from(rootEl.children).forEach((child) => {
        processElement(child, { slide, rootRect, pptx });
      });
    });

    if (slidesAdded === 0) {
      throw new Error("PPTX export failed: no slides could be rendered. The slide HTML may not have loaded correctly.");
    }

    const blob = await pptx.write({ outputType: "blob" }) as Blob;
    downloadBlob(blob, `${deckName}.pptx`);
  } finally {
    host.remove();
  }
}
