// Top-level workflow UI with SSE streaming.
//
//   1. Create deck form (materials + expected pages + aspect)
//   2. LiveStream pane shows tokens as each subagent generates
//   3. HITL panels when an interrupt arrives; markdown-rendered
//   4. Deck canvas + comment overlay once slides are rendered
//   5. History + regenerate controls

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdvancedChatPanel } from "./AdvancedChatPanel";
import { DeckCanvas } from "./DeckCanvas";
import { CommentLayer } from "./CommentLayer";
import {
  BriefReview,
  HitlReviewPanel,
  LayoutStage,
  StructureStage,
  StyleStage,
} from "./HitlReviewPanel";
import { LiveStream } from "./LiveStream";
import { Markdown } from "./Markdown";
import { PlaygroundPanel } from "./PlaygroundPanel";
import {
  api,
  STREAM_BASE,
  type AgentMode,
  type AdvancedChatDraft,
  type CatalogResponse,
  type CreateDeckBody,
  type DeckListItem,
  type DeckState,
  type ForkFromReviewBody,
  type ImageAsset,
  type ImageInsertionPlan,
  type ImageMapping,
  type Masterpiece,
  type Material,
  type ModelStage,
  type RuntimeModelOptions,
  type ShareDeckResponse,
  type SharedDeckResponse,
  type ThinkingEffort,
} from "./api";
import { streamSSE, type StreamEvent } from "./sse";
import {
  exportHtmlSingle,
  exportHtmlZip,
  exportPngZip,
  exportPptx,
  hasExportableSlides,
} from "./exporter";
import {
  DEFAULT_ZENMUX_BASE_URL,
  hasRuntimeZenmuxKey,
  readRuntimeConfig,
  type RuntimeConfig,
  writeRuntimeConfig,
} from "./runtimeConfig";

const CANVAS_W = 960;
const CANVAS_H = 540;
type ReviewStage = "structure" | "style" | "layout" | "brief" | "ready";
type DeckHistoryEntry = { id: string; name: string };
type StoredDeckHistoryEntry = DeckHistoryEntry | string;
const REVIEW_STAGES: Array<{ id: ReviewStage; label: string }> = [
  { id: "structure", label: "Structure" },
  { id: "style", label: "Style" },
  { id: "layout", label: "Layout" },
  { id: "brief", label: "Brief" },
  { id: "ready", label: "Final deck" },
];
const SUPPORTED_UPLOAD_ACCEPT = ".txt,.md,.markdown,.pdf,.pptx,.jpg,.jpeg,.png,.docx,.xlsx";
const SUPPORTED_UPLOAD_EXTENSIONS = new Set(
  SUPPORTED_UPLOAD_ACCEPT.split(",").map((ext) => ext.toLowerCase()),
);
const MODEL_STAGE_ORDER: ModelStage[] = ["style", "layout", "html"];
const RECENT_DECKS_PAGE_SIZE = 5;
const SESSION_DECK_HISTORY_KEY = "osz.session.history";

function isSupportedUpload(file: File): boolean {
  const dot = file.name.lastIndexOf(".");
  if (dot === -1) return false;
  return SUPPORTED_UPLOAD_EXTENSIONS.has(file.name.slice(dot).toLowerCase());
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function materialLabel(material: Material): string {
  if (material.name) return material.name;
  if (material.kind === "text") return "Pasted text";
  return material.uri;
}

function splitImageUrls(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function deckWithBaseSlides(deck: DeckState): DeckState {
  const base = deck.values?.html_slides_base as Record<number, string> | undefined;
  if (!base || Object.keys(base).length === 0) return deck;
  return {
    ...deck,
    values: {
      ...deck.values,
      html_slides: base,
    },
  };
}

function hasOriginalSlides(deck: DeckState | null): boolean {
  const base = deck?.values?.html_slides_base as Record<string, string> | undefined;
  return Boolean(base && Object.keys(base).length > 0);
}

function firstInterruptPayload(deck: DeckState | null): any {
  const interrupt = deck?.interrupts?.[0];
  if (!interrupt) return null;
  return typeof interrupt === "object" && "value" in interrupt ? (interrupt as any).value : interrupt;
}

function selectedModelOverrides(
  overrides: Partial<Record<ModelStage, string>>,
): Partial<Record<ModelStage, string>> | undefined {
  const entries = MODEL_STAGE_ORDER
    .map((stage) => [stage, overrides[stage]?.trim()] as const)
    .filter((entry): entry is readonly [ModelStage, string] => Boolean(entry[1]));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function selectedThinkingEffortOverrides(
  overrides: Partial<Record<ModelStage, ThinkingEffort>>,
): Partial<Record<ModelStage, ThinkingEffort>> | undefined {
  const entries = MODEL_STAGE_ORDER
    .map((stage) => [stage, overrides[stage]] as const)
    .filter((entry): entry is readonly [ModelStage, ThinkingEffort] => Boolean(entry[1]));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function recentDeckDateText(createdAt: string | null): string {
  if (!createdAt) return "";
  return new Date(createdAt).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function recentDeckSearchText(deck: DeckListItem): string {
  return [
    deck.deck_name,
    deck.thread_id,
    deck.stage,
    recentDeckDateText(deck.created_at),
    deck.created_at,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function readDeckHistory(): DeckHistoryEntry[] {
  try {
    const raw = JSON.parse(sessionStorage.getItem(SESSION_DECK_HISTORY_KEY) || "[]") as StoredDeckHistoryEntry[];
    if (!Array.isArray(raw)) return [];
    const seen = new Set<string>();
    const entries: DeckHistoryEntry[] = [];
    for (const item of raw) {
      const entry = typeof item === "string" ? { id: item, name: item } : item;
      if (!entry?.id || seen.has(entry.id)) continue;
      seen.add(entry.id);
      entries.push({ id: entry.id, name: entry.name || entry.id });
    }
    return entries;
  } catch {
    return [];
  }
}

function writeDeckHistory(entries: DeckHistoryEntry[]) {
  sessionStorage.setItem(SESSION_DECK_HISTORY_KEY, JSON.stringify(entries));
}

function sessionDeckList(
  history: DeckHistoryEntry[],
  backendDecks: DeckListItem[] | null,
): DeckListItem[] {
  const backendById = new Map((backendDecks ?? []).map((d) => [d.thread_id, d]));
  return history.map((entry) => {
    const backendDeck = backendById.get(entry.id);
    return backendDeck ?? {
      thread_id: entry.id,
      deck_name: entry.name,
      stage: "",
      created_at: null,
    };
  });
}

function isDeckAccessError(error: unknown): boolean {
  const text = String(error);
  return text.includes("GET /decks/") && text.includes("→ 404:");
}

export function App() {
  const [deck, setDeck] = useState<DeckState | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [currentSlide, setCurrentSlide] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showOwnedHistory, setShowOwnedHistory] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [showShare, setShowShare] = useState(false);
  const [showMasterpieces, setShowMasterpieces] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [shareInfo, setShareInfo] = useState<ShareDeckResponse | null>(null);
  const [sharedDeck, setSharedDeck] = useState<SharedDeckResponse | null>(null);
  const [requestedShareId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("share"));
  const [forkingShare, setForkingShare] = useState(false);
  const [deckList, setDeckList] = useState<DeckListItem[] | null>(null);
  const [deckHistory, setDeckHistory] = useState<DeckHistoryEntry[]>(() => readDeckHistory());
  const [deletingDeckId, setDeletingDeckId] = useState<string | null>(null);
  const [runtimeModelOptions, setRuntimeModelOptions] = useState<RuntimeModelOptions | null>(null);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig>(() => readRuntimeConfig());
  const [showConfig, setShowConfig] = useState(false);
  const [identityReady, setIdentityReady] = useState(false);
  const [selectedReviewStage, setSelectedReviewStage] = useState<ReviewStage>("ready");

  // Streaming state
  const [buffersByTag, setBuffersByTag] = useState<Record<string, string>>({});
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState<number | null>(null);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [elapsedByTag, setElapsedByTag] = useState<Record<string, number>>({});
  const tagStartRef = useRef<Record<string, number>>({});
  const abortRef = useRef<AbortController | null>(null);

  const updateDeckHistory = useCallback((entries: DeckHistoryEntry[]) => {
    writeDeckHistory(entries);
    setDeckHistory(entries);
  }, []);

  const rememberDeck = useCallback((nextDeck: DeckState) => {
    const name = (nextDeck.values?.deck_name as string) || nextDeck.thread_id;
    const hist = readDeckHistory();
    const filtered = hist.filter((h) => h.id !== nextDeck.thread_id);
    updateDeckHistory([{ id: nextDeck.thread_id, name }, ...filtered].slice(0, 20));
  }, [updateDeckHistory]);

  const forgetDecks = useCallback((threadIds: string[]) => {
    const deleted = new Set(threadIds);
    updateDeckHistory(readDeckHistory().filter((entry) => !deleted.has(entry.id)));
  }, [updateDeckHistory]);

  const refresh = useCallback(
    async (id: string) => {
      try {
        const d = await api.getDeck(id);
        setDeck(d);
        rememberDeck(d);
        if (!catalog) setCatalog(await api.getCatalog(id));
      } catch (e) {
        if (isDeckAccessError(e)) {
          localStorage.removeItem("osz.thread_id");
          forgetDecks([id]);
          setDeck(null);
          setErr("This deck is not available in this browser session.");
          return;
        }
        throw e;
      }
    },
    [catalog, forgetDecks, rememberDeck],
  );

  const updateRuntimeConfig = useCallback((next: RuntimeConfig) => {
    setRuntimeConfig(next);
    writeRuntimeConfig(next);
    setErr(null);
  }, []);

  const refreshDeckList = useCallback(async () => {
    if (!identityReady) return;
    try {
      const { decks } = await api.listDecks();
      setDeckList(decks);
    } catch {
      setDeckList([]);
    }
  }, [identityReady]);

  useEffect(() => {
    void api.ensureIdentity()
      .then(() => setIdentityReady(true))
      .catch((e) => {
        setErr(`Could not initialize private deck identity: ${String(e)}`);
        setIdentityReady(true);
      });
  }, []);

  useEffect(() => {
    if (!requestedShareId) return;
    localStorage.removeItem("osz.thread_id");
    setDeck(null);
    setErr(null);
    void api.getSharedDeck(requestedShareId)
      .then(setSharedDeck)
      .catch((e) => setErr(String(e)));
  }, [requestedShareId]);

  useEffect(() => {
    if (!identityReady) return;
    if (requestedShareId) return;
    if (sharedDeck) return;
    const saved = localStorage.getItem("osz.thread_id");
    if (saved) void refresh(saved).catch((e) => setErr(String(e)));
  }, [identityReady, refresh, requestedShareId, sharedDeck]);

  useEffect(() => {
    if (!catalog) void api.getCatalog("catalog").then(setCatalog).catch(() => undefined);
  }, [catalog]);

  useEffect(() => {
    if (!runtimeModelOptions) void api.listRuntimeModelOptions().then(setRuntimeModelOptions).catch(() => undefined);
  }, [runtimeModelOptions]);

  useEffect(() => {
    if (!deck && identityReady) void refreshDeckList();
  }, [deck, identityReady, refreshDeckList]);

  useEffect(() => {
    setSelectedReviewStage("ready");
    setCurrentSlide(0);
  }, [deck?.thread_id]);

  useEffect(() => {
    if ((deck?.values?.current_stage as string | undefined) !== "ready" || (deck?.interrupts?.length ?? 0) > 0) {
      setSelectedReviewStage("ready");
    }
  }, [deck?.values?.current_stage, deck?.interrupts?.length]);

  useEffect(() => {
    if (deck) rememberDeck(deck);
  }, [deck?.thread_id, rememberDeck]);

  // Handle a single SSE event stream to completion.
  async function consumeStream(url: string, body: unknown | FormData): Promise<void> {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBuffersByTag({});
    setActiveNode(null);
    setActiveSlide(null);
    setActiveModel(null);
    setElapsedByTag({});
    tagStartRef.current = {};
    setErr(null);
    setBusy(true);

    try {
      for await (const ev of streamSSE(url, body, ctrl.signal)) {
        applyEvent(ev);
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") setErr(String(e));
    } finally {
      setBusy(false);
      setActiveNode(null);
      setActiveSlide(null);
      setActiveModel(null);
    }
  }

  function applyEvent(ev: StreamEvent) {
    switch (ev.type) {
      case "thread": {
        localStorage.setItem("osz.thread_id", ev.thread_id);
        if (!catalog) void api.getCatalog(ev.thread_id).then(setCatalog);
        break;
      }
      case "event": {
        setActiveNode(ev.node);
        setActiveSlide(ev.slide_idx ?? null);
        if (ev.model) setActiveModel(ev.model);
        break;
      }
      case "token": {
        const tag = ev.tag ?? "unknown";
        setBuffersByTag((b) => {
          const next = { ...b, [tag]: (b[tag] ?? "") + ev.text };
          // Record start time on first token for this tag
          if (!tagStartRef.current[tag]) {
            tagStartRef.current = { ...tagStartRef.current, [tag]: Date.now() };
          }
          // Finalize elapsed when a slide buffer completes with </html>
          if (tag.startsWith("html:") && next[tag].includes("</html>") && !elapsedByTag[tag]) {
            const elapsed = Date.now() - (tagStartRef.current[tag] ?? Date.now());
            setElapsedByTag((prev) => ({ ...prev, [tag]: elapsed }));
          }
          return next;
        });
        break;
      }
      case "update": {
        // Opportunistically merge patch into deck.values so markdown previews
        // appear as soon as each node commits.
        setDeck((prev) =>
          prev ? { ...prev, values: mergeDeckValues(prev.values, ev.patch) } : prev,
        );
        break;
      }
      case "interrupt": {
        setDeck((prev) =>
          prev ? { ...prev, interrupts: [ev.payload] } : prev,
        );
        break;
      }
      case "done": {
        setDeck(ev.state);
        if ((ev.state?.values?.current_stage as string | undefined) === "advanced_chat") {
          setBuffersByTag((prev) => {
            if (!prev.advanced_chat) return prev;
            const next = { ...prev };
            delete next.advanced_chat;
            return next;
          });
        }
        // Finalize elapsed for any tags that didn't hit </html>
        setElapsedByTag((prev) => {
          const next = { ...prev };
          for (const [tag, start] of Object.entries(tagStartRef.current)) {
            if (!next[tag]) {
              next[tag] = Date.now() - start;
            }
          }
          return next;
        });
        rememberDeck(ev.state);
        break;
      }
      case "comment_saved": {
        // Echo into the LiveStream so the user sees their comment was stored
        // before the regen cycle starts. Tagged as "comment" so it renders in
        // the outline/style column area.
        setBuffersByTag((b) => ({
          ...b,
          comment: `💬 slide ${ev.slide_idx + 1}: ${ev.text}\n`,
        }));
        break;
      }
      case "error": {
        setErr(ev.message);
        break;
      }
    }
  }

  // Strip the _truncated / _repr sentinels the backend uses for heavy fields.
  function sanitize(patch: Record<string, any>): Record<string, any> {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(patch)) {
      if (v && typeof v === "object" && ("_truncated" in v || "_repr" in v)) continue;
      out[k] = v;
    }
    return out;
  }

  function mergeDeckValues(
    prevValues: Record<string, any>,
    patch: Record<string, any>,
  ): Record<string, any> {
    const next = { ...prevValues, ...sanitize(patch) };
    if ("html_slides" in patch) {
      const incoming = patch.html_slides;
      if (incoming && typeof incoming === "object" && !Array.isArray(incoming)) {
        const htmlEntries = Object.entries(incoming).filter(
          ([key, value]) => Number.isInteger(Number(key)) && typeof value === "string",
        );
        if (htmlEntries.length > 0) {
          next.html_slides = {
            ...(prevValues.html_slides ?? {}),
            ...Object.fromEntries(htmlEntries),
          };
        } else {
          next.html_slides = prevValues.html_slides ?? {};
        }
      }
    }
    return next;
  }

  // --- Create / resume handlers ---

  async function onCreate(form: {
    deckName: string;
    text: string;
    pages: number;
    aspect: string;
    density: string;
    agentMode: AgentMode;
    styleHint: string;
    visualStylePresetId: string | null;
    imageUrls: string[];
    modelOverrides: Partial<Record<ModelStage, string>>;
    thinkingEffortOverrides: Partial<Record<ModelStage, ThinkingEffort>>;
    files: File[];
  }) {
    const modelOverrides = selectedModelOverrides(form.modelOverrides);
    const thinkingEffortOverrides = selectedThinkingEffortOverrides(form.thinkingEffortOverrides);
    if (form.files.length > 0) {
      const body = new FormData();
      if (form.deckName.trim()) body.append("deck_name", form.deckName.trim());
      if (form.text.trim()) body.append("text", form.text);
      body.append("expected_pages", String(form.pages));
      body.append("aspect_ratio", form.aspect);
      body.append("density_preference", form.density);
      body.append("agent_mode", form.agentMode);
      body.append("language", "en");
      if (form.styleHint.trim()) {
        body.append("visual_style_preference", form.styleHint.trim());
      }
      if (form.visualStylePresetId) {
        body.append("visual_style_preset_id", form.visualStylePresetId);
      }
      if (form.imageUrls.length > 0) {
        body.append("image_urls", JSON.stringify(form.imageUrls));
      }
      if (modelOverrides) {
        body.append("model_overrides", JSON.stringify(modelOverrides));
      }
      if (thinkingEffortOverrides) {
        body.append("thinking_effort_overrides", JSON.stringify(thinkingEffortOverrides));
      }
      for (const file of form.files) {
        body.append("files", file);
      }
      await consumeStream(`${STREAM_BASE}/decks/upload/stream`, body);
      return;
    }
    const mats: Material[] = [];
    if (form.text.trim()) mats.push({ kind: "text", uri: `text:${form.text}` });
    const derivedName = form.text.trim().split("\n")[0].slice(0, 60) || null;
    const body: CreateDeckBody = {
      deck_name: form.deckName.trim() || derivedName,
      expected_pages: form.pages,
      aspect_ratio: form.aspect,
      density_preference: form.density,
      agent_mode: form.agentMode,
      language: "en",
      visual_style_preference: form.styleHint || null,
      visual_style_preset_id: form.visualStylePresetId,
      image_urls: form.imageUrls,
      model_overrides: modelOverrides,
      thinking_effort_overrides: thinkingEffortOverrides,
      materials: mats,
    };
    await consumeStream(`${STREAM_BASE}/decks/stream`, body);
  }

  async function onResume(payload: Record<string, unknown>) {
    if (!deck) return;
    await consumeStream(
      `${STREAM_BASE}/decks/${deck.thread_id}/resume/stream`,
      { payload },
    );
  }

  async function onAdvancedChat(message: string) {
    if (!deck) return;
    await consumeStream(api.advancedChatStreamUrl(deck.thread_id), { message });
  }

  async function onAdvancedChatContinue(draft: AdvancedChatDraft) {
    await onResume({ approved: true, draft });
  }

  async function onComment(text: string, box: { x: number; y: number; w: number; h: number }) {
    if (!deck) return;
    if (busy || deck.values?.current_stage !== "ready" || (deck.interrupts?.length ?? 0) > 0) {
      setErr("Comments can be applied after the deck is ready.");
      return;
    }
    // Stream the combined add-comment + apply-edits so the LiveStream pane
    // shows live token output while the LLM regenerates affected slides.
    await consumeStream(api.commentStreamUrl(deck.thread_id, currentSlide), { text, box });
  }

  async function onForkFromReview(body: ForkFromReviewBody) {
    if (!deck) return;
    abortRef.current?.abort();
    setBuffersByTag({});
    setActiveNode(null);
    setActiveSlide(null);
    setElapsedByTag({});
    tagStartRef.current = {};
    setErr(null);
    setBusy(true);
    try {
      const nextDeck = await api.forkFromReview(deck.thread_id, body);
      localStorage.setItem("osz.thread_id", nextDeck.thread_id);
      rememberDeck(nextDeck);
      setDeck(nextDeck);
      void refreshDeckList();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onNewDeck() {
    abortRef.current?.abort();
    localStorage.removeItem("osz.thread_id");
    setDeck(null);
    setSharedDeck(null);
    setErr(null);
    setBuffersByTag({});
    setShowMasterpieces(false);
    setShowOwnedHistory(false);
    setShowShare(false);
    window.history.replaceState({}, "", window.location.pathname);
  }

  async function onShareDeck() {
    if (!deck) return;
    setErr(null);
    try {
      const share = await api.shareDeck(deck.thread_id);
      setShareInfo(share);
      setShowShare(true);
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(share.share_url).catch(() => undefined);
      }
    } catch (e) {
      setErr(String(e));
    }
  }

  async function onForkSharedDeck(shareId: string) {
    setForkingShare(true);
    setErr(null);
    try {
      const result = await api.forkSharedDeck(shareId);
      localStorage.setItem("osz.thread_id", result.state.thread_id);
      rememberDeck(result.state);
      setDeck(result.state);
      setSharedDeck(null);
      window.history.replaceState({}, "", window.location.pathname);
      void refreshDeckList();
    } catch (e) {
      setErr(String(e));
    } finally {
      setForkingShare(false);
    }
  }

  async function onDeleteDeck(id: string, name?: string | null) {
    if (busy || deletingDeckId) return;
    const label = (name || id).trim();
    const confirmed = window.confirm(
      `Delete "${label}"? This permanently removes its checkpoints, generated files, and playground lanes.`,
    );
    if (!confirmed) return;

    setDeletingDeckId(id);
    setErr(null);
    try {
      const result = await api.deleteDeck(id);
      const deletedIds = result.deleted_thread_ids.length ? result.deleted_thread_ids : [id];
      const deleted = new Set(deletedIds);
      const activeId = localStorage.getItem("osz.thread_id");
      if (activeId && deleted.has(activeId)) {
        localStorage.removeItem("osz.thread_id");
      }
      forgetDecks(deletedIds);
      if (deck && deleted.has(deck.thread_id)) {
        abortRef.current?.abort();
        setDeck(null);
        setBuffersByTag({});
        setActiveNode(null);
        setActiveSlide(null);
        setActiveModel(null);
        setElapsedByTag({});
        tagStartRef.current = {};
        setShowExport(false);
        setShowHistory(false);
        setShowMasterpieces(false);
      }
      await refreshDeckList();
    } catch (e) {
      setErr(String(e));
    } finally {
      setDeletingDeckId(null);
    }
  }

  async function runExport(
    label: string,
    fn: (d: DeckState) => Promise<void>,
  ) {
    if (!deck) return;
    setShowExport(false);
    setExporting(label);
    try {
      await fn(deck);
    } catch (e) {
      setErr(`Export failed: ${String(e)}`);
    } finally {
      setExporting(null);
    }
  }

  const visibleSessionDecks = useMemo(
    () => sessionDeckList(deckHistory, deckList),
    [deckHistory, deckList],
  );

  // --- Render ---

  if (sharedDeck) {
    return (
      <SharedDeckView
        shared={sharedDeck}
        err={err}
        forking={forkingShare}
        onFork={() => void onForkSharedDeck(sharedDeck.share_id)}
        onNewDeck={onNewDeck}
      />
    );
  }

  if (!deck) {
    return (
      <CreateForm
        onSubmit={onCreate}
        busy={busy}
        err={err}
        catalog={catalog}
        runtimeConfig={runtimeConfig}
        runtimeModelOptions={runtimeModelOptions}
        onRuntimeConfigChange={updateRuntimeConfig}
        recentDecks={visibleSessionDecks}
        ownedDecks={deckList ?? []}
        deletingDeckId={deletingDeckId}
        onLoadDeck={(id) => void refresh(id).catch((e) => setErr(String(e)))}
        onDeleteDeck={(id, name) => void onDeleteDeck(id, name)}
      />
    );
  }

  const stage = (deck.values?.current_stage as string) ?? "";
  const hasInterrupt = (deck.interrupts?.length ?? 0) > 0;
  const interruptPayload = firstInterruptPayload(deck);
  const interruptGate = interruptPayload?.gate as string | undefined;
  const isAdvancedChatGate = interruptGate === "advanced_chat";
  const hasPendingTasks = (deck.next?.length ?? 0) > 0;
  const materialWarnings = Array.isArray(deck.values?.materials)
    ? (deck.values.materials as Material[]).filter((material) => Boolean(material.note))
    : [];
  const slides = (deck.values?.html_slides as Record<number, string>) ?? {};
  const renderedSlideIdx = Object.keys(slides)
    .map((k) => Number(k))
    .filter((k) => Number.isInteger(k))
    .sort((a, b) => a - b);
  const briefSlides = Array.isArray(deck.values?.brief?.slides)
    ? (deck.values.brief.slides as Array<{ slide_idx: number }>)
    : [];
  const expectedSlideIdx = briefSlides.length
    ? briefSlides
        .map((slide) => Number(slide.slide_idx))
        .filter((idx) => Number.isInteger(idx))
        .sort((a, b) => a - b)
    : renderedSlideIdx;
  const hasSlides = expectedSlideIdx.length > 0;
  const renderedCount = renderedSlideIdx.length;
  const expectedCount = expectedSlideIdx.length;
  const outlineMd = deck.values?.outline_md as string | undefined;
  const briefMd = deck.values?.consolidated_brief_md as string | undefined;

  const bufferTags = Object.keys(buffersByTag);
  const advancedChatOnlyLive =
    isAdvancedChatGate &&
    ((busy && bufferTags.length === 0 && activeNode == null) ||
      activeNode === "advanced_chat" ||
      (bufferTags.length > 0 && bufferTags.every((tag) => tag === "advanced_chat")));
  const showLive = (busy || bufferTags.length > 0) && !advancedChatOnlyLive;
  const readyReviewEnabled = stage === "ready" && !hasInterrupt;
  const reviewStepIndex = REVIEW_STAGES.findIndex((step) => step.id === selectedReviewStage);
  const imagePlan = deck.values?.image_insertion_plan as ImageInsertionPlan | undefined;
  const imageAssets = (deck.values?.image_assets as ImageAsset[] | undefined) ?? [];
  const imageInsertionStatus = deck.values?.image_insertion_status as string | undefined;
  // Backend resets image_insertion_plan to {} after a regenerate-from-stage
  // rewind, and {} is truthy — so test for actual content, not just presence.
  const hasImagePlan = Boolean(imagePlan && Object.keys(imagePlan).length > 0);
  const showImageInsertion =
    readyReviewEnabled &&
    hasSlides &&
    (imageInsertionStatus !== "unavailable" || hasImagePlan || imageAssets.length > 0);

  return (
    <div style={{ padding: 16, maxWidth: 1520, margin: "0 auto" }}>
      <header className="osz-app-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <strong
            style={{
              fontFamily: "'Playfair Display', Georgia, serif",
              fontSize: 20,
              fontWeight: 900,
              letterSpacing: -0.3,
            }}
          >
            Open Slides Zero
          </strong>
          <span style={{ color: "#948e83" }}>·</span>
          <span
            style={{
              fontSize: 14,
              color: "#f5f3ee",
              maxWidth: 300,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {(deck.values?.deck_name as string) || deck.thread_id}
          </span>
          <span style={{ color: "#948e83" }}>·</span>
          <code style={{ color: "#cfc8b9", fontSize: 12, fontFamily: "ui-monospace, 'SF Mono', monospace" }}>
            {deck.thread_id}
          </code>
          <span style={{ color: "#948e83" }}>·</span>
          <span style={{ fontSize: 13, color: "#cfc8b9" }}>
            stage: <code style={{ fontFamily: "ui-monospace, 'SF Mono', monospace" }}>{stage}</code>
          </span>
          {stage === "html" && expectedCount > 0 && renderedCount < expectedCount && (
            <span style={{ fontSize: 13, color: "#cfc8b9" }}>
              rendered {renderedCount}/{expectedCount}
            </span>
          )}
          {busy && activeModel && (
            <span style={{ fontSize: 12, color: "#cfc8b9" }}>
              model: <code style={{ fontFamily: "ui-monospace, 'SF Mono', monospace" }}>{activeModel}</code>
            </span>
          )}
          {busy && (
            <span className="osz-status osz-status-busy" style={{ marginLeft: 4 }}>
              ● streaming
            </span>
          )}
        </div>
	        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", position: "relative" }}>
	          <button
	            className="osz-header-btn"
	            onClick={() => {
	              setShowMasterpieces(false);
	              setShowExport(false);
	              setShowHistory(false);
	              setShowOwnedHistory(false);
	              setShowShare(false);
	              setShowConfig((s) => !s);
	            }}
	          >
            Config
          </button>
          <button
            className="osz-header-btn"
	            onClick={() => {
	              setShowExport(false);
	              setShowHistory(false);
	              setShowOwnedHistory(false);
	              setShowShare(false);
	              setShowConfig(false);
	              setShowMasterpieces((s) => !s);
	            }}
          >
            Masterpieces
          </button>
          <button
            className="osz-header-btn"
	            onClick={() => {
	              setShowMasterpieces(false);
	              setShowExport(false);
	              setShowConfig(false);
	              setShowOwnedHistory(false);
	              setShowShare(false);
	              setShowHistory((s) => {
	                const next = !s;
	                if (next) void refreshDeckList();
	                return next;
	              });
	            }}
	          >
	            Session decks
	          </button>
	          <button
	            className="osz-header-btn"
	            onClick={() => {
	              setShowMasterpieces(false);
	              setShowExport(false);
	              setShowConfig(false);
	              setShowHistory(false);
	              setShowShare(false);
	              setShowOwnedHistory((s) => {
	                const next = !s;
	                if (next) void refreshDeckList();
	                return next;
	              });
	            }}
	          >
	            My history
	          </button>
	          <div style={{ position: "relative" }}>
            <button
              className="osz-header-btn"
              disabled={!hasExportableSlides(deck) || exporting !== null}
              onClick={() => {
	                setShowMasterpieces(false);
	                setShowHistory(false);
	                setShowOwnedHistory(false);
	                setShowShare(false);
	                setShowConfig(false);
	                setShowExport((s) => !s);
	              }}
            >
              {exporting ? `Exporting ${exporting}…` : "Export"}
            </button>
            {showExport && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  right: 0,
	                minWidth: 320,
	                maxHeight: "min(680px, calc(100vh - 96px))",
	                overflowY: "auto",
                  background: "#f5f3ee",
                  border: "1.5px solid #0a0a0a",
                  borderRadius: 0,
                  boxShadow: "none",
                  zIndex: 100,
                  padding: "4px 0",
                }}
              >
                {[
                  { key: "html", label: "Current HTML (single file)", fn: exportHtmlSingle },
                  { key: "zip", label: "Current HTML (zip of slides)", fn: exportHtmlZip },
                  { key: "pngs", label: "Current PNGs (zip of slides)", fn: exportPngZip },
                  { key: "pptx", label: "Current PPTX (editable)", fn: exportPptx },
                  ...(hasOriginalSlides(deck)
                    ? [
                        {
                          key: "html-original",
                          label: "Original HTML before images",
                          fn: (d: DeckState) => exportHtmlSingle(deckWithBaseSlides(d)),
                        },
                        {
                          key: "pngs-original",
                          label: "Original PNGs before images",
                          fn: (d: DeckState) => exportPngZip(d, { useBaseSlides: true }),
                        },
                        {
                          key: "pptx-original",
                          label: "Original PPTX before images",
                          fn: (d: DeckState) => exportPptx(deckWithBaseSlides(d)),
                        },
                      ]
                    : []),
                ].map((opt) => (
                  <button
                    key={opt.key}
                    onClick={() => void runExport(opt.key, opt.fn)}
                    style={{
                      display: "block",
                      width: "100%",
                      textAlign: "left",
                      padding: "6px 12px",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      fontSize: 13,
                      fontFamily: "inherit",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#e8e3d8")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
	          <button
	            className="osz-header-btn"
	            disabled={busy || Boolean(deletingDeckId)}
	            onClick={() =>
              void onDeleteDeck(
                deck.thread_id,
                (deck.values?.deck_name as string | undefined) || deck.thread_id,
              )
            }
          >
	            {deletingDeckId === deck.thread_id ? "Deleting…" : "Delete deck"}
	          </button>
	          <button
	            className="osz-header-btn"
	            disabled={busy || Boolean(deletingDeckId)}
	            onClick={() => {
	              setShowMasterpieces(false);
	              setShowExport(false);
	              setShowHistory(false);
	              setShowOwnedHistory(false);
	              setShowConfig(false);
	              if (shareInfo?.thread_id === deck.thread_id) {
	                setShowShare((s) => !s);
	              } else {
	                void onShareDeck();
	              }
	            }}
	          >
	            Share deck
	          </button>
	          <button className="osz-header-btn" onClick={onNewDeck}>New deck</button>
	          {showShare && shareInfo && (
	            <div
	              style={{
	                position: "absolute",
	                top: "calc(100% + 4px)",
	                right: 0,
	                minWidth: 360,
	                background: "#f5f3ee",
	                border: "1.5px solid #0a0a0a",
	                borderRadius: 0,
	                boxShadow: "none",
	                zIndex: 100,
	                padding: 12,
	              }}
	            >
	              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1.4, textTransform: "uppercase", color: "#0a0a0a" }}>
	                Share link
	              </div>
	              <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.4, marginTop: 6 }}>
	                Viewers can open this read-only deck and fork it into their own history. ZenMux keys are not included in the shared deck.
	              </div>
	              <input
	                readOnly
	                value={shareInfo.share_url}
	                onFocus={(e) => e.currentTarget.select()}
	                style={{
	                  width: "100%",
	                  boxSizing: "border-box",
	                  marginTop: 10,
	                  padding: "8px 10px",
	                  border: "1px solid #0a0a0a",
	                  background: "#e8e3d8",
	                  color: "#1c1c1e",
	                  fontFamily: "ui-monospace, 'SF Mono', monospace",
	                  fontSize: 12,
	                }}
	              />
	              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
	                <button
	                  type="button"
	                  onClick={() => {
	                    if (navigator.clipboard) {
	                      void navigator.clipboard.writeText(shareInfo.share_url);
	                    }
	                  }}
	                  className="osz-button"
	                >
	                  Copy
	                </button>
	              </div>
	            </div>
	          )}
	          {showHistory && (
	            <div
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                right: 0,
                minWidth: 200,
                background: "#f5f3ee",
                border: "1.5px solid #0a0a0a",
                borderRadius: 0,
                boxShadow: "none",
	                zIndex: 100,
	                padding: "8px 0",
	              }}
	            >
	              <div style={{ padding: "4px 12px 8px", borderBottom: "1px solid #0a0a0a" }}>
	                <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1.4, textTransform: "uppercase", color: "#0a0a0a" }}>
	                  Current session
	                </div>
	                <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.4, marginTop: 4 }}>
	                  Clears when this tab session ends. Reloading this tab keeps it.
	                </div>
	              </div>
	              {(() => {
	                if (visibleSessionDecks.length === 0) {
	                  return (
                    <div style={{ padding: "8px 12px", color: "#948e83", fontSize: 13 }}>
                      No history yet
                    </div>
                  );
                }
                return visibleSessionDecks.map((historyDeck) => (
                  <div
                    key={historyDeck.thread_id}
                    style={{
                      display: "flex",
                      alignItems: "stretch",
                      width: "100%",
                      borderBottom: "1px solid #0a0a0a",
                    }}
                  >
                    <button
                      disabled={Boolean(deletingDeckId)}
                      onClick={() => {
                        setShowHistory(false);
                        void refresh(historyDeck.thread_id).catch((e) => setErr(String(e)));
                      }}
                      style={{
                        flex: "1 1 auto",
                        minWidth: 0,
                        textAlign: "left",
                        padding: "6px 12px",
                        background: "none",
                        border: "none",
                        cursor: deletingDeckId ? "default" : "pointer",
                        fontSize: 13,
                        fontFamily: "inherit",
                      }}
                      onMouseEnter={(e) => {
                        if (!deletingDeckId) e.currentTarget.style.background = "#e8e3d8";
                      }}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }}>
                        {historyDeck.deck_name || historyDeck.thread_id}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <code style={{ fontSize: 11, color: "#948e83" }}>{historyDeck.thread_id}</code>
                        {historyDeck.stage && (
                          <span style={{ fontSize: 10, color: "#5c5852", background: "transparent", padding: "1px 4px" }}>
                            {historyDeck.stage}
                          </span>
                        )}
                      </div>
                    </button>
                    <button
                      disabled={busy || Boolean(deletingDeckId)}
                      onClick={() => void onDeleteDeck(historyDeck.thread_id, historyDeck.deck_name)}
                      style={{
                        flex: "0 0 auto",
                        border: "none",
                        borderLeft: "1px solid #0a0a0a",
                        background: "transparent",
                        color: "#8b1a1a",
                        cursor: busy || deletingDeckId ? "default" : "pointer",
                        fontSize: 12,
                        fontFamily: "inherit",
                        padding: "0 10px",
                      }}
                    >
                      {deletingDeckId === historyDeck.thread_id ? "Deleting…" : "Delete"}
                    </button>
                  </div>
	                ));
	              })()}
	            </div>
	          )}
	          {showOwnedHistory && (
	            <div
	              style={{
	                position: "absolute",
	                top: "calc(100% + 4px)",
	                right: 0,
	                minWidth: 360,
	                maxWidth: 520,
	                maxHeight: "min(680px, calc(100vh - 96px))",
	                overflowY: "auto",
	                background: "#f5f3ee",
	                border: "1.5px solid #0a0a0a",
	                borderRadius: 0,
	                boxShadow: "none",
	                zIndex: 100,
	                padding: "8px 0",
	              }}
	            >
	              <div style={{ padding: "4px 12px 8px", borderBottom: "1px solid #0a0a0a" }}>
	                <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 1.4, textTransform: "uppercase", color: "#0a0a0a" }}>
	                  My deck history
	                </div>
	                <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.4, marginTop: 4 }}>
	                  Available on this browser while site cookies remain. Clearing site data removes access.
	                </div>
	              </div>
	              {deckList == null ? (
	                <div style={{ padding: "8px 12px", color: "#948e83", fontSize: 13 }}>
	                  Loading history...
	                </div>
	              ) : deckList.length > 0 ? (
	                deckList.map((historyDeck) => (
	                  <div
	                    key={historyDeck.thread_id}
	                    style={{
	                      display: "flex",
	                      alignItems: "stretch",
	                      width: "100%",
	                      borderBottom: "1px solid #0a0a0a",
	                    }}
	                  >
	                    <button
	                      disabled={Boolean(deletingDeckId)}
	                      onClick={() => {
	                        setShowOwnedHistory(false);
	                        void refresh(historyDeck.thread_id).catch((e) => setErr(String(e)));
	                      }}
	                      style={{
	                        flex: "1 1 auto",
	                        minWidth: 0,
	                        textAlign: "left",
	                        padding: "8px 12px",
	                        background: "none",
	                        border: "none",
	                        cursor: deletingDeckId ? "default" : "pointer",
	                        fontSize: 13,
	                        fontFamily: "inherit",
	                      }}
	                      onMouseEnter={(e) => {
	                        if (!deletingDeckId) e.currentTarget.style.background = "#e8e3d8";
	                      }}
	                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
	                    >
	                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 300 }}>
	                        {historyDeck.deck_name || historyDeck.thread_id}
	                      </div>
	                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
	                        <code style={{ fontSize: 11, color: "#948e83" }}>{historyDeck.thread_id}</code>
	                        {historyDeck.stage && (
	                          <span style={{ fontSize: 10, color: "#5c5852", background: "transparent", padding: "1px 4px" }}>
	                            {historyDeck.stage}
	                          </span>
	                        )}
	                      </div>
	                    </button>
	                    <button
	                      disabled={busy || Boolean(deletingDeckId)}
	                      onClick={() => void onDeleteDeck(historyDeck.thread_id, historyDeck.deck_name)}
	                      style={{
	                        flex: "0 0 auto",
	                        border: "none",
	                        borderLeft: "1px solid #0a0a0a",
	                        background: "transparent",
	                        color: "#8b1a1a",
	                        cursor: busy || deletingDeckId ? "default" : "pointer",
	                        fontSize: 12,
	                        fontFamily: "inherit",
	                        padding: "0 10px",
	                      }}
	                    >
	                      {deletingDeckId === historyDeck.thread_id ? "Deleting…" : "Delete"}
	                    </button>
	                  </div>
	                ))
	              ) : (
	                <div style={{ padding: "8px 12px", color: "#948e83", fontSize: 13 }}>
	                  No saved deck history for this browser.
	                </div>
	              )}
	            </div>
	          )}
	        </div>
      </header>

      {err && (
        <div style={{ color: "#8b1a1a", padding: 8, border: "1.5px solid #8b1a1a", borderRadius: 0, marginBottom: 8, background: "#f5f3ee" }}>
          {err}
        </div>
      )}
      {showConfig && (
        <RuntimeConfigPanel
          config={runtimeConfig}
          modelOptions={runtimeModelOptions}
          onChange={updateRuntimeConfig}
          busy={busy}
        />
      )}
      {materialWarnings.length > 0 && (
        <div
          style={{
            color: "#8a5a14",
            background: "#f5f3ee",
            border: "1px solid #8a5a14",
            borderRadius: 0,
            padding: 10,
            marginBottom: 12,
          }}
        >
          <strong style={{ display: "block", marginBottom: 6 }}>Material warnings</strong>
          {materialWarnings.map((material, idx) => (
            <div key={`${material.uri}-${idx}`} style={{ fontSize: 13, lineHeight: 1.45 }}>
              <span style={{ fontWeight: 600 }}>{materialLabel(material)}:</span> {material.note}
            </div>
          ))}
        </div>
      )}

      <div
        className="osz-main-grid"
        style={{
          display: "grid",
          gridTemplateColumns: showLive ? "minmax(0, 1fr) 380px" : "minmax(0, 1fr)",
          gap: 16,
          alignItems: "start",
        }}
      >
        <main style={{ minWidth: 0 }}>
          {showMasterpieces ? (
            <MasterpieceManager
              deck={deck}
              onClose={() => setShowMasterpieces(false)}
            />
          ) : (
            <>
          {readyReviewEnabled && (
            <section
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                padding: 12,
                marginBottom: 12,
                border: "1.5px solid #0a0a0a",
                borderRadius: 0,
                background: "#f5f3ee",
              }}
            >
              <button
                className="osz-button"
                disabled={reviewStepIndex <= 0}
                onClick={() => setSelectedReviewStage(REVIEW_STAGES[reviewStepIndex - 1].id)}
              >
                Previous step
              </button>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
                {REVIEW_STAGES.map((step, idx) => (
                  <button
                    key={step.id}
                    onClick={() => setSelectedReviewStage(step.id)}
                    style={{
                      padding: "5px 12px",
                      border: "1px solid #0a0a0a",
                      borderRadius: 0,
                      background: step.id === selectedReviewStage ? "#0a0a0a" : "#f5f3ee",
                      color: step.id === selectedReviewStage ? "#f5f3ee" : "#5c5852",
                      fontSize: 11,
                      fontWeight: 700,
                      letterSpacing: 1.4,
                      textTransform: "uppercase",
                      cursor: "pointer",
                    }}
                  >
                    {idx + 1}. {step.label}
                  </button>
                ))}
              </div>
              <button
                className="osz-button"
                disabled={reviewStepIndex >= REVIEW_STAGES.length - 1}
                onClick={() => setSelectedReviewStage(REVIEW_STAGES[reviewStepIndex + 1].id)}
              >
                Next step
              </button>
            </section>
          )}

          {hasInterrupt && isAdvancedChatGate ? (
            <AdvancedChatPanel
              deck={deck}
              busy={busy}
              streamingText={buffersByTag.advanced_chat ?? ""}
              onSend={onAdvancedChat}
              onContinue={onAdvancedChatContinue}
            />
          ) : hasInterrupt && (
            <HitlReviewPanel deck={deck} catalog={catalog} onResume={onResume} />
          )}

          {readyReviewEnabled && selectedReviewStage === "structure" && (
            <StructureStage
              catalog={catalog}
              scenarioId={deck.values?.scenario_id as string | undefined}
              structureId={deck.values?.structure_id as string | undefined}
              candidates={deck.values?.structure_candidates as string[] | undefined}
              title="① Review structure choice"
              submitLabel="Create fork from structure"
              onSubmit={async (payload) => onForkFromReview({ review_stage: "structure", ...payload })}
            />
          )}

          {readyReviewEnabled && selectedReviewStage === "style" && (
            <StyleStage
              title="② Review visual style"
              submitLabel="Create fork from style"
              visualStyleMd={deck.values?.visual_style_md as string | undefined}
              visualStyle={deck.values?.visual_style as Record<string, any> | undefined}
              onSubmit={async ({ feedback }) =>
                onForkFromReview({ review_stage: "style", feedback })
              }
            />
          )}

          {readyReviewEnabled && selectedReviewStage === "layout" && (
            <LayoutStage
              catalog={catalog}
              layouts={deck.values?.layouts as Array<Record<string, any>> | undefined}
              selectedVisualStylePresetId={deck.values?.visual_style_preset_id as string | null | undefined}
              title="③ Review layouts"
              submitLabel="Create fork from layout"
              submitDisabledWhenUnchanged
              onSubmit={async ({ overrides, visual_style_preset_id }) =>
                onForkFromReview({ review_stage: "layout", overrides, visual_style_preset_id })
              }
            />
          )}

          {readyReviewEnabled && selectedReviewStage === "brief" && (
            <BriefReview briefMd={briefMd} />
          )}

          {(!readyReviewEnabled || selectedReviewStage === "ready") && (
            stage === "playground" && !hasInterrupt ? (
              <PlaygroundPanel deck={deck} catalog={catalog} />
            ) : (
              <>
              {hasPendingTasks && !hasInterrupt && !busy && (
                <div
                  style={{
                    padding: 12,
                    marginBottom: 12,
                    border: "1px solid #8a5a14",
                    borderRadius: 0,
                    background: "#f5f3ee",
                    color: "#8a5a14",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                  }}
                >
                  <span style={{ fontSize: 13, color: "#8a5a14" }}>
                    {renderedCount < expectedCount
                      ? `Rendering paused: ${renderedCount}/${expectedCount} slides complete.`
                      : "Generation is paused — resume to continue."}
                  </span>
                  <button
                    style={{
                      padding: "4px 12px",
                      fontSize: 13,
                      background: "#8a5a14",
                      color: "#f5f3ee",
                      border: "1.5px solid #8a5a14",
                      borderRadius: 0,
                      cursor: "pointer",
                      fontWeight: 700,
                      letterSpacing: 1.4,
                      textTransform: "uppercase",
                    }}
                    onClick={() => onResume({})}
                  >
                    Resume generation
                  </button>
                </div>
              )}

              {!hasInterrupt && !hasSlides && outlineMd && (
                <section
                  style={{ padding: 12, border: "1.5px solid #0a0a0a", borderRadius: 0 }}
                >
                  <Markdown>{outlineMd}</Markdown>
                </section>
              )}

              {!hasInterrupt && !hasSlides && briefMd && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer" }}>Consolidated brief</summary>
                  <section
                    style={{
                      marginTop: 8,
                      padding: 12,
                      border: "1.5px solid #0a0a0a",
                      borderRadius: 0,
                    }}
                  >
                    <Markdown>{briefMd}</Markdown>
                  </section>
                </details>
              )}

              {showImageInsertion && (
                <ImageInsertionPanel
                  deck={deck}
                  onDeck={setDeck}
                  onError={setErr}
                />
              )}

              {hasSlides && (
                <DeckCanvas
                  slides={slides}
                  slideOrder={expectedSlideIdx}
                  currentSlide={currentSlide}
                  onSelectSlide={setCurrentSlide}
                  aspectRatio={(deck.values?.aspect_ratio as any) ?? "16:9"}
                  width={CANVAS_W}
                >
                  {readyReviewEnabled && !busy && (
                    <CommentLayer width={CANVAS_W} height={CANVAS_H} onSubmit={onComment} />
                  )}
                </DeckCanvas>
              )}
              </>
            )
          )}
            </>
          )}
        </main>

        {showLive && (
          <aside style={{ position: "sticky", top: 16, alignSelf: "start" }}>
            <LiveStream
              buffersByTag={buffersByTag}
              activeNode={activeNode}
              activeSlide={activeSlide}
              elapsedByTag={elapsedByTag}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

function cleanAssetName(value: string | null | undefined): string {
  if (!value) return "";
  const decoded = decodeURIComponent(value.split(/[?#]/)[0].split("/").pop() ?? value);
  const stem = decoded.replace(/\.(jpe?g|png|webp|gif)$/i, "");
  if (/^(original|proxyImageThumbnailLarge|image|photo|[0-9a-f-]{16,})$/i.test(stem)) {
    return "";
  }
  return stem
    .replace(/^ph[_-]/i, "")
    .replace(/^photo[_-]/i, "")
    .replace(/[_-]gbif\d+$/i, "")
    .replace(/[_-]+/g, " ")
    .trim();
}

function firstMatch(pattern: RegExp, value: string): string {
  return value.match(pattern)?.[1] ?? "";
}

function assetLabel(asset: ImageAsset): string {
  if (asset.source === "generated") {
    const text = (asset.prompt || asset.summary || "AI image").trim();
    return `Generated · ${text.slice(0, 90)}`;
  }

  const haystack = `${asset.summary ?? ""} ${asset.uri ?? ""} ${asset.name ?? ""}`;
  const gbif = firstMatch(/gbif\.org\/occurrence\/(\d+)/i, haystack) || firstMatch(/\bgbif[_-]?(\d{6,})\b/i, haystack);
  const inat = firstMatch(/inaturalist[^/]*\/photos\/(\d+)/i, haystack);
  const commons = firstMatch(/(?:Special:Redirect\/file\/|File:)([^?#\s]+)/i, haystack);
  const readableName = cleanAssetName(asset.name) || cleanAssetName(asset.uri);
  const refName = cleanAssetName(firstMatch(/Referenced in [^:]+:\s*(\S+)/i, asset.summary ?? ""));

  const parts: string[] = [];
  if (gbif) parts.push(`GBIF ${gbif}`);
  if (inat) parts.push(`iNaturalist photo ${inat}`);
  if (!gbif && commons) parts.push(`Wikimedia · ${cleanAssetName(commons) || decodeURIComponent(commons)}`);
  const descriptive = refName || readableName;
  if (descriptive && !parts.some((part) => part.toLowerCase().includes(descriptive.toLowerCase()))) {
    parts.push(descriptive);
  }
  return parts.join(" · ") || asset.uri || asset.asset_id;
}

function assetThumbnailSrc(deck: DeckState, asset: ImageAsset): string {
  if (asset.uri.startsWith("http") || asset.uri.startsWith("data:image/")) return asset.uri;
  return api.imageAssetContentUrl(deck.thread_id, asset.asset_id);
}

function defaultImagePrompt(slot: { slide_idx: number; hint: string }): string {
  return (
    `Create a polished presentation image for slide ${slot.slide_idx + 1}: ${slot.hint}. ` +
    "Use a clean editorial composition, no visible text, no watermarks, " +
    "and enough negative space to sit inside a slide image slot."
  );
}

function ImageInsertionPanel({
  deck,
  onDeck,
  onError,
}: {
  deck: DeckState;
  onDeck: (deck: DeckState) => void;
  onError: (message: string | null) => void;
}) {
  const plan = deck.values?.image_insertion_plan as ImageInsertionPlan | undefined;
  const [panelBusy, setPanelBusy] = useState<string | null>(null);
  const [generatingBySlot, setGeneratingBySlot] = useState<Record<string, string>>({});
  const [selectedBySlot, setSelectedBySlot] = useState<Record<string, string>>({});
  const [promptsBySlot, setPromptsBySlot] = useState<Record<string, string>>({});
  const [openPickerSlot, setOpenPickerSlot] = useState<string | null>(null);

  useEffect(() => {
    // After a regenerate-from-stage rewind, image_insertion_plan is cleared
    // to {} server-side (truthy), so guard on real content, not presence.
    if (!plan || Object.keys(plan).length === 0) return;
    const mappings =
      (plan.applied_mappings?.length ? plan.applied_mappings : plan.mappings) ?? [];
    setSelectedBySlot(
      Object.fromEntries(mappings.map((mapping) => [mapping.slot_id, mapping.asset_id])),
    );
    setPromptsBySlot((prev) => ({
      ...Object.fromEntries(
        (plan.unmatched_slots ?? []).map((slot) => [slot.slot_id, slot.prompt]),
      ),
      ...prev,
    }));
  }, [plan?.status, plan?.slots?.length, plan?.assets?.length]);

  const assets = plan?.assets ?? ((deck.values?.image_assets as ImageAsset[] | undefined) ?? []);
  const slots = plan?.slots ?? [];
  const promptBySlot = Object.fromEntries(
    (plan?.unmatched_slots ?? []).map((slot) => [slot.slot_id, slot.prompt]),
  );
  const promptForSlot = (slot: { slot_id: string; slide_idx: number; hint: string }) =>
    promptsBySlot[slot.slot_id] ?? promptBySlot[slot.slot_id] ?? defaultImagePrompt(slot);
  const noUserImageSlots = slots.filter((slot) => !selectedBySlot[slot.slot_id]);
  const generationActive = Object.keys(generatingBySlot).length > 0;

  async function refreshPlan() {
    setPanelBusy("Planning");
    onError(null);
    try {
      const result = await api.planImages(deck.thread_id);
      onDeck(result.state);
    } catch (e) {
      onError(String(e));
    } finally {
      setPanelBusy(null);
    }
  }

  async function applyMappings() {
    const mappings: ImageMapping[] = Object.entries(selectedBySlot)
      .filter(([, assetId]) => Boolean(assetId))
      .map(([slot_id, asset_id]) => ({ slot_id, asset_id }));
    setPanelBusy("Applying");
    onError(null);
    try {
      const result = await api.applyImages(deck.thread_id, mappings);
      onDeck(result.state);
    } catch (e) {
      onError(String(e));
    } finally {
      setPanelBusy(null);
    }
  }

  async function generateForSlot(slot: { slot_id: string; slide_idx: number; hint: string }) {
    const prompt = promptForSlot(slot).trim();
    if (!prompt) return;
    setGeneratingBySlot((prev) => ({ ...prev, [slot.slot_id]: "Generating image..." }));
    onError(null);
    try {
      const result = await api.generateImage(deck.thread_id, slot.slide_idx, slot.slot_id, prompt);
      onDeck(result.state);
    } catch (e) {
      onError(String(e));
    } finally {
      setGeneratingBySlot((prev) => {
        const next = { ...prev };
        delete next[slot.slot_id];
        return next;
      });
    }
  }

  async function generateAllNoUserImages() {
    const items = noUserImageSlots
      .map((slot) => ({
        slide_idx: slot.slide_idx,
        slot_id: slot.slot_id,
        prompt: promptForSlot(slot).trim(),
      }))
      .filter((item) => item.prompt);
    if (items.length === 0) return;
    setGeneratingBySlot(
      Object.fromEntries(items.map((item) => [item.slot_id, "Generating in batch..."])),
    );
    onError(null);
    try {
      const result = await api.generateImages(deck.thread_id, items);
      onDeck(result.state);
      if (result.errors.length > 0) {
        onError(`${result.errors.length} image generation request(s) failed.`);
      }
    } catch (e) {
      onError(String(e));
    } finally {
      setGeneratingBySlot({});
    }
  }

  return (
    <section
      style={{
        padding: 12,
        marginBottom: 12,
        border: "1.5px solid #0a0a0a",
        borderRadius: 0,
        background: "#f5f3ee",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <h3 style={{ margin: "0 0 4px", fontSize: 16 }}>Insert images</h3>
          <div style={{ fontSize: 13, color: "#5c5852" }}>
            Keep exporting the placeholder deck, or review matches and apply real images.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" disabled={Boolean(panelBusy) || generationActive} onClick={refreshPlan}>
            {plan ? "Refresh matches" : "Review matches"}
          </button>
          {plan && (
            <button type="button" disabled={Boolean(panelBusy) || generationActive} onClick={applyMappings}>
              Apply selected images
            </button>
          )}
          {plan && noUserImageSlots.length > 0 && (
            <button type="button" disabled={Boolean(panelBusy) || generationActive} onClick={generateAllNoUserImages}>
              Generate all no-user images
            </button>
          )}
        </div>
      </div>

      {panelBusy && <div style={{ marginTop: 8, fontSize: 12, color: "#5c5852" }}>{panelBusy}...</div>}

      {plan && (
        <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
          {slots.map((slot) => {
            const selectedAsset = assets.find((asset) => asset.asset_id === selectedBySlot[slot.slot_id]);
            const prompt = promptForSlot(slot);
            const generatingHint = generatingBySlot[slot.slot_id];
            return (
              <div
                key={slot.slot_id}
                className="osz-image-row"
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(160px, 1fr) minmax(220px, 1.2fr)",
                  gap: 10,
                  padding: 10,
                  border: "1.5px solid #0a0a0a",
                  borderRadius: 0,
                  background: "#f5f3ee",
                }}
              >
                <div>
                  <div style={{ fontSize: 12, color: "#5c5852" }}>Slide {slot.slide_idx + 1}</div>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{slot.hint}</div>
                </div>
                <div style={{ display: "grid", gap: 8, position: "relative" }}>
                  <button
                    type="button"
                    onClick={() => setOpenPickerSlot((open) => (open === slot.slot_id ? null : slot.slot_id))}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                      width: "100%",
                      padding: "7px 9px",
                      border: "1.5px solid #0a0a0a",
                      borderRadius: 0,
                      background: "#f5f3ee",
                      fontFamily: "inherit",
                      fontSize: 13,
                      cursor: "pointer",
                      textAlign: "left",
                    }}
                  >
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {selectedAsset ? assetLabel(selectedAsset) : "No user image"}
                    </span>
                    <span aria-hidden="true" style={{ color: "#5c5852" }}>▾</span>
                  </button>
                  {openPickerSlot === slot.slot_id && (
                    <div
                      style={{
                        position: "absolute",
                        top: 40,
                        right: 0,
                        left: 0,
                        zIndex: 20,
                        maxHeight: 390,
                        overflowY: "auto",
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                        gap: 8,
                        padding: 10,
                        border: "1.5px solid #0a0a0a",
                        borderRadius: 0,
                        background: "#f5f3ee",
                        boxShadow: "none",
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedBySlot((prev) => ({ ...prev, [slot.slot_id]: "" }));
                          setOpenPickerSlot(null);
                        }}
                        style={{
                          gridColumn: "1 / -1",
                          padding: 8,
                          border: selectedAsset ? "1.5px solid #0a0a0a" : "2px solid #0a0a0a",
                          borderRadius: 0,
                          background: selectedAsset ? "#f5f3ee" : "#e8e3d8",
                          textAlign: "left",
                          fontFamily: "inherit",
                          cursor: "pointer",
                        }}
                      >
                        No user image
                      </button>
                      {assets.map((asset) => {
                        const selected = selectedAsset?.asset_id === asset.asset_id;
                        return (
                          <button
                            key={asset.asset_id}
                            type="button"
                            onClick={() => {
                              setSelectedBySlot((prev) => ({
                                ...prev,
                                [slot.slot_id]: asset.asset_id,
                              }));
                              setOpenPickerSlot(null);
                            }}
                            style={{
                              display: "grid",
                              gridTemplateColumns: "70px minmax(0, 1fr)",
                              gap: 8,
                              alignItems: "center",
                              minHeight: 72,
                              padding: 7,
                              border: selected ? "2px solid #0a0a0a" : "1.5px solid #0a0a0a",
                              borderRadius: 0,
                              background: selected ? "#e8e3d8" : "#f5f3ee",
                              cursor: "pointer",
                              textAlign: "left",
                              fontFamily: "inherit",
                            }}
                          >
                            <img
                              src={assetThumbnailSrc(deck, asset)}
                              alt=""
                              loading="lazy"
                              style={{
                                width: 70,
                                height: 54,
                                objectFit: "cover",
                                borderRadius: 0,
                                border: "1px solid #0a0a0a",
                                background: "#e8e3d8",
                              }}
                            />
                            <span
                              style={{
                                minWidth: 0,
                                fontSize: 12,
                                lineHeight: 1.25,
                                color: "#1c1c1e",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {assetLabel(asset)}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                  {selectedAsset && (
                    <img
                      src={assetThumbnailSrc(deck, selectedAsset)}
                      alt=""
                      style={{
                        width: 96,
                        height: 54,
                        objectFit: "cover",
                        border: "1.5px solid #0a0a0a",
                        borderRadius: 0,
                      }}
                    />
                  )}
                  {!selectedAsset && (
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 8, alignItems: "start" }}>
                      <textarea
                        value={prompt}
                        onChange={(e) =>
                          setPromptsBySlot((prev) => ({
                            ...prev,
                            [slot.slot_id]: e.target.value,
                          }))
                        }
                        rows={3}
                        style={{ width: "100%", boxSizing: "border-box", fontFamily: "inherit", fontSize: 13 }}
                      />
                      <button
                        type="button"
                        disabled={Boolean(panelBusy) || Boolean(generatingHint)}
                        onClick={() => generateForSlot(slot)}
                      >
                        Generate
                      </button>
                      {generatingHint && (
                        <div
                          style={{
                            gridColumn: "1 / -1",
                            padding: "6px 8px",
                            border: "1px solid #0a0a0a",
                            borderRadius: 0,
                            background: "#e8e3d8",
                            color: "#1c1c1e",
                            fontSize: 12,
                          }}
                        >
                          {generatingHint}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// ---------------- Masterpieces ----------------

function MasterpieceManager({
  deck,
  onClose,
}: {
  deck: DeckState;
  onClose: () => void;
}) {
  const [items, setItems] = useState<Masterpiece[]>([]);
  const [laneCount, setLaneCount] = useState<number | null>(null);
  const [maxLanes, setMaxLanes] = useState(5);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const playgroundOpen = (deck.values?.current_stage as string | undefined) === "playground";

  const refresh = useCallback(async () => {
    const result = await api.listMasterpieces();
    setItems(result.masterpieces);
    if (playgroundOpen) {
      const lanes = await api.listPlaygroundLanes(deck.thread_id);
      setLaneCount(lanes.lanes.length);
      setMaxLanes(lanes.max_lanes);
    } else {
      setLaneCount(null);
    }
  }, [deck.thread_id, playgroundOpen]);

  useEffect(() => {
    void refresh().catch((e) => setErr(String(e)));
  }, [refresh]);

  async function deleteItem(item: Masterpiece) {
    setBusy(item.id);
    setErr(null);
    try {
      await api.deleteMasterpiece(item.id);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function useAsLane(item: Masterpiece) {
    if (!playgroundOpen || laneCount == null || laneCount >= maxLanes) return;
    setBusy(item.id);
    setErr(null);
    try {
      for await (const ev of streamSSE(
        api.createPlaygroundLaneStreamUrl(deck.thread_id),
        { creator_prompt: item.prompt },
      )) {
        if (ev.type === "error") throw new Error(ev.message);
      }
      await refresh();
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section style={{ border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee", padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <h3 style={{ margin: 0 }}>Masterpieces</h3>
          {playgroundOpen && laneCount != null && (
            <div style={{ color: "#5c5852", fontSize: 13, marginTop: 4 }}>
              Playground lanes: {laneCount}/{maxLanes}
            </div>
          )}
        </div>
        <button className="osz-button" onClick={onClose}>Close</button>
      </div>

      {err && (
        <div style={{ color: "#8b1a1a", padding: 8, border: "1.5px solid #8b1a1a", borderRadius: 0, marginTop: 12, background: "#f5f3ee" }}>
          {err}
        </div>
      )}

      <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
        {items.length === 0 && (
          <div style={{ color: "#5c5852", padding: 12, border: "1.5px solid #0a0a0a", borderRadius: 0 }}>
            No saved masterpiece prompts yet.
          </div>
        )}
        {items.map((item) => (
          <div
            key={item.id}
            style={{
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              padding: 12,
              background: "#f5f3ee",
            }}
          >
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{item.prompt}</div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 10 }}>
              <span style={{ color: "#5c5852", fontSize: 12 }}>
                {new Date(item.created_at).toLocaleString()}
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                {playgroundOpen && (
                  <button
                    disabled={busy !== null || laneCount == null || laneCount >= maxLanes}
                    onClick={() => void useAsLane(item)}
                  >
                    Use as lane
                  </button>
                )}
                <button disabled={busy !== null} onClick={() => void deleteItem(item)}>
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function RuntimeConfigPanel({
  config,
  modelOptions,
  onChange,
  busy,
}: {
  config: RuntimeConfig;
  modelOptions: RuntimeModelOptions | null;
  onChange: (config: RuntimeConfig) => void;
  busy: boolean;
}) {
  const stages = Object.entries(modelOptions?.stages ?? {});
  const effortOptions = modelOptions?.thinking_efforts?.options ?? [];
  const hasKey = hasRuntimeZenmuxKey(config);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const overrideCount =
    Object.keys(config.modelOverrides).length + Object.keys(config.thinkingEffortOverrides).length;

  function update(next: Partial<RuntimeConfig>) {
    onChange({ ...config, ...next });
  }

  function updateModelOverride(stage: string, value: string) {
    const next = { ...config.modelOverrides };
    if (value) next[stage] = value;
    else delete next[stage];
    update({ modelOverrides: next });
  }

  function updateEffortOverride(stage: string, value: ThinkingEffort | "") {
    const next = { ...config.thinkingEffortOverrides };
    if (value) next[stage] = value;
    else delete next[stage];
    update({ thinkingEffortOverrides: next });
  }

  return (
    <section className="osz-panel runtime-config-panel">
      <div className="osz-panel-body">
      <div className="osz-section-header">
        <div className="osz-section-title">
          <span className="osz-step-badge">1</span>
          <h2>Connect ZenMux</h2>
        </div>
        <span
          className={`osz-status ${hasKey ? "osz-status-ready" : "osz-status-required"}`}
        >
          {hasKey ? "Key ready" : "Key required"}
        </span>
      </div>
      <div className="runtime-config-primary">
        <label className="osz-field">
          ZenMux API key
          <input
            type="password"
            autoComplete="off"
            value={config.zenmuxApiKey}
            disabled={busy}
            onChange={(e) => update({ zenmuxApiKey: e.target.value })}
            placeholder="sk-..."
            className="osz-control"
          />
        </label>
      </div>
      <div className="runtime-config-note">
        Stored only in this browser session. The key is sent to this site for active requests.
      </div>

      <div className={`osz-disclosure ${showAdvanced ? "osz-disclosure-open" : ""}`}>
        <button
          type="button"
          className="osz-disclosure-summary"
          aria-expanded={showAdvanced}
          onClick={() => setShowAdvanced((open) => !open)}
        >
          <span>Advanced connection and models</span>
          <span className="osz-muted">
            {overrideCount === 0 ? "recommended defaults" : `${overrideCount} override${overrideCount === 1 ? "" : "s"}`}
          </span>
        </button>
        {showAdvanced && <>
        <div className="runtime-advanced-settings">
          <label className="osz-field">
            ZenMux base URL
            <input
              type="url"
              value={config.zenmuxBaseUrl || DEFAULT_ZENMUX_BASE_URL}
              disabled={busy}
              onChange={(e) => update({ zenmuxBaseUrl: e.target.value || DEFAULT_ZENMUX_BASE_URL })}
              className="osz-control"
            />
          </label>
        </div>
        <div className="runtime-model-grid">
          {stages.map(([stage, stageOptions]) => {
            const effortValue = config.thinkingEffortOverrides[stage] ?? "";
            return (
              <div key={stage} className="runtime-model-card">
                <label className="osz-field">
                  {stageOptions.label}
                  <select
                    value={config.modelOverrides[stage] ?? ""}
                    disabled={busy || !modelOptions}
                    onChange={(e) => updateModelOverride(stage, e.target.value)}
                    className="osz-control"
                  >
                    <option value="">Recommended default</option>
                    {stageOptions.options.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="osz-muted">Default: {stageOptions.default_model}</div>
                {stageOptions.supports_thinking_effort && (
                  <label className="osz-field">
                    Thinking effort
                    <select
                      value={effortValue}
                      disabled={busy || !modelOptions}
                      onChange={(e) => updateEffortOverride(stage, e.target.value as ThinkingEffort | "")}
                      className="osz-control"
                    >
                      <option value="">Provider default</option>
                      {effortOptions.map((option) => (
                        <option key={option.id} value={option.id}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            );
          })}
        </div>
        </>}
      </div>

      <div className="runtime-config-actions">
        <button
          type="button"
          disabled={busy}
          onClick={() => update({ zenmuxApiKey: "" })}
          className="osz-button"
        >
          Clear key
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            onChange({
              zenmuxApiKey: config.zenmuxApiKey,
              zenmuxBaseUrl: DEFAULT_ZENMUX_BASE_URL,
              modelOverrides: {},
              thinkingEffortOverrides: {},
            })
          }
          className="osz-button"
        >
          Reset models
        </button>
      </div>
      </div>
    </section>
  );
}

function SharedDeckView({
  shared,
  err,
  forking,
  onFork,
  onNewDeck,
}: {
  shared: SharedDeckResponse;
  err: string | null;
  forking: boolean;
  onFork: () => void;
  onNewDeck: () => void;
}) {
  const [currentSlide, setCurrentSlide] = useState(0);
  const values = shared.deck.values || {};
  const slides = useMemo(() => {
    const raw = (values.html_slides ?? {}) as Record<string | number, string>;
    return Object.fromEntries(
      Object.entries(raw)
        .map(([key, html]) => [Number(key), html] as const)
        .filter(([key, html]) => Number.isInteger(key) && typeof html === "string" && html.length > 0),
    ) as Record<number, string>;
  }, [values.html_slides]);
  const slideOrder = useMemo(() => Object.keys(slides).map(Number).sort((a, b) => a - b), [slides]);

  useEffect(() => {
    if (slideOrder.length > 0 && !slideOrder.includes(currentSlide)) {
      setCurrentSlide(slideOrder[0]);
    }
  }, [currentSlide, slideOrder]);

  const deckName = (values.deck_name as string | undefined) || shared.source_thread_id;
  const stage = (values.current_stage as string | undefined) || "unknown";
  const aspectRatio = (values.aspect_ratio as "16:9" | "4:3" | "21:9" | undefined) ?? "16:9";

  return (
    <div style={{ padding: 16, maxWidth: 1280, margin: "0 auto" }}>
      <header className="osz-app-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <strong
            style={{
              fontFamily: "'Playfair Display', Georgia, serif",
              fontSize: 20,
              fontWeight: 900,
              letterSpacing: -0.3,
            }}
          >
            Open Slides Zero
          </strong>
          <span style={{ color: "#948e83" }}>·</span>
          <span style={{ fontSize: 14, color: "#f5f3ee" }}>Shared deck</span>
          <span style={{ color: "#948e83" }}>·</span>
          <span style={{ fontSize: 13, color: "#cfc8b9" }}>
            stage: <code style={{ fontFamily: "ui-monospace, 'SF Mono', monospace" }}>{stage}</code>
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button
            className="osz-header-btn"
            disabled={forking}
            onClick={onFork}
            title="Create an owned copy that uses your browser identity and your ZenMux key for future edits."
          >
            {forking ? "Forking..." : "Fork to edit"}
          </button>
          <button className="osz-header-btn" disabled={forking} onClick={onNewDeck}>
            New deck
          </button>
        </div>
      </header>

      {err && (
        <div style={{ color: "#8b1a1a", padding: 8, border: "1.5px solid #8b1a1a", borderRadius: 0, marginBottom: 8, background: "#f5f3ee" }}>
          {err}
        </div>
      )}

      <section className="osz-panel" style={{ marginTop: 16 }}>
        <div className="osz-panel-body">
          <div className="osz-section-header">
            <div>
              <h2 style={{ margin: 0, color: "#0a0a0a", fontSize: 22 }}>{deckName}</h2>
              <div style={{ color: "#5c5852", fontSize: 13, lineHeight: 1.55, marginTop: 4 }}>
                This is a read-only shared view. Fork it to your own history before editing; generation and edits use your own ZenMux key.
              </div>
            </div>
          </div>
          {slideOrder.length > 0 ? (
            <DeckCanvas
              slides={slides}
              slideOrder={slideOrder}
              currentSlide={currentSlide}
              onSelectSlide={setCurrentSlide}
              aspectRatio={aspectRatio}
              width={960}
            />
          ) : (
            <div style={{ padding: 16, border: "1.5px solid #0a0a0a", color: "#5c5852" }}>
              This shared deck does not have rendered slides yet. Fork it to continue from the latest checkpoint.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// ---------------- Create form ----------------

function CreateForm({
  onSubmit,
  busy,
  err,
  catalog,
  runtimeConfig,
  runtimeModelOptions,
  onRuntimeConfigChange,
  recentDecks,
  ownedDecks,
  deletingDeckId,
  onLoadDeck,
  onDeleteDeck,
}: {
  onSubmit: (f: {
    deckName: string;
    text: string;
    pages: number;
    aspect: string;
    density: string;
    agentMode: AgentMode;
    styleHint: string;
    visualStylePresetId: string | null;
    imageUrls: string[];
    modelOverrides: Partial<Record<ModelStage, string>>;
    thinkingEffortOverrides: Partial<Record<ModelStage, ThinkingEffort>>;
    files: File[];
  }) => void;
  busy: boolean;
  err: string | null;
  catalog: CatalogResponse | null;
  runtimeConfig: RuntimeConfig;
  runtimeModelOptions: RuntimeModelOptions | null;
  onRuntimeConfigChange: (config: RuntimeConfig) => void;
  recentDecks: DeckListItem[] | null;
  ownedDecks: DeckListItem[];
  deletingDeckId: string | null;
  onLoadDeck: (id: string) => void;
  onDeleteDeck: (id: string, name?: string | null) => void;
}) {
  const [deckName, setDeckName] = useState("");
  const [text, setText] = useState("");
  const [pages, setPages] = useState(8);
  const [aspect, setAspect] = useState("16:9");
  const [density, setDensity] = useState("balanced");
  const [agentMode, setAgentMode] = useState<AgentMode>("default");
  const [styleHint, setStyleHint] = useState("");
  const [imageUrlText, setImageUrlText] = useState("");
  const [visualPresetId, setVisualPresetId] = useState("");
  const [modelOverrides, setModelOverrides] = useState<Partial<Record<ModelStage, string>>>({});
  const [thinkingEffortOverrides, setThinkingEffortOverrides] = useState<
    Partial<Record<ModelStage, ThinkingEffort>>
  >({});
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [recentSearch, setRecentSearch] = useState("");
  const [recentPage, setRecentPage] = useState(1);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const visualPresets = catalog?.visual_style_presets ?? [];
  const selectedPreset = visualPresets.find((preset) => preset.id === visualPresetId);
  const imageUrls = splitImageUrls(imageUrlText);
  const filteredRecentDecks = useMemo(() => {
    const decks = recentDecks ?? [];
    const q = recentSearch.trim().toLowerCase();
    if (!q) return decks;
    return decks.filter((deck) => recentDeckSearchText(deck).includes(q));
  }, [recentDecks, recentSearch]);
  const recentPageCount = Math.max(1, Math.ceil(filteredRecentDecks.length / RECENT_DECKS_PAGE_SIZE));
  const boundedRecentPage = Math.min(recentPage, recentPageCount);
  const visibleRecentDecks = filteredRecentDecks.slice(
    (boundedRecentPage - 1) * RECENT_DECKS_PAGE_SIZE,
    boundedRecentPage * RECENT_DECKS_PAGE_SIZE,
  );

  useEffect(() => {
    setRecentPage(1);
  }, [recentSearch]);

  useEffect(() => {
    if (recentPage > recentPageCount) setRecentPage(recentPageCount);
  }, [recentPage, recentPageCount]);

  function addFiles(nextFiles: File[]) {
    const accepted: File[] = [];
    const rejected: string[] = [];
    for (const file of nextFiles) {
      if (isSupportedUpload(file)) {
        accepted.push(file);
      } else {
        rejected.push(file.name);
      }
    }
    if (accepted.length > 0) {
      setFiles((prev) => [...prev, ...accepted]);
    }
    setFileError(
      rejected.length > 0
        ? `Unsupported file type: ${rejected.join(", ")}`
        : null,
    );
  }

  return (
    <div className="osz-create-shell">
      <header className="osz-create-hero">
        <div className="osz-eyebrow">— Compose a new deck</div>
        <h1>Open Slides Zero</h1>
        <p>
          Create a private deck from text, files, and image links. Bring your ZenMux key;
          deck access stays tied to this browser.
        </p>
      </header>
      {err && (
        <div className="osz-error">
          {err}
        </div>
      )}

      <RuntimeConfigPanel
        config={runtimeConfig}
        modelOptions={runtimeModelOptions}
        onChange={onRuntimeConfigChange}
        busy={busy}
      />

      <section className="osz-panel">
        <div className="osz-panel-body">
        <div className="osz-section-header">
          <div className="osz-section-title">
            <span className="osz-step-badge">2</span>
            <h2>Deck settings</h2>
          </div>
        </div>
        <div style={{ display: "grid", gap: 14 }}>
          <label className="osz-field">
            Deck name (optional)
            <input
              type="text"
              value={deckName}
              onChange={(e) => setDeckName(e.target.value)}
              placeholder="e.g. Q3 Earnings Review"
              className="osz-control"
            />
          </label>
          <div className="osz-grid-3">
            <label className="osz-field">
              Pages
              <input
                type="number"
                min={3}
                max={30}
                value={pages}
                onChange={(e) => setPages(Number(e.target.value))}
                className="osz-control"
              />
            </label>
            <label className="osz-field">
              Aspect
              <select value={aspect} onChange={(e) => setAspect(e.target.value)} className="osz-control">
                <option>16:9</option>
                <option>4:3</option>
                <option>21:9</option>
              </select>
            </label>
            <label className="osz-field">
              Density
              <select
                value={density}
                onChange={(e) => setDensity(e.target.value)}
                className="osz-control"
              >
                <option>minimal</option>
                <option>balanced</option>
                <option>dense</option>
                <option>very_dense</option>
              </select>
            </label>
            <label className="osz-field" title="Advanced opens a chat-first planning workflow and keeps the per-thread RAG index.">
              Agent
              <select
                value={agentMode}
                onChange={(e) => setAgentMode(e.target.value as AgentMode)}
                className="osz-control"
              >
                <option value="default">default</option>
                <option value="advanced">advanced chat</option>
              </select>
            </label>
            <label className="osz-field">
              Visual direction
              <select
                value={visualPresetId}
                onChange={(e) => setVisualPresetId(e.target.value)}
                className="osz-control"
              >
                <option value="">AI Decide</option>
                {visualPresets.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="osz-field">
              Style hint
              <input
                placeholder="e.g. MBB slate"
                value={styleHint}
                onChange={(e) => setStyleHint(e.target.value)}
                className="osz-control"
              />
            </label>
          </div>
          {selectedPreset && (
            <div className="osz-muted">
              {selectedPreset.description}
            </div>
          )}
        </div>
        </div>
      </section>

      <section className="osz-panel">
        <div className="osz-panel-body">
        <div className="osz-section-header">
          <div className="osz-section-title">
            <span className="osz-step-badge">3</span>
            <h2>Source material</h2>
          </div>
          <div className="osz-muted">TXT, Markdown, PDF, PPTX, images, DOCX, XLSX</div>
        </div>
        <div style={{ display: "grid", gap: 14 }}>
          <label className="osz-field">
            Material (text, bullets, or pasted notes)
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={9}
              className="osz-control"
            />
          </label>
          <div className="osz-field">
            <span>Upload source files</span>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={SUPPORTED_UPLOAD_ACCEPT}
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []));
                e.currentTarget.value = "";
              }}
              style={{ display: "none" }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                disabled={busy}
                onClick={() => fileInputRef.current?.click()}
                className="osz-button"
              >
                Choose files
              </button>
              <span className="osz-muted">
                {files.length === 0
                  ? "No files selected"
                  : `${files.length} file${files.length === 1 ? "" : "s"} selected`}
              </span>
            </div>
          </div>
          <label className="osz-field">
            Image URLs for insertion
            <textarea
              value={imageUrlText}
              onChange={(e) => setImageUrlText(e.target.value)}
              rows={3}
              placeholder="One image URL per line"
              className="osz-control"
            />
          </label>
          {fileError && (
            <div className="osz-warning">
              {fileError}
            </div>
          )}
          {files.length > 0 && (
            <div className="osz-file-list">
              {files.map((file, idx) => (
                <div
                  key={`${file.name}-${file.size}-${idx}`}
                  className="osz-file-row"
                >
                  <div style={{ minWidth: 0 }}>
                    <div className="osz-file-name">
                      {file.name}
                    </div>
                    <div className="osz-muted">{formatFileSize(file.size)}</div>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setFiles((prev) => prev.filter((_, fileIdx) => fileIdx !== idx))}
                    className="osz-button osz-button-danger"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        </div>
      </section>

      <div className="osz-create-actions">
        <button
          disabled={
            busy ||
            Boolean(deletingDeckId) ||
            !hasRuntimeZenmuxKey(runtimeConfig) ||
            (!text.trim() && files.length === 0 && imageUrls.length === 0)
          }
          onClick={() =>
            onSubmit({
              deckName,
              text,
              pages,
              aspect,
              density,
              agentMode,
              styleHint,
              visualStylePresetId: visualPresetId || null,
              imageUrls,
              modelOverrides,
              thinkingEffortOverrides,
              files,
            })
          }
          className="osz-button osz-button-primary"
        >
          {busy
            ? "Streaming…"
            : deletingDeckId
              ? "Deleting…"
              : !hasRuntimeZenmuxKey(runtimeConfig)
                ? "Add ZenMux key"
                : "Create deck"}
        </button>
        {!hasRuntimeZenmuxKey(runtimeConfig) && (
          <span className="osz-muted">Enter a ZenMux key above to start.</span>
        )}
      </div>

      {recentDecks && recentDecks.length > 0 && (
        <div className="recent-decks-panel">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <div>
              <h2 style={{ fontSize: 16, margin: 0, color: "#0a0a0a" }}>Current session decks</h2>
              <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.45, marginTop: 3 }}>
                This list is scoped to this tab session and clears when the tab session ends.
              </div>
            </div>
            <input
              type="search"
              value={recentSearch}
              onChange={(e) => setRecentSearch(e.target.value)}
              placeholder="Search decks"
              style={{
                width: 220,
                maxWidth: "50%",
                fontFamily: "inherit",
                padding: "6px 8px",
              }}
            />
          </div>
          <div
            style={{
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              overflow: "hidden",
            }}
          >
            {visibleRecentDecks.length === 0 && (
              <div style={{ padding: "12px 14px", color: "#5c5852", fontSize: 13 }}>
                No decks match "{recentSearch.trim()}".
              </div>
            )}
            {visibleRecentDecks.map((d, idx) => (
              <div
                key={d.thread_id}
                style={{
                  display: "flex",
                  alignItems: "stretch",
                  width: "100%",
                  background: "#f5f3ee",
                  borderBottom: idx === visibleRecentDecks.length - 1 ? "none" : "1px solid #0a0a0a",
                }}
              >
                <button
                  disabled={busy || Boolean(deletingDeckId)}
                  onClick={() => onLoadDeck(d.thread_id)}
                  style={{
                    flex: "1 1 auto",
                    minWidth: 0,
                    textAlign: "left",
                    padding: "10px 14px",
                    background: "#f5f3ee",
                    border: "none",
                    cursor: busy || deletingDeckId ? "default" : "pointer",
                    fontFamily: "inherit",
                    fontSize: 14,
                    opacity: busy || deletingDeckId ? 0.6 : 1,
                  }}
                  onMouseEnter={(e) => {
                    if (!busy && !deletingDeckId) e.currentTarget.style.background = "#e8e3d8";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "#f5f3ee";
                  }}
                >
                  <div
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontWeight: 500,
                    }}
                  >
                    {d.deck_name || d.thread_id}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                    <code style={{ fontSize: 11, color: "#948e83" }}>{d.thread_id}</code>
                    {d.stage && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "#5c5852",
                          background: "transparent",
                          padding: "1px 5px",
                        }}
                      >
                        {d.stage}
                      </span>
                    )}
                    {d.created_at && (
                      <span style={{ fontSize: 11, color: "#948e83" }}>
                        {recentDeckDateText(d.created_at)}
                      </span>
                    )}
                  </div>
                </button>
                <button
                  type="button"
                  disabled={busy || Boolean(deletingDeckId)}
                  onClick={() => onDeleteDeck(d.thread_id, d.deck_name || d.thread_id)}
                  style={{
                    flex: "0 0 auto",
                    border: "none",
                    borderLeft: "1px solid #0a0a0a",
                    background: "#f5f3ee",
                    color: "#8b1a1a",
                    cursor: busy || deletingDeckId ? "default" : "pointer",
                    fontFamily: "inherit",
                    fontSize: 13,
                    padding: "0 14px",
                    opacity: busy || deletingDeckId ? 0.6 : 1,
                  }}
                >
                  {deletingDeckId === d.thread_id ? "Deleting…" : "Delete"}
                </button>
              </div>
            ))}
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              marginTop: 10,
              color: "#5c5852",
              fontSize: 12,
            }}
          >
            <span>
              {filteredRecentDecks.length} deck{filteredRecentDecks.length === 1 ? "" : "s"}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                type="button"
                disabled={busy || Boolean(deletingDeckId) || boundedRecentPage <= 1}
                onClick={() => setRecentPage((page) => Math.max(1, page - 1))}
              >
                Previous
              </button>
              <span>
                page {boundedRecentPage} of {recentPageCount}
              </span>
              <button
                type="button"
                disabled={busy || Boolean(deletingDeckId) || boundedRecentPage >= recentPageCount}
                onClick={() => setRecentPage((page) => Math.min(recentPageCount, page + 1))}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
      <DeckHistoryPanel
        title="My deck history"
        description="Saved decks owned by this browser. Use this to recover older decks after the current tab session ends; access is lost if cookies or site data are cleared."
        decks={ownedDecks}
        emptyText="No saved deck history for this browser."
        busy={busy}
        deletingDeckId={deletingDeckId}
        onLoadDeck={onLoadDeck}
        onDeleteDeck={onDeleteDeck}
      />
    </div>
  );
}

function DeckHistoryPanel({
  title,
  description,
  decks,
  emptyText,
  busy,
  deletingDeckId,
  onLoadDeck,
  onDeleteDeck,
}: {
  title: string;
  description: string;
  decks: DeckListItem[];
  emptyText: string;
  busy: boolean;
  deletingDeckId: string | null;
  onLoadDeck: (id: string) => void;
  onDeleteDeck: (id: string, name?: string | null) => void;
}) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const filteredDecks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return decks;
    return decks.filter((deck) => recentDeckSearchText(deck).includes(q));
  }, [decks, search]);
  const pageCount = Math.max(1, Math.ceil(filteredDecks.length / RECENT_DECKS_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount);
  const visibleDecks = filteredDecks.slice(
    (boundedPage - 1) * RECENT_DECKS_PAGE_SIZE,
    boundedPage * RECENT_DECKS_PAGE_SIZE,
  );

  useEffect(() => {
    setPage(1);
  }, [search]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  if (decks.length === 0) {
    return (
      <div className="recent-decks-panel">
        <h2 style={{ fontSize: 16, margin: 0, color: "#0a0a0a" }}>{title}</h2>
        <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.45, marginTop: 3 }}>
          {description}
        </div>
        <div style={{ padding: "12px 14px", color: "#5c5852", fontSize: 13, border: "1.5px solid #0a0a0a", marginTop: 12 }}>
          {emptyText}
        </div>
      </div>
    );
  }

  return (
    <div className="recent-decks-panel">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <div>
          <h2 style={{ fontSize: 16, margin: 0, color: "#0a0a0a" }}>{title}</h2>
          <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.45, marginTop: 3 }}>
            {description}
          </div>
        </div>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search decks"
          style={{
            width: 220,
            maxWidth: "50%",
            fontFamily: "inherit",
            padding: "6px 8px",
          }}
        />
      </div>
      <div
        style={{
          border: "1.5px solid #0a0a0a",
          borderRadius: 0,
          overflow: "hidden",
        }}
      >
        {visibleDecks.length === 0 && (
          <div style={{ padding: "12px 14px", color: "#5c5852", fontSize: 13 }}>
            No decks match "{search.trim()}".
          </div>
        )}
        {visibleDecks.map((d, idx) => (
          <div
            key={d.thread_id}
            style={{
              display: "flex",
              alignItems: "stretch",
              width: "100%",
              background: "#f5f3ee",
              borderBottom: idx === visibleDecks.length - 1 ? "none" : "1px solid #0a0a0a",
            }}
          >
            <button
              disabled={busy || Boolean(deletingDeckId)}
              onClick={() => onLoadDeck(d.thread_id)}
              style={{
                flex: "1 1 auto",
                minWidth: 0,
                textAlign: "left",
                padding: "10px 14px",
                background: "#f5f3ee",
                border: "none",
                cursor: busy || deletingDeckId ? "default" : "pointer",
                fontFamily: "inherit",
                fontSize: 14,
                opacity: busy || deletingDeckId ? 0.6 : 1,
              }}
              onMouseEnter={(e) => {
                if (!busy && !deletingDeckId) e.currentTarget.style.background = "#e8e3d8";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "#f5f3ee";
              }}
            >
              <div
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  fontWeight: 500,
                }}
              >
                {d.deck_name || d.thread_id}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
                <code style={{ fontSize: 11, color: "#948e83" }}>{d.thread_id}</code>
                {d.stage && (
                  <span
                    style={{
                      fontSize: 10,
                      color: "#5c5852",
                      background: "transparent",
                      padding: "1px 5px",
                    }}
                  >
                    {d.stage}
                  </span>
                )}
                {d.created_at && (
                  <span style={{ fontSize: 11, color: "#948e83" }}>
                    {recentDeckDateText(d.created_at)}
                  </span>
                )}
              </div>
            </button>
            <button
              type="button"
              disabled={busy || Boolean(deletingDeckId)}
              onClick={() => onDeleteDeck(d.thread_id, d.deck_name || d.thread_id)}
              style={{
                flex: "0 0 auto",
                border: "none",
                borderLeft: "1px solid #0a0a0a",
                background: "#f5f3ee",
                color: "#8b1a1a",
                cursor: busy || deletingDeckId ? "default" : "pointer",
                fontFamily: "inherit",
                fontSize: 13,
                padding: "0 14px",
                opacity: busy || deletingDeckId ? 0.6 : 1,
              }}
            >
              {deletingDeckId === d.thread_id ? "Deleting..." : "Delete"}
            </button>
          </div>
        ))}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginTop: 10,
          color: "#5c5852",
          fontSize: 12,
        }}
      >
        <span>
          {filteredDecks.length} deck{filteredDecks.length === 1 ? "" : "s"}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            disabled={busy || Boolean(deletingDeckId) || boundedPage <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Previous
          </button>
          <span>
            page {boundedPage} of {pageCount}
          </span>
          <button
            type="button"
            disabled={busy || Boolean(deletingDeckId) || boundedPage >= pageCount}
            onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
