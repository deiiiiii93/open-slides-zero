// Proportional visual wireframe — replaces the ASCII preview. Each pattern
// has a hand-crafted zone box layout; an unknown pattern falls back to a
// family-based generic layout. Boxes are positioned in percentages of a
// 16:9 thumbnail so zoom levels don't break alignment.

type Box = {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  accent?: boolean;  // treat as image/visual region
};

const PER_PATTERN: Record<string, Box[]> = {
  // --- covers ---
  cover_center_title: [
    { name: "title", x: 15, y: 30, w: 70, h: 22 },
    { name: "subtitle", x: 25, y: 55, w: 50, h: 10 },
    { name: "date", x: 40, y: 75, w: 20, h: 8 },
  ],
  cover_left_title: [
    { name: "title", x: 4, y: 20, w: 42, h: 28 },
    { name: "subtitle", x: 4, y: 52, w: 42, h: 14 },
    { name: "visual", x: 50, y: 4, w: 46, h: 92, accent: true },
  ],
  cover_split_image: [
    { name: "title", x: 4, y: 30, w: 40, h: 22 },
    { name: "subtitle", x: 4, y: 56, w: 40, h: 12 },
    { name: "image", x: 48, y: 2, w: 50, h: 96, accent: true },
  ],
  cover_bottom_bar: [
    { name: "visual", x: 2, y: 2, w: 96, h: 65, accent: true },
    { name: "title", x: 10, y: 72, w: 80, h: 12 },
    { name: "subtitle", x: 10, y: 86, w: 80, h: 8 },
  ],
  cover_full_bleed: [
    { name: "background", x: 0, y: 0, w: 100, h: 100, accent: true },
    { name: "title", x: 8, y: 40, w: 55, h: 20 },
    { name: "subtitle", x: 8, y: 62, w: 55, h: 10 },
  ],
  editorial_hero_split: [
    { name: "eyebrow", x: 4, y: 8, w: 30, h: 6 },
    { name: "title", x: 4, y: 18, w: 44, h: 35 },
    { name: "support", x: 4, y: 56, w: 44, h: 30 },
    { name: "visual", x: 52, y: 4, w: 44, h: 92, accent: true },
  ],
  editorial_full_bleed_campaign: [
    { name: "background", x: 0, y: 0, w: 100, h: 100, accent: true },
    { name: "title", x: 8, y: 30, w: 60, h: 22 },
    { name: "actions", x: 8, y: 58, w: 60, h: 12 },
    { name: "funnel", x: 8, y: 78, w: 60, h: 14 },
  ],

  // --- content horizontal ---
  chart_left_bullets_right: [
    { name: "content", x: 4, y: 8, w: 38, h: 84 },
    { name: "visual", x: 46, y: 8, w: 50, h: 84, accent: true },
  ],
  three_parallel_columns: [
    { name: "col-1", x: 3, y: 12, w: 30, h: 76 },
    { name: "col-2", x: 35, y: 12, w: 30, h: 76 },
    { name: "col-3", x: 67, y: 12, w: 30, h: 76 },
  ],
  bilingual_split: [
    { name: "lang-a", x: 3, y: 8, w: 46, h: 84 },
    { name: "lang-b", x: 51, y: 8, w: 46, h: 84 },
  ],
  state_machine_panel: [
    { name: "states", x: 3, y: 8, w: 30, h: 84 },
    { name: "diagram", x: 35, y: 8, w: 62, h: 84, accent: true },
  ],
  content_f_shape: [
    { name: "title", x: 4, y: 5, w: 92, h: 12 },
    { name: "content", x: 4, y: 20, w: 56, h: 65 },
    { name: "visual", x: 62, y: 20, w: 34, h: 65, accent: true },
    { name: "footer", x: 4, y: 88, w: 92, h: 8 },
  ],
  editorial_thesis_panel: [
    { name: "eyebrow", x: 4, y: 6, w: 40, h: 8 },
    { name: "thesis", x: 4, y: 18, w: 40, h: 40 },
    { name: "proof", x: 48, y: 12, w: 48, h: 72, accent: true },
    { name: "footer", x: 4, y: 88, w: 92, h: 6 },
  ],
  data_split_metric: [
    { name: "visual", x: 3, y: 6, w: 48, h: 88, accent: true },
    { name: "metric1", x: 53, y: 6, w: 44, h: 21 },
    { name: "metric2", x: 53, y: 29, w: 44, h: 21 },
    { name: "metric3", x: 53, y: 52, w: 44, h: 21 },
    { name: "caption", x: 53, y: 75, w: 44, h: 19 },
  ],
  image_gallery_grid: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "image-1", x: 3, y: 18, w: 46, h: 25, accent: true },
    { name: "image-2", x: 51, y: 18, w: 46, h: 25, accent: true },
    { name: "image-3", x: 3, y: 46, w: 46, h: 25, accent: true },
    { name: "image-4", x: 51, y: 46, w: 46, h: 25, accent: true },
    { name: "caption", x: 3, y: 76, w: 94, h: 18 },
  ],

  // --- content vertical ---
  text_top_chart_bottom: [
    { name: "content", x: 4, y: 6, w: 92, h: 25 },
    { name: "visual", x: 4, y: 34, w: 92, h: 60, accent: true },
  ],
  narrative_focus: [
    { name: "headline", x: 8, y: 8, w: 84, h: 20 },
    { name: "evidence", x: 8, y: 32, w: 84, h: 60, accent: true },
  ],
  safe_vertical_stack: [
    { name: "content", x: 6, y: 6, w: 88, h: 60 },
    { name: "support", x: 6, y: 68, w: 88, h: 26, accent: true },
  ],
  adaptive_single_column: [
    { name: "content", x: 6, y: 6, w: 88, h: 88 },
  ],
  paginated_document: [
    { name: "content", x: 6, y: 6, w: 88, h: 88 },
  ],

  // --- content grid ---
  mixed_2x2_focus: [
    { name: "chart", x: 3, y: 8, w: 46, h: 42, accent: true },
    { name: "image", x: 51, y: 8, w: 46, h: 42, accent: true },
    { name: "content", x: 3, y: 52, w: 94, h: 40 },
  ],
  content_card_grid: [
    { name: "title", x: 4, y: 4, w: 92, h: 12 },
    { name: "card-1", x: 3, y: 19, w: 30, h: 35 },
    { name: "card-2", x: 35, y: 19, w: 30, h: 35 },
    { name: "card-3", x: 67, y: 19, w: 30, h: 35 },
    { name: "card-4", x: 3, y: 57, w: 30, h: 35 },
    { name: "card-5", x: 35, y: 57, w: 30, h: 35 },
    { name: "card-6", x: 67, y: 57, w: 30, h: 35 },
  ],
  editorial_selection_shortlist: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "shortlist", x: 4, y: 18, w: 40, h: 76 },
    { name: "rationale", x: 48, y: 18, w: 26, h: 76 },
    { name: "selected", x: 76, y: 18, w: 20, h: 76, accent: true },
  ],
  editorial_reason_cards: [
    { name: "title", x: 4, y: 4, w: 92, h: 12 },
    { name: "card-1", x: 3, y: 20, w: 30, h: 72 },
    { name: "card-2", x: 35, y: 20, w: 30, h: 72 },
    { name: "card-3", x: 67, y: 20, w: 30, h: 72 },
  ],
  editorial_execution_grid: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "step-1", x: 3, y: 18, w: 46, h: 38 },
    { name: "step-2", x: 51, y: 18, w: 46, h: 38 },
    { name: "step-3", x: 3, y: 58, w: 46, h: 38 },
    { name: "step-4", x: 51, y: 58, w: 46, h: 38 },
  ],
  data_dashboard: [
    { name: "kpi-1", x: 3, y: 6, w: 30, h: 22 },
    { name: "kpi-2", x: 35, y: 6, w: 30, h: 22 },
    { name: "kpi-3", x: 67, y: 6, w: 30, h: 22 },
    { name: "chart", x: 3, y: 32, w: 62, h: 62, accent: true },
    { name: "detail", x: 67, y: 32, w: 30, h: 62 },
  ],
  editorial_inverted_impact: [
    { name: "eyebrow", x: 4, y: 4, w: 40, h: 6 },
    { name: "title", x: 4, y: 12, w: 92, h: 18 },
    { name: "col-1", x: 3, y: 34, w: 30, h: 60 },
    { name: "col-2", x: 35, y: 34, w: 30, h: 60 },
    { name: "col-3", x: 67, y: 34, w: 30, h: 60 },
  ],

  // --- radial / narrative ---
  radial_compact: [
    { name: "core", x: 38, y: 35, w: 24, h: 30 },
  ],
  linear_or_serpentine_timeline: [
    { name: "timeline", x: 5, y: 38, w: 90, h: 18 },
    { name: "content", x: 5, y: 62, w: 90, h: 30 },
  ],

  // --- timeline / steps ---
  timeline_horizontal: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "timeline", x: 4, y: 30, w: 92, h: 20 },
    { name: "nodes", x: 4, y: 55, w: 92, h: 40 },
  ],
  timeline_vertical: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "timeline", x: 10, y: 18, w: 8, h: 76 },
    { name: "nodes", x: 22, y: 18, w: 74, h: 76 },
  ],
  steps_horizontal: [
    { name: "title", x: 4, y: 4, w: 92, h: 12 },
    { name: "steps", x: 4, y: 20, w: 92, h: 74 },
  ],
  steps_vertical: [
    { name: "title", x: 4, y: 4, w: 92, h: 10 },
    { name: "steps", x: 4, y: 18, w: 50, h: 76 },
    { name: "visual", x: 56, y: 18, w: 40, h: 76, accent: true },
  ],

  // --- closings ---
  closing_centered: [
    { name: "title", x: 15, y: 30, w: 70, h: 24 },
    { name: "contact", x: 25, y: 60, w: 50, h: 14 },
  ],
  closing_split_cta: [
    { name: "title", x: 4, y: 25, w: 46, h: 50 },
    { name: "cta", x: 52, y: 25, w: 44, h: 50, accent: true },
  ],
  closing_minimal: [
    { name: "title", x: 15, y: 40, w: 70, h: 20 },
  ],
  editorial_manifesto_closer: [
    { name: "eyebrow", x: 15, y: 15, w: 70, h: 8 },
    { name: "title", x: 10, y: 30, w: 80, h: 30 },
    { name: "closer", x: 20, y: 70, w: 60, h: 12 },
  ],
};

function fallbackBoxes(family: string, zones: string[]): Box[] {
  if (family === "horizontal") {
    const w = 94 / Math.max(1, zones.length);
    return zones.map((z, i) => ({ name: z, x: 3 + w * i, y: 8, w: w - 1, h: 84 }));
  }
  if (family === "vertical") {
    const h = 88 / Math.max(1, zones.length);
    return zones.map((z, i) => ({ name: z, x: 4, y: 6 + h * i, w: 92, h: h - 1 }));
  }
  if (family === "grid") {
    const cols = Math.ceil(Math.sqrt(zones.length));
    const rows = Math.ceil(zones.length / cols);
    const cw = 94 / cols;
    const rh = 84 / rows;
    return zones.map((z, i) => ({
      name: z,
      x: 3 + (i % cols) * cw,
      y: 8 + Math.floor(i / cols) * rh,
      w: cw - 1,
      h: rh - 1,
    }));
  }
  if (family === "radial") {
    return [{ name: zones[0] ?? "core", x: 38, y: 35, w: 24, h: 30 }];
  }
  return zones.map((z, i) => ({ name: z, x: 4 + i * 8, y: 8 + i * 6, w: 40, h: 20 }));
}

function boxesFor(pattern: string, family: string, zones: string[]): Box[] {
  return PER_PATTERN[pattern] ?? fallbackBoxes(family, zones);
}

type Props = {
  pattern: string;
  family: string;
  zones: string[];
  width?: number;
  aspectRatio?: string;  // "16:9" | "4:3" | "21:9"
};

const ASPECT: Record<string, number> = { "16:9": 9 / 16, "4:3": 3 / 4, "21:9": 9 / 21 };

export function LayoutWireframe({
  pattern,
  family,
  zones,
  width = 320,
  aspectRatio = "16:9",
}: Props) {
  const ratio = ASPECT[aspectRatio] ?? ASPECT["16:9"];
  const height = Math.round(width * ratio);
  const boxes = boxesFor(pattern, family, zones);

  const showRadialDots = pattern === "radial_compact" || family === "radial";

  return (
    <div
      style={{
        position: "relative",
        width,
        height,
        background: "#fff",
        border: "1px solid #111",
        borderRadius: 3,
        boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
        flexShrink: 0,
      }}
    >
      {boxes.map((b, i) => (
        <div
          key={`${b.name}-${i}`}
          style={{
            position: "absolute",
            left: `${b.x}%`,
            top: `${b.y}%`,
            width: `${b.w}%`,
            height: `${b.h}%`,
            background: b.accent ? "#1f2937" : "#f3f4f6",
            color: b.accent ? "#e5e7eb" : "#374151",
            border: b.accent ? "1px solid #111" : "1px solid #d1d5db",
            fontFamily: "ui-sans-serif, -apple-system, sans-serif",
            fontSize: 10,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            textAlign: "center",
            overflow: "hidden",
            textTransform: "lowercase",
            letterSpacing: 0.2,
          }}
          title={b.name}
        >
          {b.name}
        </div>
      ))}
      {showRadialDots && (
        <>
          {[0, 1, 2, 3, 4, 5].map((i) => {
            const angle = (i / 6) * Math.PI * 2;
            const cx = 50 + Math.cos(angle) * 34;
            const cy = 50 + Math.sin(angle) * 38;
            return (
              <div
                key={`dot-${i}`}
                style={{
                  position: "absolute",
                  left: `${cx}%`,
                  top: `${cy}%`,
                  width: 10,
                  height: 10,
                  marginLeft: -5,
                  marginTop: -5,
                  borderRadius: "50%",
                  background: "#9ca3af",
                  border: "1px solid #6b7280",
                }}
              />
            );
          })}
        </>
      )}
    </div>
  );
}
