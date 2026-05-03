export type LayoutPatternInfo = {
  label: string;
  bestFor: string;
  caution?: string;
};

const PATTERN_INFO: Record<string, LayoutPatternInfo> = {
  chart_left_bullets_right: {
    label: "Visual plus explanation",
    bestFor: "A chart, image, or diagram with a short explanatory argument beside it.",
  },
  text_top_chart_bottom: {
    label: "Text over visual",
    bestFor: "One clear takeaway followed by a large chart, proof image, or evidence panel.",
  },
  three_parallel_columns: {
    label: "Three equal columns",
    bestFor: "Three comparable ideas, examples, groups, or pillars with similar weight.",
  },
  mixed_2x2_focus: {
    label: "Mixed evidence grid",
    bestFor: "A few mixed blocks, such as chart plus image plus explanatory text.",
  },
  radial_compact: {
    label: "Central idea map",
    bestFor: "One core concept with related points orbiting around it.",
    caution: "Avoid for timelines or dense paragraphs.",
  },
  linear_or_serpentine_timeline: {
    label: "Narrative timeline",
    bestFor: "A sequence, journey, process, or before-after story that should read left to right.",
  },
  adaptive_single_column: {
    label: "Single-column note",
    bestFor: "A simple text-forward slide where hierarchy matters more than composition.",
  },
  state_machine_panel: {
    label: "State machine panel",
    bestFor: "States, transitions, workflows, or system diagrams with supporting labels.",
  },
  narrative_focus: {
    label: "Headline and proof",
    bestFor: "A strong editorial claim with one supporting evidence area.",
  },
  paginated_document: {
    label: "Document page",
    bestFor: "Dense prose, policy, source excerpts, or report-like content.",
    caution: "Use sparingly in presentation decks.",
  },
  bilingual_split: {
    label: "Bilingual split",
    bestFor: "The same idea shown in two languages or two parallel text treatments.",
  },
  safe_vertical_stack: {
    label: "Vertical stack",
    bestFor: "Text-first content with a smaller support area underneath.",
  },
  content_f_shape: {
    label: "Report F-shape",
    bestFor: "A title, primary text block, supporting visual, and footer note.",
  },
  content_card_grid: {
    label: "Card grid",
    bestFor: "Four to six compact cards, features, examples, or categories.",
  },
  editorial_thesis_panel: {
    label: "Thesis and proof",
    bestFor: "A crisp thesis on the left with a larger proof, quote, or exhibit on the right.",
  },
  editorial_selection_shortlist: {
    label: "Shortlist decision",
    bestFor: "Comparing options, rationale, and one selected recommendation.",
  },
  editorial_reason_cards: {
    label: "Three reason cards",
    bestFor: "Three editorial reasons or arguments with equal emphasis.",
  },
  editorial_execution_grid: {
    label: "Execution grid",
    bestFor: "Steps, actions, roadmap blocks, or implementation workstreams.",
  },
  data_dashboard: {
    label: "Dashboard",
    bestFor: "KPIs, a primary chart, and a detail panel on one analytical slide.",
  },
  data_split_metric: {
    label: "Visual with metrics",
    bestFor: "One big visual or chart paired with stacked numeric callouts.",
  },
  image_gallery_grid: {
    label: "Image gallery",
    bestFor: "Multiple images, specimens, screenshots, or visual examples with a caption.",
  },
  editorial_inverted_impact: {
    label: "Impact columns",
    bestFor: "A big impact statement followed by three supporting columns.",
  },
  cover_center_title: {
    label: "Centered title cover",
    bestFor: "A formal title slide with balanced title, subtitle, and date.",
  },
  cover_left_title: {
    label: "Left title cover",
    bestFor: "A title-led cover with room for a strong visual on the right.",
  },
  cover_split_image: {
    label: "Split-image cover",
    bestFor: "A cover where the image should carry about half the slide.",
  },
  cover_bottom_bar: {
    label: "Image with title bar",
    bestFor: "A large hero image with title information anchored below it.",
  },
  cover_full_bleed: {
    label: "Full-bleed cover",
    bestFor: "An immersive image-led cover with text over the background.",
    caution: "Needs a strong image or visual concept.",
  },
  editorial_hero_split: {
    label: "Editorial hero split",
    bestFor: "A magazine-like opening with title, support copy, and hero visual.",
  },
  editorial_full_bleed_campaign: {
    label: "Campaign cover",
    bestFor: "A bold full-screen campaign message with actions or funnel cues.",
  },
  closing_centered: {
    label: "Centered closing",
    bestFor: "A balanced closing slide with contact or final note.",
  },
  closing_split_cta: {
    label: "Split call to action",
    bestFor: "A closing slide that pairs a final message with a clear action area.",
  },
  closing_minimal: {
    label: "Minimal closing",
    bestFor: "A quiet ending with one final sentence or title.",
  },
  editorial_manifesto_closer: {
    label: "Manifesto closer",
    bestFor: "A more editorial final statement with an eyebrow and closing line.",
  },
  timeline_horizontal: {
    label: "Horizontal timeline",
    bestFor: "Milestones, chronology, phases, or events across time.",
  },
  timeline_vertical: {
    label: "Vertical timeline",
    bestFor: "A longer sequence that benefits from top-to-bottom scanning.",
  },
  steps_horizontal: {
    label: "Horizontal steps",
    bestFor: "Three to five ordered steps that should feel fast and linear.",
  },
  steps_vertical: {
    label: "Vertical steps with visual",
    bestFor: "Ordered steps paired with a supporting visual or process diagram.",
  },
};

function humanizePatternId(patternId: string): string {
  return patternId
    .split("_")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function layoutPatternInfo(patternId: string): LayoutPatternInfo {
  return (
    PATTERN_INFO[patternId] ?? {
      label: humanizePatternId(patternId),
      bestFor: "A catalog layout pattern available for this deck.",
    }
  );
}
