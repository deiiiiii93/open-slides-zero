// Thin wrapper around react-markdown with GFM + safe defaults for our panels.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = { children: string; style?: React.CSSProperties; className?: string };

export function Markdown({ children, style, className }: Props) {
  const markdownClassName = className ? `osz-markdown ${className}` : "osz-markdown";

  return (
    <div className={markdownClassName} style={{ lineHeight: 1.55, ...style }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (props) => (
            <h1
              style={{
                fontFamily: "'Playfair Display', Georgia, serif",
                fontWeight: 700,
                fontSize: 22,
                marginTop: 14,
                marginBottom: 8,
                color: "#0a0a0a",
                lineHeight: 1.15,
              }}
              {...props}
            />
          ),
          h2: (props) => (
            <h2
              style={{
                fontFamily: "'Playfair Display', Georgia, serif",
                fontWeight: 700,
                fontSize: 18,
                marginTop: 12,
                marginBottom: 6,
                paddingBottom: 4,
                borderBottom: "1px solid #0a0a0a",
                color: "#0a0a0a",
                lineHeight: 1.2,
              }}
              {...props}
            />
          ),
          h3: (props) => (
            <h3
              style={{
                fontFamily: "ui-sans-serif, -apple-system, sans-serif",
                fontWeight: 700,
                fontSize: 14,
                marginTop: 10,
                marginBottom: 4,
                color: "#0a0a0a",
              }}
              {...props}
            />
          ),
          code: ({ children, className, ...rest }) => (
            <code className={className} {...rest}>
              {children}
            </code>
          ),
          a: (props) => (
            <a
              style={{
                color: "#0a0a0a",
                textDecoration: "none",
                borderBottom: "1px solid #0a0a0a",
              }}
              {...props}
            />
          ),
          pre: (props) => (
            <pre
              style={{
                background: "#e8e3d8",
                padding: 12,
                border: "1px solid #0a0a0a",
                borderRadius: 0,
                overflow: "auto",
                fontSize: 12,
                lineHeight: 1.45,
                fontFamily: "ui-monospace, 'SF Mono', monospace",
                color: "#1c1c1e",
              }}
              {...props}
            />
          ),
          table: (props) => (
            <table style={{ borderCollapse: "collapse", margin: "8px 0" }} {...props} />
          ),
          th: (props) => (
            <th
              style={{
                border: "1px solid #0a0a0a",
                background: "#e8e3d8",
                padding: "5px 9px",
                textAlign: "left",
                fontWeight: 700,
                color: "#0a0a0a",
              }}
              {...props}
            />
          ),
          td: (props) => (
            <td
              style={{
                border: "1px solid #0a0a0a",
                padding: "5px 9px",
                color: "#1c1c1e",
              }}
              {...props}
            />
          ),
          blockquote: (props) => (
            <blockquote
              style={{
                borderLeft: "2px solid #0a0a0a",
                margin: "8px 0",
                paddingLeft: 12,
                color: "#5c5852",
                fontStyle: "italic",
              }}
              {...props}
            />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
