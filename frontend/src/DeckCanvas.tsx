// DeckCanvas renders each slide in an isolated <iframe srcdoc> at exactly 960x540
// (Layout Catalog §10.1) and CSS-scales to fit the viewport. Isolated iframes
// prevent the slide's CSS from leaking into the app chrome.

import { useMemo } from "react";

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

  const html = slides[currentSlide] ?? pendingSlideHtml(baseW, baseH, currentSlide);

  return (
    <div style={{ display: "flex", gap: 16 }}>
      <aside
        style={{
          width: 140,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: 8,
          borderRight: "1px solid #e5e5e5",
        }}
      >
        {sortedIdx.map((i) => {
          const isReady = typeof slides[i] === "string" && slides[i].length > 0;
          return (
            <button
              key={i}
              onClick={() => onSelectSlide(i)}
              style={{
                padding: 8,
                borderRadius: 4,
                border: i === currentSlide ? "2px solid #2563eb" : "1px solid #e5e5e5",
                background: isReady ? "white" : "#f8fafc",
                cursor: "pointer",
                fontSize: 12,
                textAlign: "left",
                color: isReady ? "#111" : "#64748b",
              }}
            >
              Slide {i + 1}
              {!isReady ? " · rendering" : ""}
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
              border: "1px solid #111",
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
  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Slide ${slideIdx + 1}</title>
    <style>
      body {
        margin: 0;
        background: #f3f4f6;
        font-family: Georgia, serif;
      }
      .slide {
        width: ${width}px;
        height: ${height}px;
        box-sizing: border-box;
        padding: 40px;
        display: grid;
        place-items: center;
        color: #475569;
        background:
          linear-gradient(135deg, rgba(255,255,255,0.92), rgba(241,245,249,0.96)),
          repeating-linear-gradient(
            -45deg,
            rgba(148,163,184,0.08) 0,
            rgba(148,163,184,0.08) 12px,
            transparent 12px,
            transparent 24px
          );
      }
      .card {
        padding: 22px 28px;
        border: 1px solid rgba(100,116,139,0.25);
        background: rgba(255,255,255,0.82);
        letter-spacing: 0.02em;
      }
      .label {
        font-size: 12px;
        text-transform: uppercase;
        color: #64748b;
        margin-bottom: 8px;
      }
      .title {
        font-size: 28px;
        color: #0f172a;
      }
    </style>
  </head>
  <body>
    <div class="slide">
      <div class="card">
        <div class="label">Rendering</div>
        <div class="title">Slide ${slideIdx + 1} is still generating</div>
      </div>
    </div>
  </body>
</html>`;
}
