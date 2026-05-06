// DeckCanvas renders each slide in an isolated <iframe srcdoc> at exactly 960x540
// (Layout Catalog §10.1) and CSS-scales to fit the viewport. Isolated iframes
// prevent the slide's CSS from leaking into the app chrome.

import { useMemo } from "react";
import { normalizeImagePlaceholders } from "./imagePlaceholders";

type Props = {
  slides: Record<number, string>;
  slideOrder?: number[];
  currentSlide: number;
  onSelectSlide: (idx: number) => void;
  aspectRatio?: "16:9" | "4:3" | "21:9";
  width?: number;
  children?: React.ReactNode; // overlay (e.g. CommentLayer)
};

const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

export function DeckCanvas({
  slides,
  slideOrder,
  currentSlide,
  onSelectSlide,
  aspectRatio = "16:9",
  width = 960,
  children,
}: Props) {
  const [baseW, baseH] = CANVAS[aspectRatio] ?? CANVAS["16:9"];
  const scale = width / baseW;
  const scaledH = baseH * scale;

  const sortedIdx = useMemo(
    () => (slideOrder?.length ? slideOrder : Object.keys(slides).map(Number).sort((a, b) => a - b)),
    [slideOrder, slides],
  );

  const html = normalizeImagePlaceholders(slides[currentSlide] ?? pendingSlideHtml(baseW, baseH, currentSlide));

  return (
    <div style={{ display: "flex", gap: 16 }}>
      <aside
        style={{
          width: 140,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          height: scaledH,
          background: "#f5f3ee",
          borderRight: "1.5px solid #0a0a0a",
          padding: 16,
        }}
      >
        <div
          style={{
            paddingBottom: 12,
            marginBottom: 14,
            borderBottom: "1px solid #0a0a0a",
            fontFamily: "ui-sans-serif, -apple-system, sans-serif",
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 2.5,
            textTransform: "uppercase",
            color: "#5c5852",
            lineHeight: 1.0,
          }}
        >
          Slides · {sortedIdx.length}
        </div>
        {sortedIdx.map((i) => {
          const isReady = typeof slides[i] === "string" && slides[i].length > 0;
          return (
            <button
              key={i}
              onClick={() => onSelectSlide(i)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "12px 14px",
                marginBottom: 8,
                border: i === currentSlide
                  ? "2px solid #0a0a0a"
                  : isReady
                  ? "1.5px solid #0a0a0a"
                  : "1.5px solid #948e83",
                borderRadius: 0,
                background: "#f5f3ee",
                color: isReady ? "#0a0a0a" : "#948e83",
                cursor: "pointer",
                transition: "border-color 100ms linear, background 100ms linear",
              }}
            >
              <div
                style={{
                  fontFamily: "'Playfair Display', Georgia, serif",
                  fontWeight: 700,
                  fontSize: 14,
                  color: isReady ? "#0a0a0a" : "#948e83",
                  lineHeight: 1.0,
                }}
              >
                Slide {i + 1}
              </div>
              {!isReady && (
                <div
                  style={{
                    fontSize: 10,
                    color: "#948e83",
                    marginTop: 4,
                    letterSpacing: 0.4,
                  }}
                >
                  rendering
                </div>
              )}
            </button>
          );
        })}
      </aside>

      <div style={{ position: "relative", width, height: scaledH }}>
        <div
          style={{
            transformOrigin: "top left",
            transform: `scale(${scale})`,
            width: baseW,
            height: baseH,
          }}
        >
          <iframe
            title={`slide-${currentSlide}`}
            srcDoc={html}
            sandbox="allow-same-origin"
            style={{
              width: baseW,
              height: baseH,
              border: "2px solid #0a0a0a",
              borderRadius: 0,
              background: "white",
            }}
          />
        </div>
        {children}
      </div>
    </div>
  );
}

function pendingSlideHtml(width: number, height: number, slideIdx: number): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body { margin: 0; height: 100%; }
  body {
    display: grid;
    place-items: center;
    background: #f5f3ee;
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #1c1c1e;
  }
  .card {
    border: 2px solid #0a0a0a;
    background: #f5f3ee;
    padding: 32px 40px;
    max-width: 460px;
    text-align: center;
  }
  .eyebrow {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2.5px;
    text-transform: uppercase;
    color: #5c5852;
    margin-bottom: 14px;
  }
  h2 {
    font-family: Georgia, serif;
    font-weight: 700;
    font-size: 26px;
    margin: 0 0 10px 0;
    color: #0a0a0a;
    line-height: 1.15;
  }
  p {
    font-size: 14px;
    color: #5c5852;
    margin: 0;
    line-height: 1.55;
  }
</style>
</head>
<body>
  <div class="card">
    <div class="eyebrow">Rendering</div>
    <h2>Slide ${slideIdx + 1} is generating</h2>
    <p>The agent is producing this slide's HTML.</p>
  </div>
</body>
</html>`;
}
