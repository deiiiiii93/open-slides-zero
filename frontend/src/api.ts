// Thin fetch wrapper. Vite dev server proxies /api/* to FastAPI on :8765.
// In production, point API_BASE at the deployed backend.

const API_BASE = "/api";

async function http<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(API_BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// --- Types ---

export type Material = {
  kind: "text" | "file" | "image";
  uri: string;
  name?: string;
  parsed?: string | null;
  note?: string;
};

export type DeckState = {
  thread_id: string;
  checkpoint_id?: string;
  source_thread_id?: string;
  values: Record<string, any>;
  next: string[];
  interrupts: any[];
};

export type CatalogResponse = {
  scenarios: Array<{ id: string; name_en: string; name_zh: string; structures: string[] }>;
  structures: Array<{ id: string; name_en: string; name_zh: string; description_en: string }>;
  patterns: Record<string, { family: string; kind: string; zones: string[] }>;
  visual_style_presets: Array<{
    id: string;
    label: string;
    description: string;
    prompt: string;
    style_bias?: Record<string, string>;
    layout_bias?: { prefer?: string[]; avoid?: string[] };
    html_rules?: string[];
  }>;
};

// --- Endpoints ---

export type CreateDeckBody = {
  deck_name?: string | null;
  expected_pages: number;
  aspect_ratio?: string;
  density_preference?: string;
  language?: string;
  visual_style_preference?: string | null;
  visual_style_preset_id?: string | null;
  style_reference_image_uri?: string | null;
  materials: Material[];
};

export const STREAM_BASE = API_BASE;

export type DeckListItem = {
  thread_id: string;
  deck_name: string;
  stage: string;
  created_at: string | null;
};

export type PlaygroundLane = {
  lane_id: string;
  lane_thread_id: string;
  creator_prompt: string;
  cutoff: boolean;
  created_at: string;
  state: DeckState | null;
};

export type Masterpiece = {
  id: string;
  prompt: string;
  created_at: string;
};

export type ForkFromStructureBody = {
  review_stage: "structure";
  scenario_id: string;
  structure_id: string;
  deck_name?: string | null;
};

export type ForkFromStyleBody = {
  review_stage: "style";
  feedback: string;
  deck_name?: string | null;
};

export type ForkFromLayoutBody = {
  review_stage: "layout";
  overrides: Record<number, string>;
  visual_style_preset_id?: string | null;
  deck_name?: string | null;
};

export type ForkFromReviewBody =
  | ForkFromStructureBody
  | ForkFromStyleBody
  | ForkFromLayoutBody;

export const api = {
  listDecks: () => http<{ decks: DeckListItem[] }>("GET", "/decks"),

  createDeck: (body: CreateDeckBody) => http<DeckState>("POST", "/decks", body),

  getDeck: (id: string) => http<DeckState>("GET", `/decks/${id}`),

  getCatalog: (id: string) => http<CatalogResponse>("GET", `/decks/${id}/catalog`),

  resume: (id: string, payload: Record<string, unknown>) =>
    http<DeckState>("POST", `/decks/${id}/resume`, payload),

  getSlide: (id: string, idx: number) =>
    http<{ slide_idx: number; html: string }>("GET", `/decks/${id}/slides/${idx}`),

  addComment: (id: string, slideIdx: number, text: string, box?: { x: number; y: number; w: number; h: number }) =>
    http("POST", `/decks/${id}/slides/${slideIdx}/comments`, { text, box }),

  applyEdits: (id: string) => http("POST", `/decks/${id}/apply_edits`),

  commentStreamUrl: (id: string, slideIdx: number) =>
    `${STREAM_BASE}/decks/${id}/slides/${slideIdx}/comments/stream`,

  regenerate: (id: string, from_stage: string, patch?: Record<string, any>, affected_slides?: number[]) =>
    http<DeckState>("POST", `/decks/${id}/regenerate`, { from_stage, patch, affected_slides }),

  forkFromReview: (id: string, body: ForkFromReviewBody) =>
    http<DeckState>("POST", `/decks/${id}/fork_from_review`, body),

  history: (id: string) =>
    http<{ thread_id: string; history: Array<{ checkpoint_id: string; stage: string; created_at: string }> }>(
      "GET",
      `/decks/${id}/history`,
    ),

  rewind: (id: string, checkpoint_id: string) =>
    http<DeckState>("POST", `/decks/${id}/rewind`, { checkpoint_id }),

  listPlaygroundLanes: (id: string) =>
    http<{ max_lanes: number; lanes: PlaygroundLane[] }>("GET", `/decks/${id}/playground/lanes`),

  createPlaygroundLaneStreamUrl: (id: string) =>
    `${STREAM_BASE}/decks/${id}/playground/lanes/stream`,

  cutoffPlaygroundLane: (id: string, laneId: string) =>
    http<{ ok: boolean; lane: PlaygroundLane }>("POST", `/decks/${id}/playground/lanes/${laneId}/cutoff`, {}),

  saveLaneMasterpiece: (id: string, laneId: string) =>
    http<{ ok: boolean; masterpiece: Masterpiece }>("POST", `/decks/${id}/playground/lanes/${laneId}/masterpiece`, {}),

  listMasterpieces: () =>
    http<{ masterpieces: Masterpiece[] }>("GET", "/masterpieces"),

  deleteMasterpiece: (id: string) =>
    http<{ ok: boolean }>("DELETE", `/masterpieces/${id}`),
};
