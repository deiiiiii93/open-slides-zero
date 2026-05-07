// DeckCanvas renders each slide in an isolated <iframe srcdoc> at exactly 960x540
// (Layout Catalog §10.1) and CSS-scales to fit the viewport. Isolated iframes
// prevent the slide's CSS from leaking into the app chrome.

import { useEffect, useMemo, useRef, useState } from "react";
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
  const wrapRef = useRef<HTMLDivElement>(null);
  const [effW, setEffW] = useState(width);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      if (w > 0) setEffW(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const scale = effW / baseW;
  const scaledH = baseH * scale;

  const sortedIdx = useMemo(
    () => (slideOrder?.length ? slideOrder : Object.keys(slides).map(Number).sort((a, b) => a - b)),
    [slideOrder, slides],
  );

  const html = normalizeImagePlaceholders(slides[currentSlide] ?? pendingSlideHtml(currentSlide));

  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length !== 1) {
      touchStart.current = null;
      return;
    }
    const t = e.touches[0];
    touchStart.current = { x: t.clientX, y: t.clientY };
  };
  const handleTouchEnd = (e: React.TouchEvent) => {
    const start = touchStart.current;
    touchStart.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (Math.abs(dx) < 40 || Math.abs(dx) < Math.abs(dy)) return;
    const idx = sortedIdx.indexOf(currentSlide);
    if (idx < 0) return;
    if (dx < 0 && idx < sortedIdx.length - 1) onSelectSlide(sortedIdx[idx + 1]);
    else if (dx > 0 && idx > 0) onSelectSlide(sortedIdx[idx - 1]);
  };

  const currentPosition = sortedIdx.indexOf(currentSlide);

  return (
    <div className="osz-deck-shell" style={{ display: "flex", gap: 16 }}>
      <div className="osz-deck-mobile-nav">
        <span className="osz-deck-mobile-nav-label">Slide</span>
        <select
          className="osz-deck-mobile-nav-select"
          value={currentSlide}
          onChange={(e) => onSelectSlide(Number(e.target.value))}
        >
          {sortedIdx.map((i) => {
            const isReady = typeof slides[i] === "string" && slides[i].length > 0;
            return (
              <option key={i} value={i}>
                Slide {i + 1}{isReady ? "" : " · rendering"}
              </option>
            );
          })}
        </select>
        <span className="osz-deck-mobile-nav-counter">
          {currentPosition >= 0 ? currentPosition + 1 : "—"} / {sortedIdx.length}
        </span>
      </div>
      <aside
        className="osz-deck-sidebar"
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
          className="osz-deck-eyebrow"
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
              className="osz-deck-thumb"
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

      <div
        ref={wrapRef}
        className="osz-canvas-wrap"
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        style={{
          position: "relative",
          width: "100%",
          maxWidth: width,
          minWidth: 0,
          height: scaledH,
          overflow: "hidden",
          touchAction: "pan-y",
        }}
      >
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

function pendingSlideHtml(slideIdx: number): string {
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
