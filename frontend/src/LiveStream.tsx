// LiveStream shows a live-updating token feed for whatever node is currently
// generating. Tokens are grouped by `tag` (outline / style / layout / html:N)
// and rendered as streaming markdown once complete enough to be parseable.

import { Markdown } from "./Markdown";

type Props = {
  buffersByTag: Record<string, string>;
  activeNode: string | null;
  activeSlide: number | null;
  elapsedByTag: Record<string, number>;
  title?: string;
  subtitle?: string;
  isActive?: boolean;
  maxHeight?: string | number;
};

const NODE_LABELS: Record<string, string> = {
  propose_structure: "① Scenario & structure candidates",
  outline: "② Outline",
  style: "③ Visual style",
  layout: "④ Per-slide layouts",
  html_one: "⑤ Slide HTML",
  consolidate: "· Consolidating brief",
  post_html: "✓ Ready",
};

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 100) / 10;
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  return `${m}m ${rs}s`;
}

export function LiveStream({
  buffersByTag,
  activeNode,
  activeSlide,
  elapsedByTag,
  title = "Live",
  subtitle,
  isActive = false,
  maxHeight = "calc(100vh - 120px)",
}: Props) {
  const tagsInOrder = ["propose_structure", "outline", "style", "layout"];
  const htmlTags = Object.keys(buffersByTag)
    .filter((t) => t.startsWith("html:"))
    .sort((a, b) => Number(a.split(":")[1]) - Number(b.split(":")[1]));

  const label = activeNode
    ? (NODE_LABELS[activeNode] ?? activeNode) +
      (activeSlide != null ? ` (slide ${activeSlide + 1})` : "")
    : "idle";

  const commentBuf = buffersByTag.comment;

  return (
    <div
      style={{
        padding: 12,
        border: isActive ? "1px solid #2563eb" : "1px solid #e5e5e5",
        borderRadius: 8,
        background: "#fff",
        fontFamily: "ui-sans-serif, -apple-system, sans-serif",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        maxHeight,
        overflowY: "auto",
      }}
    >
      <div
        style={{
          fontSize: 11,
          letterSpacing: 0.8,
          textTransform: "uppercase",
          color: "#6b7280",
        }}
      >
        {title}
      </div>
      {subtitle && (
        <div style={{ fontSize: 12, color: "#64748b", marginTop: -6 }}>
          {subtitle}
        </div>
      )}
      <div style={{ fontSize: 13, fontWeight: 600, color: "#111827" }}>{label}</div>

      {commentBuf && (
        <div
          style={{
            fontSize: 12,
            padding: 8,
            background: "#eff6ff",
            border: "1px solid #bfdbfe",
            borderRadius: 4,
            color: "#1e3a8a",
            whiteSpace: "pre-wrap",
          }}
        >
          {commentBuf}
        </div>
      )}

      {tagsInOrder.map((tag) =>
        buffersByTag[tag] ? (
          <details key={tag} open={activeNode === tag}>
            <summary
              style={{
                fontSize: 12,
                cursor: "pointer",
                color: "#374151",
                padding: "2px 0",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{NODE_LABELS[tag] ?? tag}</span>
              {elapsedByTag[tag] && (
                <span style={{ color: "#6b7280", fontSize: 10, marginLeft: 8 }}>
                  {fmtMs(elapsedByTag[tag])}
                </span>
              )}
            </summary>
            <div
              style={{
                maxHeight: 260,
                overflow: "auto",
                padding: 8,
                background: "#fafafa",
                border: "1px solid #e5e7eb",
                borderRadius: 4,
                marginTop: 4,
                fontSize: 12,
              }}
            >
              <Markdown>{buffersByTag[tag]}</Markdown>
            </div>
          </details>
        ) : null,
      )}

      {htmlTags.length > 0 && (
        <details open={activeNode === "html_one"}>
          <summary
            style={{
              fontSize: 12,
              cursor: "pointer",
              color: "#374151",
              padding: "2px 0",
            }}
          >
            ⑤ Slide HTML · {htmlTags.length} slide{htmlTags.length > 1 ? "s" : ""}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
            {htmlTags.map((tag) => {
              const idx = Number(tag.split(":")[1]);
              const buf = buffersByTag[tag] ?? "";
              const isActive = activeSlide === idx && activeNode === "html_one";
              const elapsed = elapsedByTag[tag];
              const isDone = buf.includes("</html>");
              return (
                <div
                  key={tag}
                  style={{
                    border: isActive ? "1px solid #2563eb" : "1px solid #e5e7eb",
                    borderRadius: 4,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "4px 8px",
                      background: isActive ? "#eff6ff" : "#f9fafb",
                      fontSize: 11,
                      color: "#374151",
                    }}
                  >
                    <span>slide {idx + 1}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      {elapsed && (
                        <span style={{ color: "#6b7280", fontSize: 10 }}>
                          {fmtMs(elapsed)}
                        </span>
                      )}
                      <span style={{ color: "#6b7280" }}>
                        {buf.length.toLocaleString()} chars
                      </span>
                      {isDone && (
                        <span style={{ color: "#16a34a", fontSize: 13, fontWeight: 700 }}>
                          &#10003;
                        </span>
                      )}
                    </div>
                  </div>
                  <pre
                    style={{
                      margin: 0,
                      padding: 6,
                      background: "#0b1020",
                      color: "#d1d5db",
                      fontSize: 10,
                      lineHeight: 1.35,
                      maxHeight: 140,
                      overflow: "auto",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                    }}
                  >
                    {buf.slice(-300)}
                  </pre>
                </div>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}
