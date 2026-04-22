// Client-side exporters for deck output.
//
// Three formats:
//   exportHtmlSingle  — one self-contained .html file with iframes per slide
//   exportHtmlZip     — .zip with slide_NN.html files + a small index.html
//   exportPptx        — editable .pptx via dom-to-pptx walking each slide's DOM
//
// All three run fully in the browser; no backend endpoints are required.

import JSZip from "jszip";
import { exportToPptx } from "dom-to-pptx";
import type { DeckState } from "./api";

// Keep in sync with DeckCanvas.tsx:17-21. Duplicated rather than shared because
// it's four lines and importing DeckCanvas just for a constant is overkill.
const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

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
//
// Poll-based on purpose: a freshly-created iframe ships with an initial about:blank
// document whose readyState is already "complete" and which has a (empty) body,
// so a naive "readyState === complete" check fires before srcdoc has been parsed.
// We wait for the URL to flip to about:srcdoc (or a non-empty body, as a fallback)
// before trusting the contentDocument.
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
      await Promise.race([
        fonts.ready,
        new Promise((res) => setTimeout(res, 3000)),
      ]);
    }
  } catch {
    // Cross-origin guard — sandboxed iframes can throw; ignore.
  }
}

export async function exportPptx(deck: DeckState): Promise<void> {
  const entries = getSlideEntries(deck);
  if (entries.length === 0) throw new Error("No rendered slides to export");
  const [w, h] = getCanvasSize(deck);
  const deckName = getDeckName(deck);

  // Mount all slide iframes off-screen but rendered. display:none or
  // visibility:hidden breaks getBoundingClientRect, so we use left:-20000px.
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
    // Note: no sandbox attribute — dom-to-pptx needs same-origin access to
    // contentDocument + computed styles. srcdoc iframes are same-origin by default.
    host.appendChild(f);
    return f;
  });

  try {
    await Promise.all(frames.map((f) => waitForIframeReady(f)));

    // Convert pixel canvas to PPT inches. Aligned to dom-to-pptx's assumption
    // that 1in ≈ 96px (CSS pixels) which is the standard devtools assumption.
    const widthIn = w / 96;
    const heightIn = h / 96;

    const elements: HTMLElement[] = [];
    const skipped: number[] = [];
    frames.forEach((f, i) => {
      const body = f.contentDocument?.body ?? f.contentDocument?.documentElement;
      if (body && body.childElementCount > 0) {
        elements.push(body as HTMLElement);
      } else {
        skipped.push(entries[i][0] + 1);
      }
    });
    if (skipped.length) {
      console.warn(`PPTX export: skipping slides with no accessible body: ${skipped.join(", ")}`);
    }
    if (elements.length === 0) {
      throw new Error(
        "No slide iframes loaded in time. Try again, or refresh the page if slides failed to render.",
      );
    }

    await exportToPptx(elements, {
      fileName: `${deckName}.pptx`,
      autoEmbedFonts: true,
      width: widthIn,
      height: heightIn,
    });
  } finally {
    host.remove();
  }
}
