// DeckCanvas renders each slide in an isolated <iframe srcdoc> at exactly 960x540
// (Layout Catalog §10.1) and CSS-scales to fit the viewport. Isolated iframes
// prevent the slide's CSS from leaking into the app chrome.

import { useMemo } from "react";

type Props = {
  slides: Record<number, string>;
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
    () => Object.keys(slides).map(Number).sort((a, b) => a - b),
    [slides],
  );

  const html = slides[currentSlide] ?? "";

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
        {sortedIdx.map((i) => (
          <button
            key={i}
            onClick={() => onSelectSlide(i)}
            style={{
              padding: 8,
              borderRadius: 4,
              border: i === currentSlide ? "2px solid #2563eb" : "1px solid #e5e5e5",
              background: "white",
              cursor: "pointer",
              fontSize: 12,
              textAlign: "left",
            }}
          >
            Slide {i + 1}
          </button>
        ))}
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
