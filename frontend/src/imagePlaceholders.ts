const PLACEHOLDER_PRIMARY = "Add image here";

function hasRealImageSource(img: HTMLImageElement): boolean {
  const rawSrc = img.getAttribute("src");
  return Boolean(rawSrc && rawSrc.trim());
}

function dimensionToCss(value: string): string {
  return /^\d+(?:\.\d+)?$/.test(value.trim()) ? `${value.trim()}px` : value.trim();
}

function applyDefaultStyle(slot: HTMLElement, img: HTMLImageElement): void {
  const width = img.getAttribute("width");
  const height = img.getAttribute("height");
  if (width && !slot.style.width) slot.style.width = dimensionToCss(width);
  if (height && !slot.style.height) slot.style.height = dimensionToCss(height);

  slot.style.display = "flex";
  slot.style.flexDirection = "column";
  slot.style.alignItems = "center";
  slot.style.justifyContent = "center";
  slot.style.gap = slot.style.gap || "8px";
  slot.style.padding = slot.style.padding || "18px";
  slot.style.boxSizing = "border-box";
  slot.style.overflow = "hidden";
  slot.style.textAlign = "center";
  slot.style.background = slot.style.background || "rgba(148, 163, 184, 0.12)";
  slot.style.border = slot.style.border || "1.5px dashed rgba(71, 85, 105, 0.48)";
  slot.style.color = slot.style.color || "rgba(30, 41, 59, 0.88)";
  slot.style.fontFamily = slot.style.fontFamily || "inherit";
}

function buildPlaceholderContent(doc: Document, hint: string): Node[] {
  const label = doc.createElement("span");
  label.textContent = PLACEHOLDER_PRIMARY;
  label.style.fontSize = "16px";
  label.style.fontWeight = "700";
  label.style.lineHeight = "1.15";

  const suggestion = doc.createElement("span");
  suggestion.textContent = `Suggested: ${hint}`;
  suggestion.style.fontSize = "12px";
  suggestion.style.lineHeight = "1.35";
  suggestion.style.opacity = "0.74";
  suggestion.style.maxWidth = "86%";
  suggestion.style.overflowWrap = "anywhere";

  return [label, suggestion];
}

function serializeDocument(doc: Document, originalHtml: string): string {
  const doctype = originalHtml.match(/^\s*<!doctype[^>]*>/i)?.[0] ?? "<!DOCTYPE html>";
  return `${doctype}\n${doc.documentElement.outerHTML}`;
}

export function normalizeImagePlaceholders(html: string): string {
  if (!html || !/<img\b/i.test(html) || typeof DOMParser === "undefined") return html;

  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    let changed = false;

    doc.querySelectorAll("img").forEach((img) => {
      if (hasRealImageSource(img)) return;

      const slot = doc.createElement("div");
      Array.from(img.attributes).forEach((attr) => {
        if (attr.name.toLowerCase() !== "src") {
          slot.setAttribute(attr.name, attr.value);
        }
      });

      const hint = (
        slot.getAttribute("data-prompt-hint") ||
        img.getAttribute("alt") ||
        "image"
      ).trim();
      slot.setAttribute("data-image-placeholder", "true");
      slot.setAttribute("data-prompt-hint", hint);
      slot.setAttribute("role", "img");
      slot.setAttribute("aria-label", `${PLACEHOLDER_PRIMARY}. Suggested: ${hint}`);
      applyDefaultStyle(slot, img);
      slot.replaceChildren(...buildPlaceholderContent(doc, hint));
      img.replaceWith(slot);
      changed = true;
    });

    return changed ? serializeDocument(doc, html) : html;
  } catch {
    return html;
  }
}
