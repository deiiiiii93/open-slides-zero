// Thin wrapper around react-markdown with GFM + safe defaults for our panels.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Props = { children: string; style?: React.CSSProperties; className?: string };

export function Markdown({ children, style, className }: Props) {
  return (
    <div className={className} style={{ lineHeight: 1.55, ...style }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: (props) => <h1 style={{ fontSize: 20, marginTop: 12 }} {...props} />,
          h2: (props) => <h2 style={{ fontSize: 16, marginTop: 10, borderBottom: "1px solid #eee", paddingBottom: 2 }} {...props} />,
          h3: (props) => <h3 style={{ fontSize: 14, marginTop: 8 }} {...props} />,
          code: ({ children, ...rest }) => (
            <code
              style={{ background: "#f3f4f6", padding: "1px 4px", borderRadius: 3, fontSize: "0.9em" }}
              {...rest}
            >
              {children}
            </code>
          ),
          pre: (props) => (
            <pre
              style={{
                background: "#fafafa",
                padding: 10,
                border: "1px solid #eee",
                borderRadius: 4,
                overflow: "auto",
                fontSize: 12,
              }}
              {...props}
            />
          ),
          table: (props) => (
            <table style={{ borderCollapse: "collapse", margin: "8px 0" }} {...props} />
          ),
          th: (props) => (
            <th style={{ border: "1px solid #e5e5e5", padding: "4px 8px", background: "#f9fafb" }} {...props} />
          ),
          td: (props) => (
            <td style={{ border: "1px solid #e5e5e5", padding: "4px 8px" }} {...props} />
          ),
          blockquote: (props) => (
            <blockquote
              style={{ borderLeft: "3px solid #d1d5db", margin: "6px 0", paddingLeft: 10, color: "#555" }}
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
