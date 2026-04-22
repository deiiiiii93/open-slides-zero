// Thin fetch wrapper. Vite dev server proxies /api/* to FastAPI on :8000.
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

export type Material = { kind: "text" | "file" | "image"; uri: string; note?: string };

export type DeckState = {
  thread_id: string;
  checkpoint_id?: string;
  values: Record<string, any>;
  next: string[];
  interrupts: any[];
};

export type CatalogResponse = {
  scenarios: Array<{ id: string; name_en: string; name_zh: string; structures: string[] }>;
  structures: Array<{ id: string; name_en: string; name_zh: string; description_en: string }>;
  patterns: Record<string, { family: string; kind: string; zones: string[] }>;
};

// --- Endpoints ---

export type CreateDeckBody = {
  deck_name?: string | null;
  expected_pages: number;
  aspect_ratio?: string;
  density_preference?: string;
  language?: string;
  visual_style_preference?: string | null;
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

  history: (id: string) =>
    http<{ thread_id: string; history: Array<{ checkpoint_id: string; stage: string; created_at: string }> }>(
      "GET",
      `/decks/${id}/history`,
    ),

  rewind: (id: string, checkpoint_id: string) =>
    http<DeckState>("POST", `/decks/${id}/rewind`, { checkpoint_id }),
};
