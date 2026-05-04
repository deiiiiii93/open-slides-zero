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

export type ImageAsset = {
  asset_id: string;
  uri: string;
  name?: string | null;
  source?: "user" | "generated" | string;
  media_type?: string;
  summary?: string;
  prompt?: string;
};

export type ImageSlot = {
  slot_id: string;
  slide_idx: number;
  slot_index: number;
  hint: string;
};

export type ImageMapping = {
  slot_id: string;
  asset_id: string;
  confidence?: number;
  rationale?: string;
};

export type UnmatchedImageSlot = {
  slot_id: string;
  slide_idx: number;
  hint: string;
  prompt: string;
  reason?: string;
};

export type ImageInsertionPlan = {
  status: string;
  slots: ImageSlot[];
  assets: ImageAsset[];
  mappings: ImageMapping[];
  applied_mappings?: ImageMapping[];
  unmatched_slots: UnmatchedImageSlot[];
};

export type DeckState = {
  thread_id: string;
  checkpoint_id?: string;
  source_thread_id?: string;
  values: Record<string, any>;
  next: string[];
  interrupts: any[];
  tasks?: Array<{ id?: string | null; name?: string | null; error?: string | null }>;
};

export type AdvancedChatMessage = {
  role: "user" | "assistant";
  content: string;
  choices?: AdvancedChatChoice[];
};

export type AdvancedChatChoice = {
  label: string;
  description?: string;
  message: string;
};

export type AdvancedChatDraft = {
  scenario_id?: string;
  structure_id?: string;
  language?: string;
  summary?: string;
  outline_md?: string;
  outline_slides?: Array<Record<string, any>>;
  visual_style_md?: string;
  visual_style?: Record<string, any>;
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

export type AgentMode = "default" | "advanced";

export type CreateDeckBody = {
  deck_name?: string | null;
  expected_pages: number;
  aspect_ratio?: string;
  density_preference?: string;
  agent_mode?: AgentMode;
  language?: string;
  visual_style_preference?: string | null;
  visual_style_preset_id?: string | null;
  style_reference_image_uri?: string | null;
  image_urls?: string[];
  model_overrides?: Partial<Record<ModelStage, string>>;
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

export type PlaygroundModelOption = {
  id: string;
  label: string;
  description: string;
};

export type ModelStage = "style" | "layout" | "html";

export type PlaygroundModelStageOptions = {
  label: string;
  default_model: string;
  options: PlaygroundModelOption[];
};

export type PlaygroundModelOptions = {
  stages: Record<ModelStage, PlaygroundModelStageOptions>;
};

export type ModelOptions = PlaygroundModelOptions;

export type CreatePlaygroundLaneBody = {
  creator_prompt: string;
  model_overrides?: Partial<Record<ModelStage, string>>;
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

  advancedChatStreamUrl: (id: string) =>
    `${STREAM_BASE}/decks/${id}/advanced_chat/stream`,

  regenerate: (id: string, from_stage: string, patch?: Record<string, any>, affected_slides?: number[]) =>
    http<DeckState>("POST", `/decks/${id}/regenerate`, { from_stage, patch, affected_slides }),

  forkFromReview: (id: string, body: ForkFromReviewBody) =>
    http<DeckState>("POST", `/decks/${id}/fork_from_review`, body),

  planImages: (id: string) =>
    http<{ ok: boolean; plan: ImageInsertionPlan; state: DeckState }>("POST", `/decks/${id}/images/plan`, {}),

  applyImages: (id: string, mappings: ImageMapping[]) =>
    http<{ ok: boolean; applied_mappings: ImageMapping[]; state: DeckState }>(
      "POST",
      `/decks/${id}/images/apply`,
      { mappings },
    ),

  generateImage: (id: string, slide_idx: number, slot_id: string, prompt: string) =>
    http<{ ok: boolean; asset: ImageAsset; state: DeckState }>(
      "POST",
      `/decks/${id}/images/generate`,
      { slide_idx, slot_id, prompt },
    ),

  generateImages: (id: string, items: Array<{ slide_idx: number; slot_id: string; prompt: string }>) =>
    http<{ ok: boolean; assets: ImageAsset[]; errors: Array<Record<string, any>>; state: DeckState }>(
      "POST",
      `/decks/${id}/images/generate_batch`,
      { items },
    ),

  imageAssetContentUrl: (id: string, assetId: string) =>
    `${API_BASE}/decks/${id}/images/assets/${encodeURIComponent(assetId)}/content`,

  history: (id: string) =>
    http<{ thread_id: string; history: Array<{ checkpoint_id: string; stage: string; created_at: string }> }>(
      "GET",
      `/decks/${id}/history`,
    ),

  rewind: (id: string, checkpoint_id: string) =>
    http<DeckState>("POST", `/decks/${id}/rewind`, { checkpoint_id }),

  listPlaygroundLanes: (id: string) =>
    http<{ max_lanes: number; lanes: PlaygroundLane[] }>("GET", `/decks/${id}/playground/lanes`),

  listPlaygroundModelOptions: () =>
    http<PlaygroundModelOptions>("GET", "/playground/model-options"),

  listModelOptions: () =>
    http<ModelOptions>("GET", "/playground/model-options"),

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
