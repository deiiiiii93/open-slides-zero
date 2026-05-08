// Client-side exporters for deck output.
//
// Three formats:
//   exportHtmlSingle  — one self-contained .html file with iframes per slide
//   exportHtmlZip     — .zip with slide_NN.html files + a small index.html
//   exportPngZip      — .zip with slide_NN.png files + a small index.html
//   exportPptx        — editable .pptx via pptxgenjs v4 walking each slide's DOM

import JSZip from "jszip";
import PptxGenJS from "pptxgenjs";
import type { DeckState } from "./api";
import { normalizeImagePlaceholders } from "./imagePlaceholders";
import { runtimeConfigHeaders } from "./runtimeConfig";

// Keep in sync with DeckCanvas.tsx:17-21. Duplicated rather than shared because
// it's four lines and importing DeckCanvas just for a constant is overkill.
const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

// PPI constant: 96 CSS pixels per inch (standard devtools assumption).
const PPI = 96;
const API_BASE = "/api";
const PNG_EXPORT_SCALE = 2;

export type ExportArtifact = {
  filename: string;
  blob: Blob;
};

export type ExportProgressEvent =
  | { kind: "phase"; label: string }
  | { kind: "info"; text: string }
  | { kind: "resource-start"; label: string }
  | { kind: "resource-ok"; label: string; detail?: string }
  | { kind: "resource-fail"; label: string; reason: string }
  | { kind: "done"; detail?: string };

export type ExportProgress = (event: ExportProgressEvent) => void;

type ExportNameOptions = {
  filenameBase?: string;
  useBaseSlides?: boolean;
};

export type ExportablePlaygroundLane = {
  lane_id: string;
  lane_thread_id: string;
  creator_prompt: string;
  state: DeckState | null;
};

type PackageIndexRow = {
  laneId: string;
  laneName: string;
  stage: string;
  prompt: string;
  singleHtmlHref: string;
  slideIndexHref: string;
  pngsHref: string;
  pptxHref: string;
  fontPackageHref?: string;
};

type FontResourceKind = "stylesheet" | "import" | "font-file";

type FontResource = {
  kind: FontResourceKind;
  url: string;
  slides: number[];
};

type FontPackageInfo = {
  resources: FontResource[];
  families: string[];
};

type PackagedFontAsset = {
  url: string;
  path?: string;
  status: "saved" | "failed";
  error?: string;
};

type PackagedCssAsset = PackagedFontAsset & {
  kind: "stylesheet" | "import";
};

// ---------------- Shared helpers ----------------

function getCanvasSize(deck: DeckState): [number, number] {
  const ar = (deck.values?.aspect_ratio as string) ?? "16:9";
  return CANVAS[ar] ?? CANVAS["16:9"];
}

function sanitizeFileName(name: string): string {
  return name.replace(/[\\/:*?"<>|\x00-\x1f]+/g, "_").trim() || "deck";
}

function getDeckName(deck: DeckState, options: ExportNameOptions = {}): string {
  if (options.filenameBase?.trim()) return sanitizeFileName(options.filenameBase);
  const raw = (deck.values?.deck_name as string | undefined) || `deck-${deck.thread_id.slice(0, 6)}`;
  return sanitizeFileName(raw);
}

function getSlideEntries(deck: DeckState): Array<[number, string]> {
  const slides = (deck.values?.html_slides ?? {}) as Record<string | number, string>;
  return Object.entries(slides)
    .map(([k, v]) => [Number(k), typeof v === "string" ? normalizeImagePlaceholders(v) : v] as [number, string])
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

function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return utf8[1].trim().replace(/^"|"$/g, "");
    }
  }
  const ascii = header.match(/filename=(?:"([^"]+)"|([^;]+))/i);
  return (ascii?.[1] || ascii?.[2] || "").trim() || null;
}

const GENERIC_FONT_FAMILIES = new Set([
  "serif",
  "sans-serif",
  "monospace",
  "cursive",
  "fantasy",
  "system-ui",
  "ui-serif",
  "ui-sans-serif",
  "ui-monospace",
  "ui-rounded",
  "-apple-system",
  "blinkmacsystemfont",
]);

function uniqueSorted(values: Iterable<string>): string[] {
  return Array.from(new Set(Array.from(values).filter(Boolean))).sort((a, b) => a.localeCompare(b));
}

function cleanCssString(value: string): string {
  return value.trim().replace(/^['"]|['"]$/g, "").trim();
}

function normalizeExternalUrl(raw: string, base = window.location.href): string | null {
  const value = cleanCssString(raw);
  if (!value || value.startsWith("data:") || value.startsWith("#")) return null;
  try {
    const url = new URL(value, base);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch {
    return null;
  }
  return null;
}

function isLikelyFontFileUrl(url: string): boolean {
  return /\.(woff2?|ttf|otf|eot)(?:[?#]|$)/i.test(url);
}

function fontFileName(url: string, index: number): string {
  try {
    const parsed = new URL(url);
    const name = sanitizeFileName(decodeURIComponent(parsed.pathname.split("/").pop() || ""));
    if (name && /\.[a-z0-9]+$/i.test(name)) return `${pad(index, 3)}-${name}`;
  } catch {
    // Fall through to extension fallback.
  }
  const ext = url.match(/\.(woff2?|ttf|otf|eot)(?:[?#]|$)/i)?.[1] || "woff2";
  return `${pad(index, 3)}-font.${ext}`;
}

function collectFontFamiliesFromHtml(html: string): string[] {
  const families: string[] = [];
  for (const match of html.matchAll(/font-family\s*:\s*([^;}]+)/gi)) {
    const declaration = match[1].replace(/!important/gi, "");
    for (const part of declaration.split(",")) {
      const family = cleanCssString(part);
      if (!family) continue;
      const normalized = family.toLowerCase();
      if (GENERIC_FONT_FAMILIES.has(normalized)) continue;
      if (normalized.startsWith("var(")) continue;
      families.push(family);
    }
  }
  return families;
}

function collectFontPackageInfo(entries: Array<[number, string]>): FontPackageInfo {
  const parser = new DOMParser();
  const resourceMap = new Map<string, FontResource>();
  const familySet = new Set<string>();

  function addResource(kind: FontResourceKind, rawUrl: string, slideIdx: number, base?: string) {
    const url = normalizeExternalUrl(rawUrl, base);
    if (!url) return;
    const key = `${kind}:${url}`;
    const existing = resourceMap.get(key);
    if (existing) {
      if (!existing.slides.includes(slideIdx + 1)) existing.slides.push(slideIdx + 1);
      return;
    }
    resourceMap.set(key, { kind, url, slides: [slideIdx + 1] });
  }

  for (const [slideIdx, html] of entries) {
    for (const family of collectFontFamiliesFromHtml(html)) familySet.add(family);

    const doc = parser.parseFromString(html, "text/html");
    doc.querySelectorAll("link[href]").forEach((link) => {
      const href = link.getAttribute("href") || "";
      const relTokens = (link.getAttribute("rel") || "").toLowerCase().split(/\s+/).filter(Boolean);
      const as = (link.getAttribute("as") || "").toLowerCase();
      const isStylesheet = relTokens.includes("stylesheet");
      const isPreloadStyle = relTokens.includes("preload") && as === "style";
      if (isStylesheet || isPreloadStyle) {
        addResource("stylesheet", href, slideIdx);
      }
    });

    for (const match of html.matchAll(/@import\s+(?:url\(\s*)?["']?([^"')\s;]+)["']?\s*\)?/gi)) {
      addResource("import", match[1], slideIdx);
    }

    for (const match of html.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
      const url = normalizeExternalUrl(match[1]);
      if (url && isLikelyFontFileUrl(url)) addResource("font-file", url, slideIdx);
    }
  }

  const resources = Array.from(resourceMap.values()).map((resource) => ({
    ...resource,
    slides: [...resource.slides].sort((a, b) => a - b),
  }));
  resources.sort((a, b) => a.url.localeCompare(b.url) || a.kind.localeCompare(b.kind));
  return { resources, families: uniqueSorted(familySet) };
}

function hasFontPackage(info: FontPackageInfo): boolean {
  return info.resources.length > 0;
}

function fetchWithTimeout(url: string, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { signal: controller.signal }).finally(() => window.clearTimeout(timer));
}

function classifyError(exc: unknown): string {
  if (exc instanceof DOMException && exc.name === "AbortError") return "timeout";
  if (exc instanceof TypeError && /failed to fetch|networkerror/i.test(exc.message)) return "CORS";
  const s = String(exc);
  const httpMatch = s.match(/\b(\d{3})(?:\s+([A-Za-z][A-Za-z ]+))?\b/);
  if (httpMatch) return httpMatch[2] ? `${httpMatch[1]} ${httpMatch[2].trim()}` : httpMatch[1];
  return s.length > 80 ? `${s.slice(0, 77)}…` : s;
}

function shortenUrl(url: string, max = 60): string {
  if (url.length <= max) return url;
  try {
    const u = new URL(url);
    const tail = u.pathname.split("/").filter(Boolean).pop() ?? "";
    const compact = `${u.host}/…/${tail}`;
    return compact.length <= max ? compact : `${compact.slice(0, max - 1)}…`;
  } catch {
    return `${url.slice(0, max - 1)}…`;
  }
}

function cssFontUrls(css: string, baseUrl: string): string[] {
  const urls: string[] = [];
  for (const match of css.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)) {
    const url = normalizeExternalUrl(match[1], baseUrl);
    if (url && isLikelyFontFileUrl(url)) urls.push(url);
  }
  return uniqueSorted(urls);
}

function buildFontReadme(deckName: string, info: FontPackageInfo, cssAssets: PackagedCssAsset[], fontAssets: PackagedFontAsset[]): string {
  const lines = [
    `# ${deckName} font package`,
    "",
    "This folder was generated because the slide HTML references external font or stylesheet resources.",
    "The editable PPTX uses font family names, but PowerPoint may substitute fonts that are not installed locally.",
    "",
    "Recommended workflow:",
    "1. Install the saved font files in `files/` when present.",
    "2. Open `font-links.html` to review the original web resources and local CSS copies.",
    "3. Open the PPTX after installing fonts for the closest visual match.",
    "",
  ];
  if (info.families.length) {
    lines.push("## Font families found in slide CSS", "", ...info.families.map((family) => `- ${family}`), "");
  }
  lines.push("## External resources", "");
  for (const resource of info.resources) {
    lines.push(`- ${resource.kind} on slide(s) ${resource.slides.join(", ")}: ${resource.url}`);
  }
  lines.push("", "## Packaged CSS", "");
  if (cssAssets.length) {
    for (const asset of cssAssets) {
      lines.push(`- ${asset.status}: ${asset.url}${asset.path ? ` -> ${asset.path}` : ""}${asset.error ? ` (${asset.error})` : ""}`);
    }
  } else {
    lines.push("- None saved.");
  }
  lines.push("", "## Packaged font files", "");
  if (fontAssets.length) {
    for (const asset of fontAssets) {
      lines.push(`- ${asset.status}: ${asset.url}${asset.path ? ` -> ${asset.path}` : ""}${asset.error ? ` (${asset.error})` : ""}`);
    }
  } else {
    lines.push("- None saved. Some providers block browser downloads; use the external resource links above.");
  }
  lines.push("");
  return lines.join("\n");
}

function buildFontLinksHtml(deckName: string, info: FontPackageInfo, cssAssets: PackagedCssAsset[]): string {
  const localLinks = cssAssets
    .filter((asset) => asset.status === "saved" && asset.path)
    .map((asset) => `  <link rel="stylesheet" href="${escapeAttr(asset.path!.replace(/^.*?font-package\//, ""))}" />`)
    .join("\n");
  const externalLinks = info.resources
    .filter((resource) => resource.kind !== "font-file")
    .map((resource) => `  <link rel="stylesheet" href="${escapeAttr(resource.url)}" />`)
    .join("\n");
  const families = info.families
    .map((family) => `<p style="font-family:${escapeAttr(JSON.stringify(family))}, sans-serif">${escapeText(family)} - The quick brown fox jumps over 1234567890.</p>`)
    .join("\n");
  const resources = info.resources
    .map((resource) => `<li><code>${escapeText(resource.kind)}</code> slide(s) ${escapeText(resource.slides.join(", "))}: <a href="${escapeAttr(resource.url)}">${escapeText(resource.url)}</a></li>`)
    .join("\n");
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${escapeText(deckName)} font package</title>
${localLinks}
${externalLinks}
  <style>
    body { margin: 0; padding: 24px; color: #1c1c1e; font-family: Georgia, serif; line-height: 1.45; }
    h1 { margin: 0 0 12px; }
    code { color: #5c5852; }
    p { font-size: 22px; margin: 14px 0; }
  </style>
</head>
<body>
  <h1>${escapeText(deckName)} font package</h1>
  <p style="font-size:14px">Install saved files from <code>files/</code> when present, then open the PPTX.</p>
  <h2>Font family samples</h2>
  ${families || "<p>No explicit font-family declarations found.</p>"}
  <h2>External resources</h2>
  <ul>${resources}</ul>
</body>
</html>
`;
}

async function addFontPackageToZip(
  zip: JSZip,
  info: FontPackageInfo,
  deckName: string,
  folder = "",
  onProgress?: ExportProgress,
): Promise<string | null> {
  if (!hasFontPackage(info)) return null;

  const base = `${folder}font-package/`;
  const cssAssets: PackagedCssAsset[] = [];
  const fontAssets = new Map<string, PackagedFontAsset>();

  async function saveFont(url: string, preferredIndex: number): Promise<string | null> {
    const existing = fontAssets.get(url);
    if (existing) return existing.path || null;
    const path = `${base}files/${fontFileName(url, preferredIndex)}`;
    const label = `font-file ${shortenUrl(url)}`;
    onProgress?.({ kind: "resource-start", label });
    try {
      const response = await fetchWithTimeout(url);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const blob = await response.blob();
      zip.file(path, blob);
      fontAssets.set(url, { url, path, status: "saved" });
      onProgress?.({ kind: "resource-ok", label, detail: `${Math.max(1, Math.round(blob.size / 1024))} KB` });
      return path;
    } catch (exc) {
      fontAssets.set(url, { url, status: "failed", error: String(exc) });
      onProgress?.({ kind: "resource-fail", label, reason: classifyError(exc) });
      return null;
    }
  }

  let cssIndex = 0;
  let fontIndex = 0;
  for (const resource of info.resources) {
    if (resource.kind === "font-file") {
      fontIndex += 1;
      await saveFont(resource.url, fontIndex);
      continue;
    }

    cssIndex += 1;
    const cssPath = `${base}css/${pad(cssIndex, 3)}-${resource.kind}.css`;
    const cssLabel = `${resource.kind} ${shortenUrl(resource.url)}`;
    onProgress?.({ kind: "resource-start", label: cssLabel });
    try {
      const response = await fetchWithTimeout(resource.url);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      let css = await response.text();
      for (const fontUrl of cssFontUrls(css, resource.url)) {
        fontIndex += 1;
        const fontPath = await saveFont(fontUrl, fontIndex);
        if (fontPath) {
          css = css.split(fontUrl).join(`../files/${fontPath.split("/").pop()}`);
        }
      }
      zip.file(cssPath, css);
      cssAssets.push({ kind: resource.kind, url: resource.url, path: cssPath, status: "saved" });
      onProgress?.({ kind: "resource-ok", label: cssLabel, detail: `${Math.max(1, Math.round(css.length / 1024))} KB` });
    } catch (exc) {
      cssAssets.push({ kind: resource.kind, url: resource.url, status: "failed", error: String(exc) });
      onProgress?.({ kind: "resource-fail", label: cssLabel, reason: classifyError(exc) });
    }
  }

  const fontAssetList = Array.from(fontAssets.values());
  const manifest = {
    deck_name: deckName,
    resources: info.resources,
    font_families: info.families,
    packaged_css: cssAssets,
    packaged_font_files: fontAssetList,
  };
  zip.file(`${base}font-manifest.json`, JSON.stringify(manifest, null, 2));
  zip.file(`${base}README.md`, buildFontReadme(deckName, info, cssAssets, fontAssetList));
  zip.file(`${base}font-links.html`, buildFontLinksHtml(deckName, info, cssAssets));
  return `${base}font-links.html`;
}

async function responseErrorDetail(res: Response): Promise<string> {
  const text = await res.text();
  if (!text) return `${res.status} ${res.statusText}`;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (parsed.detail) return JSON.stringify(parsed.detail);
  } catch {
    // Fall through to raw response text.
  }
  return text;
}

function isPptxEmbeddableImageSrc(src: string): boolean {
  if (src.startsWith("data:image/") || src.startsWith("blob:")) return true;
  try {
    const url = new URL(src, window.location.href);
    return url.origin === window.location.origin;
  } catch {
    return false;
  }
}

function pptxImageSrc(src: string): string | null {
  if (isPptxEmbeddableImageSrc(src)) return src;
  try {
    const url = new URL(src, window.location.href);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return `/api/images/proxy?url=${encodeURIComponent(url.href)}`;
    }
  } catch {
    return null;
  }
  return null;
}

function renderUnavailableImage(
  ctx: PptxContext,
  pos: { x: number; y: number; w: number; h: number },
  rotation: number | undefined,
): void {
  ctx.slide.addShape("rect" as PptxGenJS.ShapeType, {
    x: pos.x,
    y: pos.y,
    w: pos.w,
    h: pos.h,
    rotate: rotation,
    fill: { color: "F4F1EA", transparency: 8 },
    line: { color: "B7B0A4", width: 0.75, dashType: "dash" },
  });
  ctx.slide.addText("Image unavailable", {
    x: pos.x,
    y: pos.y + Math.max(0, pos.h / 2 - 0.14),
    w: pos.w,
    h: Math.min(0.32, pos.h),
    rotate: rotation,
    align: "center",
    valign: "middle",
    margin: 0,
    fontSize: 9,
    color: "6E665C",
    fit: "shrink",
  });
}

// ---------------- HTML (single file) ----------------

function buildHtmlSingleDocument(deck: DeckState, options: ExportNameOptions = {}): { deckName: string; doc: string } {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const [w, h] = getCanvasSize(deck);
  const deckName = getDeckName(deck, options);

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
    body { margin: 0; padding: 24px; background: #f5f3ee; font-family: Georgia, serif; color: #1c1c1e; }
    h1 { margin: 0 0 16px; }
    .slide { margin: 0 0 24px; }
    .slide h2 { margin: 0 0 6px; font-size: 14px; font-weight: 600; color: #5c5852; }
    iframe { border: 1px solid #0a0a0a; background: #f5f3ee; display: block; }
  </style>
</head>
<body>
  <h1>${escapeText(deckName)}</h1>
${slides}
</body>
</html>
`;

  return { deckName, doc };
}

export function buildHtmlSingleArtifact(deck: DeckState, options: ExportNameOptions = {}): ExportArtifact {
  const { deckName, doc } = buildHtmlSingleDocument(deck, options);
  return {
    filename: `${deckName}.html`,
    blob: new Blob([doc], { type: "text/html;charset=utf-8" }),
  };
}

export async function exportHtmlSingle(deck: DeckState): Promise<void> {
  const artifact = buildHtmlSingleArtifact(deck);
  downloadBlob(artifact.blob, artifact.filename);
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
    nav { background: #f5f3ee; border-right: 1px solid #0a0a0a; padding: 16px; overflow-y: auto; }
    nav h1 { font-size: 16px; margin: 0 0 12px; }
    nav ul { list-style: none; padding: 0; margin: 0; }
    nav a { display: block; padding: 6px 8px; border-radius: 0; color: #1c1c1e; text-decoration: none; font-size: 13px; }
    nav a:hover { background: #e8e3d8; }
    main { padding: 16px; }
    iframe { width: ${w}px; height: ${h}px; border: 1px solid #0a0a0a; background: #f5f3ee; }
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

function addHtmlSlidesToZip(
  zip: JSZip,
  entries: Array<[number, string]>,
  deckName: string,
  canvas: [number, number],
  folder = "",
): void {
  for (const [idx, html] of entries) {
    zip.file(`${folder}slide_${pad(idx + 1)}.html`, html);
  }
  zip.file(`${folder}index.html`, buildZipIndexHtml(entries, deckName, canvas));
}

export async function buildHtmlZipArtifact(deck: DeckState, options: ExportNameOptions = {}): Promise<ExportArtifact> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const deckName = getDeckName(deck, options);
  const canvas = getCanvasSize(deck);

  const zip = new JSZip();
  addHtmlSlidesToZip(zip, entries, deckName, canvas);

  const blob = await zip.generateAsync({ type: "blob" });
  return { filename: `${deckName}.zip`, blob };
}

export async function exportHtmlZip(deck: DeckState): Promise<void> {
  const artifact = await buildHtmlZipArtifact(deck);
  downloadBlob(artifact.blob, artifact.filename);
}

// ---------------- PNG (zip of screenshot slides) ----------------

export async function buildPngZipArtifact(deck: DeckState, options: ExportNameOptions = {}): Promise<ExportArtifact> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const deckName = getDeckName(deck, options);
  const aspectRatio = (deck.values?.aspect_ratio as string) ?? "16:9";

  const res = await fetch(`${API_BASE}/decks/${encodeURIComponent(deck.thread_id)}/exports/pngs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...runtimeConfigHeaders() },
    credentials: "same-origin",
    body: JSON.stringify({
      deck_name: deckName,
      aspect_ratio: aspectRatio,
      scale: PNG_EXPORT_SCALE,
      base_url: window.location.origin,
      use_base_slides: Boolean(options.useBaseSlides),
    }),
  });

  if (!res.ok) {
    throw new Error(`PNG export failed: ${await responseErrorDetail(res)}`);
  }

  const filename = filenameFromContentDisposition(res.headers.get("Content-Disposition")) ?? `${deckName}-pngs.zip`;
  return { filename, blob: await res.blob() };
}

export async function exportPngZip(deck: DeckState, options: ExportNameOptions = {}): Promise<void> {
  const artifact = await buildPngZipArtifact(deck, options);
  downloadBlob(artifact.blob, artifact.filename);
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
  try {
    const images = Array.from(f.contentDocument?.images ?? []);
    images.forEach((img) => {
      img.loading = "eager";
      img.decoding = "sync";
    });
    await Promise.race([
      Promise.all(images.map((img) => {
        if (img.complete) return true;
        return new Promise((resolve) => {
          img.addEventListener("load", () => resolve(true), { once: true });
          img.addEventListener("error", () => resolve(true), { once: true });
        });
      })),
      new Promise((resolve) => setTimeout(resolve, Math.max(0, deadline - Date.now()))),
    ]);
  } catch {
    // Keep PPTX export best-effort if a browser refuses image inspection.
  }
}

// --- Color helpers ---

type PptxFillColor = { hex: string; transparency?: number };
type RgbaColor = { r: number; g: number; b: number; a: number };

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

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

function parseColorChannel(token: string): number {
  const trimmed = token.trim();
  const value = trimmed.endsWith("%") ? (parseFloat(trimmed) / 100) * 255 : parseFloat(trimmed);
  return clamp(Math.round(Number.isFinite(value) ? value : 0), 0, 255);
}

function parseAlphaChannel(token: string | undefined): number {
  if (!token) return 1;
  const trimmed = token.trim();
  const value = trimmed.endsWith("%") ? parseFloat(trimmed) / 100 : parseFloat(trimmed);
  return clamp(Number.isFinite(value) ? value : 1, 0, 1);
}

function parseCssRgba(colorStr: string): RgbaColor | null {
  const color = colorStr.trim().toLowerCase();
  if (!color || color === "inherit" || color === "initial" || color === "none") return null;
  if (color === "transparent") return { r: 0, g: 0, b: 0, a: 0 };
  if (color === "black") return { r: 0, g: 0, b: 0, a: 1 };
  if (color === "white") return { r: 255, g: 255, b: 255, a: 1 };

  const hex = color.match(/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i);
  if (hex) {
    const raw = hex[1];
    const expand = (value: string) => value.length === 1 ? value + value : value;
    const r = parseInt(expand(raw.length <= 4 ? raw[0] : raw.slice(0, 2)), 16);
    const g = parseInt(expand(raw.length <= 4 ? raw[1] : raw.slice(2, 4)), 16);
    const b = parseInt(expand(raw.length <= 4 ? raw[2] : raw.slice(4, 6)), 16);
    const a = raw.length === 4
      ? parseInt(expand(raw[3]), 16) / 255
      : raw.length === 8
        ? parseInt(raw.slice(6, 8), 16) / 255
        : 1;
    return { r, g, b, a };
  }

  const fn = color.match(/^rgba?\((.*)\)$/i);
  if (!fn) return null;

  const body = fn[1].trim();
  let channels: string[] = [];
  let alpha: string | undefined;
  if (body.includes(",")) {
    channels = body.split(",").map((part) => part.trim()).filter(Boolean);
    alpha = channels[3];
  } else {
    const [rgbPart, alphaPart] = body.split("/").map((part) => part.trim());
    channels = rgbPart.split(/\s+/).filter(Boolean);
    alpha = alphaPart ?? channels[3];
  }
  if (channels.length < 3) return null;

  return {
    r: parseColorChannel(channels[0]),
    g: parseColorChannel(channels[1]),
    b: parseColorChannel(channels[2]),
    a: parseAlphaChannel(alpha),
  };
}

function rgbaToHex(color: RgbaColor): string {
  const toHex = (n: number) => clamp(Math.round(n), 0, 255).toString(16).padStart(2, "0");
  return `${toHex(color.r)}${toHex(color.g)}${toHex(color.b)}`.toUpperCase();
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

function fillFromRgba(color: RgbaColor, cssOpacity = "1"): PptxFillColor | null {
  const elementOpacity = parseFloat(cssOpacity);
  const alpha = color.a * (Number.isFinite(elementOpacity) ? elementOpacity : 1);
  if (alpha <= 0.01) return null;
  return {
    hex: rgbaToHex(color),
    transparency: alpha >= 0.995 ? undefined : Math.round((1 - alpha) * 100),
  };
}

function parseColor(colorStr: string, opacityStr = "1"): PptxFillColor | null {
  const rgba = parseCssRgba(colorStr);
  if (!rgba) {
    const hex = cssColorToHex(colorStr);
    return hex ? { hex } : null;
  }
  return fillFromRgba(rgba, opacityStr);
}

function splitCssTopLevel(input: string, separator = ","): string[] {
  const parts: string[] = [];
  let start = 0;
  let depth = 0;
  let quote: string | null = null;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quote) {
      if (ch === quote && input[i - 1] !== "\\") quote = null;
      continue;
    }
    if (ch === "\"" || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === "(") depth++;
    else if (ch === ")") depth = Math.max(0, depth - 1);
    else if (ch === separator && depth === 0) {
      parts.push(input.slice(start, i).trim());
      start = i + 1;
    }
  }
  parts.push(input.slice(start).trim());
  return parts.filter(Boolean);
}

function extractLeadingCssColor(stop: string): string | null {
  const trimmed = stop.trim();
  const fn = trimmed.match(/^(rgba?\([^)]*\))/i);
  if (fn) return fn[1];
  const hex = trimmed.match(/^(#[0-9a-f]{3,8})\b/i);
  if (hex) return hex[1];
  const named = trimmed.match(/^(transparent|black|white)\b/i);
  return named ? named[1] : null;
}

function isSparseRuleGradientLayer(layer: string): boolean {
  const match = layer.match(/^(?:repeating-)?(?:linear|radial)-gradient\((.*)\)$/i);
  if (!match) return false;
  const stops = splitCssTopLevel(match[1])
    .map((stop) => ({ stop, color: extractLeadingCssColor(stop) }))
    .filter((entry) => entry.color !== null);
  if (stops.length < 4) return false;
  const hasTransparent = stops.some((entry) => parseCssRgba(entry.color || "")?.a === 0);
  const hasVisible = stops.some((entry) => {
    const color = parseCssRgba(entry.color || "");
    return color !== null && color.a > 0.01;
  });
  if (!hasTransparent || !hasVisible) return false;

  const visiblePositions = stops
    .filter((entry) => {
      const color = parseCssRgba(entry.color || "");
      return color !== null && color.a > 0.01;
    })
    .flatMap((entry) => {
      const rest = entry.stop.slice((entry.color || "").length).trim();
      return rest.match(/(?:calc\([^)]+\)|-?\d+(?:\.\d+)?(?:px|%))/g) || [];
    });
  if (visiblePositions.length < 2) return false;

  const pxPositions = visiblePositions
    .filter((pos) => /^-?\d+(?:\.\d+)?px$/i.test(pos))
    .map((pos) => parseFloat(pos));
  if (pxPositions.length >= 2 && Math.max(...pxPositions) - Math.min(...pxPositions) <= 3) return true;

  const percentPositions = visiblePositions
    .filter((pos) => /^-?\d+(?:\.\d+)?%$/i.test(pos))
    .map((pos) => parseFloat(pos));
  if (percentPositions.length >= 2 && Math.max(...percentPositions) - Math.min(...percentPositions) <= 1) return true;

  return visiblePositions.some((pos) => pos.startsWith("calc("));
}

function parseGradientFill(backgroundImage: string, opacityStr = "1"): PptxFillColor | null {
  if (!backgroundImage || backgroundImage === "none" || !backgroundImage.toLowerCase().includes("gradient(")) return null;

  const layers = splitCssTopLevel(backgroundImage).filter((layer) => !isSparseRuleGradientLayer(layer));
  if (layers.length === 0) return null;
  const colors: RgbaColor[] = [];
  for (const layer of layers) {
    const match = layer.match(/^(?:repeating-)?(?:linear|radial)-gradient\((.*)\)$/i);
    if (!match) continue;
    const stops = splitCssTopLevel(match[1]);
    stops.forEach((stop) => {
      const colorToken = extractLeadingCssColor(stop);
      if (!colorToken) return;
      const parsed = parseCssRgba(colorToken);
      if (parsed) colors.push(parsed);
    });
  }
  if (colors.length === 0) return null;

  const baseOpacity = parseFloat(opacityStr);
  const elementOpacity = Number.isFinite(baseOpacity) ? baseOpacity : 1;
  const avgAlpha = colors.reduce((sum, color) => sum + color.a, 0) / colors.length;
  const effectiveAlpha = avgAlpha * elementOpacity;
  if (effectiveAlpha <= 0.015) return null;

  const weightedAlpha = colors.reduce((sum, color) => sum + color.a, 0);
  const weighted = weightedAlpha > 0
    ? colors.reduce((acc, color) => {
        acc.r += color.r * color.a;
        acc.g += color.g * color.a;
        acc.b += color.b * color.a;
        return acc;
      }, { r: 0, g: 0, b: 0 })
    : colors.reduce((acc, color) => {
        acc.r += color.r;
        acc.g += color.g;
        acc.b += color.b;
        return acc;
      }, { r: 0, g: 0, b: 0 });
  const denominator = weightedAlpha > 0 ? weightedAlpha : colors.length;
  return fillFromRgba({
    r: weighted.r / denominator,
    g: weighted.g / denominator,
    b: weighted.b / denominator,
    a: effectiveAlpha,
  });
}

function parseBackgroundFill(style: CSSStyleDeclaration): PptxFillColor | null {
  const gradientFill = parseGradientFill(style.backgroundImage, style.opacity);
  if (gradientFill) return gradientFill;
  return parseColor(style.backgroundColor, style.opacity);
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
    if (!s || s.content === "none" || s.content === "normal") return false;
    const bg = parseBackgroundFill(s);
    const border = parseColor(s.borderTopColor, s.opacity);
    const hasVisual =
      bg !== null ||
      parseFloat(s.width) > 0 ||
      parseFloat(s.height) > 0 ||
      (parseFloat(s.borderTopWidth) > 0 && border !== null);
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
    const bg = parseBackgroundFill(s);
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

function intrinsicImageSizeIn(img: HTMLImageElement): { w: number; h: number } | null {
  if (img.naturalWidth > 0 && img.naturalHeight > 0) {
    return { w: pxToIn(img.naturalWidth), h: pxToIn(img.naturalHeight) };
  }
  return null;
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
      const source = pptxImageSrc(img.src);
      if (!source) {
        console.warn(`PPTX export: skipping external image that cannot be embedded: ${img.src}`);
        renderUnavailableImage(ctx, pos, rotation);
        return;
      }
      const opacity = parseFloat(style.opacity);
      const objectFit = img.dataset.imageFit || style.objectFit;
      const sizingType = objectFit === "cover" || objectFit === "contain" ? objectFit : null;
      const intrinsicSize = sizingType ? intrinsicImageSizeIn(img) : null;
      const imgOpts: Record<string, unknown> = {
        x: pos.x,
        y: pos.y,
        w: intrinsicSize?.w ?? pos.w,
        h: intrinsicSize?.h ?? pos.h,
        rotate: rotation,
        transparency: !isNaN(opacity) && opacity < 1 ? Math.round((1 - opacity) * 100) : undefined,
      };
      if (source.startsWith("data:image/")) {
        imgOpts.data = source;
      } else {
        imgOpts.path = source;
      }
      if (sizingType) {
        imgOpts.sizing = { type: sizingType, w: pos.w, h: pos.h };
      }
      try {
        ctx.slide.addImage(imgOpts);
      } catch (exc) {
        console.warn("PPTX export: image could not be embedded", exc);
        renderUnavailableImage(ctx, pos, rotation);
      }
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
  const bgColor = parseBackgroundFill(style);
  const hasBgImage = style.backgroundImage && style.backgroundImage !== "none";
  const hasBg = bgColor !== null || hasBgImage;
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
  const listBg = parseBackgroundFill(listStyle);
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

      const cellBg = parseBackgroundFill(cellStyle);
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
  const tableBg = parseBackgroundFill(tableStyle);

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

export async function buildPptxArtifact(
  deck: DeckState,
  options: ExportNameOptions = {},
  onProgress?: ExportProgress,
): Promise<ExportArtifact> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const [w, h] = getCanvasSize(deck);
  const deckName = getDeckName(deck, options);

  onProgress?.({ kind: "phase", label: `Preparing ${entries.length} slides` });

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
    let ready = 0;
    await Promise.all(
      frames.map(async (f) => {
        await waitForIframeReady(f);
        ready += 1;
        onProgress?.({ kind: "info", text: `Slide ${ready}/${frames.length} ready` });
      }),
    );
    onProgress?.({ kind: "phase", label: "Building PPTX shapes" });

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
      const ctx: PptxContext = { slide, rootRect, pptx };

      // Emit the root element's own background / border / radius so it's preserved.
      // We do this directly (not via processElement) to avoid double-processing children.
      if (rootEl !== body) {
        const rootStyle = window.getComputedStyle(rootEl);
        const rootBg = parseBackgroundFill(rootStyle);
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

      if (hasVisiblePseudo(rootEl as HTMLElement, "::before")) {
        renderPseudoElement(rootEl as HTMLElement, "::before", ctx, rootRect);
      }

      // Process children of the root element.
      Array.from(rootEl.children).forEach((child) => {
        processElement(child, ctx);
      });

      if (hasVisiblePseudo(rootEl as HTMLElement, "::after")) {
        renderPseudoElement(rootEl as HTMLElement, "::after", ctx, rootRect);
      }
    });

    if (slidesAdded === 0) {
      throw new Error("PPTX export failed: no slides could be rendered. The slide HTML may not have loaded correctly.");
    }

    const blob = await pptx.write({ outputType: "blob" }) as Blob;
    return { filename: `${deckName}.pptx`, blob };
  } finally {
    host.remove();
  }
}

/** Main export function. */
export async function exportPptx(deck: DeckState, onProgress?: ExportProgress): Promise<void> {
  const artifact = await buildPptxDownloadArtifact(deck, onProgress);
  downloadBlob(artifact.blob, artifact.filename);
  onProgress?.({ kind: "done", detail: `${(artifact.blob.size / 1024 / 1024).toFixed(1)} MB` });
}

async function buildPptxDownloadArtifact(
  deck: DeckState,
  onProgress?: ExportProgress,
): Promise<ExportArtifact> {
  const pptx = await buildPptxArtifact(deck, {}, onProgress);
  const entries = getSlideEntries(deck);
  const fontPackage = collectFontPackageInfo(entries);
  if (!hasFontPackage(fontPackage)) {
    onProgress?.({ kind: "info", text: "No external fonts referenced" });
    return pptx;
  }

  onProgress?.({ kind: "phase", label: `Bundling fonts (${fontPackage.resources.length} resources)` });
  const deckName = getDeckName(deck);
  const zip = new JSZip();
  zip.file(pptx.filename, pptx.blob);
  await addFontPackageToZip(zip, fontPackage, deckName, "", onProgress);
  zip.file(
    "README.md",
    [
      `# ${deckName} PPTX export`,
      "",
      `- Editable deck: ${pptx.filename}`,
      "- Font helper: font-package/font-links.html",
      "- Install packaged fonts before opening the PPTX for the closest visual match.",
      "",
    ].join("\n"),
  );
  onProgress?.({ kind: "phase", label: "Building zip" });
  const blob = await zip.generateAsync({ type: "blob" });
  return { filename: `${deckName}-pptx.zip`, blob };
}

function buildPackageIndexHtml(packageName: string, rows: PackageIndexRow[]): string {
  const items = rows
    .map((row) => {
      const fontPackageLink = row.fontPackageHref
        ? `<br />\n        <a href="${escapeAttr(row.fontPackageHref)}">Font package</a>`
        : "";
      return `    <tr>
      <td><strong>${escapeText(row.laneName)}</strong><br /><code>${escapeText(row.laneId)}</code></td>
      <td>${escapeText(row.stage)}</td>
      <td>
        <a href="${escapeAttr(row.singleHtmlHref)}">Single HTML</a><br />
        <a href="${escapeAttr(row.slideIndexHref)}">Slide HTML index</a><br />
        <a href="${escapeAttr(row.pngsHref)}">PNG package</a><br />
        <a href="${escapeAttr(row.pptxHref)}">PPTX</a>${fontPackageLink}
      </td>
      <td><pre>${escapeText(row.prompt || "Baseline lane")}</pre></td>
    </tr>`;
    })
    .join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>${escapeText(packageName)}</title>
  <style>
    body { margin: 0; padding: 24px; background: #f5f3ee; color: #1c1c1e; font-family: Georgia, serif; }
    h1 { margin: 0 0 16px; font-size: 24px; }
    table { width: 100%; border-collapse: collapse; background: #f5f3ee; border: 1px solid #0a0a0a; }
    th, td { text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid #0a0a0a; font-size: 14px; }
    th { background: #e8e3d8; font-weight: 600; }
    a { color: #8b1a1a; }
    code { color: #5c5852; font-size: 12px; }
    pre { margin: 0; white-space: pre-wrap; font: inherit; color: #1c1c1e; }
  </style>
</head>
<body>
  <h1>${escapeText(packageName)}</h1>
  <table>
    <thead>
      <tr><th>Lane</th><th>Stage</th><th>Artifacts</th><th>Prompt</th></tr>
    </thead>
    <tbody>
${items}
    </tbody>
  </table>
</body>
</html>
`;
}

export async function buildPlaygroundLanesPackageArtifact(
  deck: DeckState,
  lanes: ExportablePlaygroundLane[],
): Promise<ExportArtifact> {
  const exportableLanes = lanes.filter((lane) => hasExportableSlides(lane.state));
  if (exportableLanes.length === 0) throw new Error("No rendered playground lanes to export");

  const packageName = `${getDeckName(deck)}-playground-lanes`;
  const zip = new JSZip();
  const rows: PackageIndexRow[] = [];

  for (const lane of exportableLanes) {
    if (!lane.state) continue;

    const folder = `${sanitizeFileName(lane.lane_id)}/`;
    const laneName = (lane.state.values?.deck_name as string | undefined) || lane.lane_id;
    const stage = (lane.state.values?.current_stage as string | undefined) || "pending";
    const entries = getSlideEntries(lane.state);
    const canvas = getCanvasSize(lane.state);
    const htmlSingle = buildHtmlSingleArtifact(lane.state);
    const pngs = await buildPngZipArtifact(lane.state);
    const pptx = await buildPptxArtifact(lane.state);

    zip.file(`${folder}${htmlSingle.filename}`, htmlSingle.blob);
    addHtmlSlidesToZip(zip, entries, getDeckName(lane.state), canvas, folder);
    zip.file(`${folder}${pngs.filename}`, pngs.blob);
    zip.file(`${folder}${pptx.filename}`, pptx.blob);
    const fontPackageHref = await addFontPackageToZip(
      zip,
      collectFontPackageInfo(entries),
      getDeckName(lane.state),
      folder,
    );

    rows.push({
      laneId: lane.lane_id,
      laneName,
      stage,
      prompt: lane.creator_prompt,
      singleHtmlHref: `${folder}${htmlSingle.filename}`,
      slideIndexHref: `${folder}index.html`,
      pngsHref: `${folder}${pngs.filename}`,
      pptxHref: `${folder}${pptx.filename}`,
      fontPackageHref: fontPackageHref ?? undefined,
    });
  }

  zip.file("index.html", buildPackageIndexHtml(packageName, rows));
  const blob = await zip.generateAsync({ type: "blob" });
  return { filename: `${packageName}.zip`, blob };
}

export async function exportPlaygroundLanesPackage(
  deck: DeckState,
  lanes: ExportablePlaygroundLane[],
): Promise<void> {
  const artifact = await buildPlaygroundLanesPackageArtifact(deck, lanes);
  downloadBlob(artifact.blob, artifact.filename);
}
