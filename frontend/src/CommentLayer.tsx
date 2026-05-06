// Overlay that captures a drag-box on the slide and prompts for comment text.
// Emits {box:{x,y,w,h in 0..1 relative to canvas}, text} to the parent.

import { useRef, useState } from "react";

type Box = { x: number; y: number; w: number; h: number };
type Props = {
  width: number;
  height: number;
  onSubmit: (text: string, box: Box) => void;
};

export function CommentLayer({ width, height, onSubmit }: Props) {
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [pendingBox, setPendingBox] = useState<Box | null>(null);
  const [text, setText] = useState("");
  const layerRef = useRef<HTMLDivElement>(null);

  function onDown(e: React.MouseEvent) {
    if (pendingBox) return;
    const rect = layerRef.current!.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    setDrag({ x0: x, y0: y, x1: x, y1: y });
  }
  function onMove(e: React.MouseEvent) {
    if (!drag) return;
    const rect = layerRef.current!.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    setDrag({ ...drag, x1: x, y1: y });
  }
  function onUp() {
    if (!drag) return;
    const box: Box = {
      x: Math.min(drag.x0, drag.x1),
      y: Math.min(drag.y0, drag.y1),
      w: Math.abs(drag.x1 - drag.x0),
      h: Math.abs(drag.y1 - drag.y0),
    };
    setDrag(null);
    if (box.w < 0.02 || box.h < 0.02) return; // drop stray clicks
    setPendingBox(box);
  }

  const live = drag
    ? {
        left: `${Math.min(drag.x0, drag.x1) * 100}%`,
        top: `${Math.min(drag.y0, drag.y1) * 100}%`,
        width: `${Math.abs(drag.x1 - drag.x0) * 100}%`,
        height: `${Math.abs(drag.y1 - drag.y0) * 100}%`,
      }
    : null;

  return (
    <div
      ref={layerRef}
      onMouseDown={onDown}
      onMouseMove={onMove}
      onMouseUp={onUp}
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        cursor: pendingBox ? "default" : "crosshair",
      }}
    >
      {live && (
        <div
          style={{
            position: "absolute",
            ...live,
            border: "2px dashed #0a0a0a",
            background: "rgba(10,10,10,0.06)",
            pointerEvents: "none",
          }}
        />
      )}
      {pendingBox && (
        <div
          style={{
            position: "absolute",
            left: `${pendingBox.x * 100}%`,
            top: `${pendingBox.y * 100}%`,
            width: `${pendingBox.w * 100}%`,
            height: `${pendingBox.h * 100}%`,
            border: "2px solid #0a0a0a",
            background: "rgba(10,10,10,0.05)",
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              position: "absolute",
              background: "#f5f3ee",
              padding: 8,
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              display: "flex",
              gap: 6,
              alignItems: "center",
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <input
              autoFocus
              placeholder="Your comment…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              style={{
                border: "1px solid #0a0a0a",
                borderRadius: 0,
                padding: 6,
                fontSize: 13,
                color: "#1c1c1e",
                background: "#e8e3d8",
                minWidth: 240,
              }}
            />
            <button
              onClick={() => {
                if (text.trim()) onSubmit(text.trim(), pendingBox);
                setPendingBox(null);
                setText("");
              }}
              style={{
                border: "1.5px solid #0a0a0a",
                borderRadius: 0,
                background: "#0a0a0a",
                color: "#f5f3ee",
                padding: "6px 12px",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: 1.4,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              Send
            </button>
            <button
              onClick={() => {
                setPendingBox(null);
                setText("");
              }}
              style={{
                border: "1.5px solid #0a0a0a",
                borderRadius: 0,
                background: "#f5f3ee",
                color: "#0a0a0a",
                padding: "6px 10px",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: 1.4,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              ✕
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
