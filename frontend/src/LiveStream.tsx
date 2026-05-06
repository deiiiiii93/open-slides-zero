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
  digest_materials: "· Digesting materials",
  advanced_chat: "Advanced planning chat",
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
  isActive: _isActive = false,
  maxHeight = "calc(100vh - 120px)",
}: Props) {
  const tagsInOrder = ["advanced_chat", "propose_structure", "outline", "style", "layout"];
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
        border: "1.5px solid #0a0a0a",
        borderRadius: 0,
        background: "#f5f3ee",
        boxShadow: "none",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        maxHeight,
        overflowY: "auto",
      }}
    >
      <div
        style={{
          color: "#5c5852",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: 2.5,
          textTransform: "uppercase",
          fontFamily: "ui-sans-serif, -apple-system, sans-serif",
        }}
      >
        {title}
      </div>
      {subtitle && (
        <div
          style={{
            color: "#5c5852",
            fontSize: 12,
            marginTop: 4,
          }}
        >
          {subtitle}
        </div>
      )}
      <div
        style={{
          fontFamily: "'Playfair Display', Georgia, serif",
          fontWeight: 700,
          fontSize: 18,
          color: "#0a0a0a",
          letterSpacing: -0.012,
          lineHeight: 1.15,
          marginTop: 12,
        }}
      >
        {label}
      </div>

      {commentBuf && (
        <div
          style={{
            background: "#e8e3d8",
            border: "1px solid #0a0a0a",
            borderRadius: 0,
            color: "#1c1c1e",
            padding: "10px 12px",
            fontSize: 13,
            lineHeight: 1.55,
            whiteSpace: "pre-wrap",
          }}
        >
          {commentBuf}
        </div>
      )}

      {tagsInOrder.map((tag) =>
        buffersByTag[tag] ? (
          <details
            key={tag}
            open={activeNode === tag}
            style={{
              borderBottom: "1px solid #0a0a0a",
              padding: 0,
            }}
          >
            <summary
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 14px",
                cursor: "pointer",
                listStyle: "none",
              }}
            >
              <span
                style={{
                  color: "#1c1c1e",
                  fontWeight: 600,
                  fontSize: 13,
                }}
              >
                {NODE_LABELS[tag] ?? tag}
              </span>
              {elapsedByTag[tag] && (
                <span
                  style={{
                    color: "#948e83",
                    fontFamily: "ui-monospace, 'SF Mono', monospace",
                    fontSize: 10,
                  }}
                >
                  {fmtMs(elapsedByTag[tag])}
                </span>
              )}
            </summary>
            <div
              style={{
                background: "#e8e3d8",
                padding: "12px 14px",
                borderTop: "1px solid #0a0a0a",
              }}
            >
              <Markdown>{buffersByTag[tag]}</Markdown>
            </div>
          </details>
        ) : null,
      )}

      {htmlTags.length > 0 && (
        <details
          open={activeNode === "html_one"}
          style={{
            borderBottom: "1px solid #0a0a0a",
            padding: 0,
          }}
        >
          <summary
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 14px",
              cursor: "pointer",
              listStyle: "none",
            }}
          >
            <span
              style={{
                color: "#1c1c1e",
                fontWeight: 600,
                fontSize: 13,
              }}
            >
              ⑤ Slide HTML · {htmlTags.length} slide{htmlTags.length > 1 ? "s" : ""}
            </span>
          </summary>
          <div
            style={{
              background: "#e8e3d8",
              padding: "12px 14px",
              borderTop: "1px solid #0a0a0a",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {htmlTags.map((tag) => {
                const idx = Number(tag.split(":")[1]);
                const buf = buffersByTag[tag] ?? "";
                const isSlideActive = activeSlide === idx && activeNode === "html_one";
                const elapsed = elapsedByTag[tag];
                const isDone = buf.includes("</html>");
                const isStreaming = buf.length > 0;
                return (
                  <div
                    key={tag}
                    style={{
                      border: (isDone || isStreaming) ? "1px solid #0a0a0a" : "1px solid #948e83",
                      borderRadius: 0,
                      background: isSlideActive ? "#e8e3d8" : "#f5f3ee",
                      marginBottom: 8,
                    }}
                  >
                    <div
                      style={{
                        padding: "10px 14px",
                        borderBottom: "1px solid #0a0a0a",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                      >
                        <span
                          style={{
                            fontFamily: "'Playfair Display', Georgia, serif",
                            fontWeight: 700,
                            fontSize: 13,
                            color: "#0a0a0a",
                          }}
                        >
                          SLIDE {idx + 1}
                        </span>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          {elapsed && (
                            <span
                              style={{
                                color: "#948e83",
                                fontFamily: "ui-monospace, 'SF Mono', monospace",
                                fontSize: 10,
                              }}
                            >
                              {fmtMs(elapsed)}
                            </span>
                          )}
                          <span
                            style={{
                              color: "#948e83",
                              fontFamily: "ui-monospace, 'SF Mono', monospace",
                              fontSize: 10,
                            }}
                          >
                            {buf.length.toLocaleString()} chars
                          </span>
                          {isDone && (
                            <span style={{ color: "#3d5a2a", fontSize: 13, fontWeight: 700 }}>
                              &#10003;
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                    <pre
                      style={{
                        background: "#0b1020",
                        color: "#cfc8b9",
                        padding: 12,
                        borderRadius: 0,
                        fontFamily: "ui-monospace, 'SF Mono', monospace",
                        fontSize: 11,
                        lineHeight: 1.45,
                        overflow: "auto",
                        margin: 0,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-all",
                        maxHeight: 140,
                      }}
                    >
                      {buf.slice(-300)}
                    </pre>
                  </div>
                );
              })}
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
