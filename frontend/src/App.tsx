// Top-level workflow UI with SSE streaming.
//
//   1. Create deck form (materials + expected pages + aspect)
//   2. LiveStream pane shows tokens as each subagent generates
//   3. HITL panels when an interrupt arrives; markdown-rendered
//   4. Deck canvas + comment overlay once slides are rendered
//   5. History + regenerate controls

import { useCallback, useEffect, useRef, useState } from "react";
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
  type CatalogResponse,
  type CreateDeckBody,
  type DeckListItem,
  type DeckState,
  type ForkFromReviewBody,
  type Masterpiece,
  type Material,
} from "./api";
import { streamSSE, type StreamEvent } from "./sse";
import {
  exportHtmlSingle,
  exportHtmlZip,
  exportPptx,
  hasExportableSlides,
} from "./exporter";

const CANVAS_W = 960;
const CANVAS_H = 540;
type ReviewStage = "structure" | "style" | "layout" | "brief" | "ready";
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

export function App() {
  const [deck, setDeck] = useState<DeckState | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [currentSlide, setCurrentSlide] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [showMasterpieces, setShowMasterpieces] = useState(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [deckList, setDeckList] = useState<DeckListItem[] | null>(null);
  const [selectedReviewStage, setSelectedReviewStage] = useState<ReviewStage>("ready");

  // Streaming state
  const [buffersByTag, setBuffersByTag] = useState<Record<string, string>>({});
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState<number | null>(null);
  const [elapsedByTag, setElapsedByTag] = useState<Record<string, number>>({});
  const tagStartRef = useRef<Record<string, number>>({});
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(
    async (id: string) => {
      const d = await api.getDeck(id);
      setDeck(d);
      if (!catalog) setCatalog(await api.getCatalog(id));
    },
    [catalog],
  );

  const refreshDeckList = useCallback(async () => {
    try {
      const { decks } = await api.listDecks();
      setDeckList(decks);
    } catch {
      setDeckList([]);
    }
  }, []);

  useEffect(() => {
    const saved = localStorage.getItem("osz.thread_id");
    if (saved) void refresh(saved).catch((e) => setErr(String(e)));
  }, [refresh]);

  useEffect(() => {
    if (!catalog) void api.getCatalog("catalog").then(setCatalog).catch(() => undefined);
  }, [catalog]);

  useEffect(() => {
    if (!deck) void refreshDeckList();
  }, [deck, refreshDeckList]);

  useEffect(() => {
    setSelectedReviewStage("ready");
    setCurrentSlide(0);
  }, [deck?.thread_id]);

  useEffect(() => {
    if ((deck?.values?.current_stage as string | undefined) !== "ready" || (deck?.interrupts?.length ?? 0) > 0) {
      setSelectedReviewStage("ready");
    }
  }, [deck?.values?.current_stage, deck?.interrupts?.length]);

  const rememberDeck = useCallback((nextDeck: DeckState) => {
    const name = (nextDeck.values?.deck_name as string) || nextDeck.thread_id;
    const histRaw = JSON.parse(localStorage.getItem("osz.history") || "[]") as (
      | { id: string; name: string }
      | string
    )[];
    const hist = histRaw.map((h) => (typeof h === "string" ? { id: h, name: h } : h));
    const filtered = hist.filter((h) => h.id !== nextDeck.thread_id);
    localStorage.setItem(
      "osz.history",
      JSON.stringify([{ id: nextDeck.thread_id, name }, ...filtered].slice(0, 20)),
    );
  }, []);

  // Handle a single SSE event stream to completion.
  async function consumeStream(url: string, body: unknown | FormData): Promise<void> {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBuffersByTag({});
    setActiveNode(null);
    setActiveSlide(null);
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
    styleHint: string;
    visualStylePresetId: string | null;
    files: File[];
  }) {
    if (form.files.length > 0) {
      const body = new FormData();
      if (form.deckName.trim()) body.append("deck_name", form.deckName.trim());
      if (form.text.trim()) body.append("text", form.text);
      body.append("expected_pages", String(form.pages));
      body.append("aspect_ratio", form.aspect);
      body.append("density_preference", form.density);
      body.append("language", "en");
      if (form.styleHint.trim()) {
        body.append("visual_style_preference", form.styleHint.trim());
      }
      if (form.visualStylePresetId) {
        body.append("visual_style_preset_id", form.visualStylePresetId);
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
      language: "en",
      visual_style_preference: form.styleHint || null,
      visual_style_preset_id: form.visualStylePresetId,
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

  async function onComment(text: string, box: { x: number; y: number; w: number; h: number }) {
    if (!deck) return;
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
    setErr(null);
    setBuffersByTag({});
    setShowMasterpieces(false);
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

  // --- Render ---

  if (!deck) {
    return (
      <CreateForm
        onSubmit={onCreate}
        busy={busy}
        err={err}
        catalog={catalog}
        recentDecks={deckList}
        onLoadDeck={(id) => void refresh(id).catch((e) => setErr(String(e)))}
      />
    );
  }

  const stage = (deck.values?.current_stage as string) ?? "";
  const hasInterrupt = (deck.interrupts?.length ?? 0) > 0;
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

  const showLive = busy || Object.keys(buffersByTag).length > 0;
  const readyReviewEnabled = stage === "ready" && !hasInterrupt;
  const reviewStepIndex = REVIEW_STAGES.findIndex((step) => step.id === selectedReviewStage);

  return (
    <div style={{ fontFamily: "Georgia, serif", padding: 16, maxWidth: 1520, margin: "0 auto" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "10px 14px",
          marginBottom: 14,
          border: "1px solid #e5e5e5",
          borderRadius: 8,
          background: "#fff",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 18 }}>Open Slides Zero</strong>
          <span style={{ color: "#ccc" }}>·</span>
          <span style={{ fontSize: 15, color: "#333", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {(deck.values?.deck_name as string) || deck.thread_id}
          </span>
          <span style={{ color: "#ccc" }}>·</span>
          <code style={{ color: "#999", fontSize: 12 }}>{deck.thread_id}</code>
          <span style={{ color: "#ccc" }}>·</span>
          <span style={{ fontSize: 13, color: "#555" }}>
            stage: <code>{stage}</code>
          </span>
          {stage === "html" && expectedCount > 0 && renderedCount < expectedCount && (
            <span style={{ fontSize: 13, color: "#64748b" }}>
              rendered {renderedCount}/{expectedCount}
            </span>
          )}
          {busy && (
            <span
              style={{
                marginLeft: 4,
                padding: "2px 8px",
                background: "#eff6ff",
                color: "#2563eb",
                border: "1px solid #bfdbfe",
                borderRadius: 12,
                fontSize: 12,
              }}
            >
              ● streaming…
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, position: "relative" }}>
          <button
            onClick={() => {
              setShowExport(false);
              setShowHistory(false);
              setShowMasterpieces((s) => !s);
            }}
          >
            Masterpieces
          </button>
          <button
            onClick={() => {
              setShowMasterpieces(false);
              setShowExport(false);
              setShowHistory((s) => {
                const next = !s;
                if (next) void refreshDeckList();
                return next;
              });
            }}
          >
            History deck
          </button>
          <div style={{ position: "relative" }}>
            <button
              disabled={!hasExportableSlides(deck) || exporting !== null}
              onClick={() => {
                setShowMasterpieces(false);
                setShowHistory(false);
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
                  minWidth: 200,
                  background: "#fff",
                  border: "1px solid #e5e5e5",
                  borderRadius: 6,
                  boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
                  zIndex: 100,
                  padding: "4px 0",
                }}
              >
                {[
                  { key: "html", label: "HTML (single file)", fn: exportHtmlSingle },
                  { key: "zip", label: "HTML (zip of slides)", fn: exportHtmlZip },
                  { key: "pptx", label: "PPTX (editable)", fn: exportPptx },
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
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button onClick={onNewDeck}>New deck</button>
          {showHistory && (
            <div
              style={{
                position: "absolute",
                top: "calc(100% + 4px)",
                right: 0,
                minWidth: 200,
                background: "#fff",
                border: "1px solid #e5e5e5",
                borderRadius: 6,
                boxShadow: "0 4px 12px rgba(0,0,0,0.1)",
                zIndex: 100,
                padding: "4px 0",
              }}
            >
              {(() => {
                // Merge backend list with localStorage history; backend is authoritative for names
                const backendDecks = deckList ?? [];
                const histRaw = JSON.parse(localStorage.getItem("osz.history") || "[]") as (
                  | { id: string; name: string }
                  | string
                )[];
                const localHist = histRaw.map((h) => (typeof h === "string" ? { id: h, name: h } : h));

                const merged = new Map<string, { name: string; stage: string }>();
                for (const d of backendDecks) {
                  merged.set(d.thread_id, { name: d.deck_name || d.thread_id, stage: d.stage });
                }
                for (const h of localHist) {
                  if (!merged.has(h.id)) {
                    merged.set(h.id, { name: h.name, stage: "" });
                  }
                }

                if (merged.size === 0) {
                  return (
                    <div style={{ padding: "8px 12px", color: "#999", fontSize: 13 }}>
                      No history yet
                    </div>
                  );
                }
                return Array.from(merged.entries()).map(([id, info]) => (
                  <button
                    key={id}
                    onClick={() => {
                      setShowHistory(false);
                      void refresh(id).catch((e) => setErr(String(e)));
                    }}
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
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }}>
                      {info.name}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <code style={{ fontSize: 11, color: "#999" }}>{id}</code>
                      {info.stage && (
                        <span style={{ fontSize: 10, color: "#64748b", background: "#f1f5f9", padding: "1px 4px", borderRadius: 4 }}>
                          {info.stage}
                        </span>
                      )}
                    </div>
                  </button>
                ));
              })()}
            </div>
          )}
        </div>
      </header>

      {err && (
        <div style={{ color: "crimson", padding: 8, border: "1px solid crimson", marginBottom: 8 }}>
          {err}
        </div>
      )}
      {materialWarnings.length > 0 && (
        <div
          style={{
            color: "#92400e",
            background: "#fffbeb",
            border: "1px solid #fcd34d",
            borderRadius: 6,
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
                border: "1px solid #e5e5e5",
                borderRadius: 6,
                background: "#fff",
              }}
            >
              <button
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
                      border: step.id === selectedReviewStage ? "1px solid #2563eb" : "1px solid #e5e5e5",
                      background: step.id === selectedReviewStage ? "#eff6ff" : "#fff",
                      color: step.id === selectedReviewStage ? "#1d4ed8" : "#374151",
                      padding: "4px 8px",
                      borderRadius: 999,
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    {idx + 1}. {step.label}
                  </button>
                ))}
              </div>
              <button
                disabled={reviewStepIndex >= REVIEW_STAGES.length - 1}
                onClick={() => setSelectedReviewStage(REVIEW_STAGES[reviewStepIndex + 1].id)}
              >
                Next step
              </button>
            </section>
          )}

          {hasInterrupt && (
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
                    border: "1px solid #fbbf24",
                    borderRadius: 6,
                    background: "#fffbeb",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                  }}
                >
                  <span style={{ fontSize: 13, color: "#92400e" }}>
                    {renderedCount < expectedCount
                      ? `Rendering paused: ${renderedCount}/${expectedCount} slides complete.`
                      : "Generation is paused — resume to continue."}
                  </span>
                  <button
                    style={{
                      padding: "4px 12px",
                      fontSize: 13,
                      background: "#f59e0b",
                      color: "#fff",
                      border: "none",
                      borderRadius: 4,
                      cursor: "pointer",
                    }}
                    onClick={() => onResume({})}
                  >
                    Resume generation
                  </button>
                </div>
              )}

              {!hasInterrupt && !hasSlides && outlineMd && (
                <section
                  style={{ padding: 12, border: "1px solid #e5e5e5", borderRadius: 6 }}
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
                      border: "1px solid #e5e5e5",
                      borderRadius: 6,
                    }}
                  >
                    <Markdown>{briefMd}</Markdown>
                  </section>
                </details>
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
                  <CommentLayer width={CANVAS_W} height={CANVAS_H} onSubmit={onComment} />
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
    <section style={{ border: "1px solid #e5e5e5", borderRadius: 6, background: "#fff", padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div>
          <h3 style={{ margin: 0 }}>Masterpieces</h3>
          {playgroundOpen && laneCount != null && (
            <div style={{ color: "#64748b", fontSize: 13, marginTop: 4 }}>
              Playground lanes: {laneCount}/{maxLanes}
            </div>
          )}
        </div>
        <button onClick={onClose}>Close</button>
      </div>

      {err && (
        <div style={{ color: "crimson", padding: 8, border: "1px solid crimson", marginTop: 12 }}>
          {err}
        </div>
      )}

      <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
        {items.length === 0 && (
          <div style={{ color: "#64748b", padding: 12, border: "1px solid #e5e5e5", borderRadius: 6 }}>
            No saved masterpiece prompts yet.
          </div>
        )}
        {items.map((item) => (
          <div
            key={item.id}
            style={{
              border: "1px solid #e5e5e5",
              borderRadius: 6,
              padding: 12,
              background: "#fafafa",
            }}
          >
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.45 }}>{item.prompt}</div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginTop: 10 }}>
              <span style={{ color: "#64748b", fontSize: 12 }}>
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

// ---------------- Create form ----------------

function CreateForm({
  onSubmit,
  busy,
  err,
  catalog,
  recentDecks,
  onLoadDeck,
}: {
  onSubmit: (f: {
    deckName: string;
    text: string;
    pages: number;
    aspect: string;
    density: string;
    styleHint: string;
    visualStylePresetId: string | null;
    files: File[];
  }) => void;
  busy: boolean;
  err: string | null;
  catalog: CatalogResponse | null;
  recentDecks: DeckListItem[] | null;
  onLoadDeck: (id: string) => void;
}) {
  const [deckName, setDeckName] = useState("");
  const [text, setText] = useState("");
  const [pages, setPages] = useState(8);
  const [aspect, setAspect] = useState("16:9");
  const [density, setDensity] = useState("balanced");
  const [styleHint, setStyleHint] = useState("");
  const [visualPresetId, setVisualPresetId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const visualPresets = catalog?.visual_style_presets ?? [];
  const selectedPreset = visualPresets.find((preset) => preset.id === visualPresetId);

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
    <div style={{ maxWidth: 720, margin: "60px auto", fontFamily: "Georgia, serif" }}>
      <h1>Open Slides Zero</h1>
      <p style={{ color: "#555" }}>
        Paste your source material, set a page target, then the agent walks you through
        three review gates (structure → style → layout) before rendering the deck.
      </p>
      {err && (
        <div style={{ color: "crimson", padding: 8, border: "1px solid crimson", marginBottom: 8 }}>
          {err}
        </div>
      )}
      <label style={{ display: "block", marginTop: 12 }}>
        Deck name (optional):
        <input
          type="text"
          value={deckName}
          onChange={(e) => setDeckName(e.target.value)}
          placeholder="e.g. Q3 Earnings Review"
          style={{ display: "block", width: "100%", fontFamily: "inherit", padding: 8 }}
        />
      </label>
      <label style={{ display: "block", marginTop: 12 }}>
        Material (text, bullets, or pasted notes):
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={10}
          style={{ display: "block", width: "100%", fontFamily: "inherit", padding: 8 }}
        />
      </label>
      <label style={{ display: "block", marginTop: 12 }}>
        Upload source files:
        <input
          type="file"
          multiple
          accept={SUPPORTED_UPLOAD_ACCEPT}
          onChange={(e) => {
            addFiles(Array.from(e.target.files ?? []));
            e.currentTarget.value = "";
          }}
          style={{ display: "block", width: "100%", marginTop: 6 }}
        />
      </label>
      <p style={{ color: "#555", fontSize: 13, marginTop: 6 }}>
        Supported: TXT, Markdown, PDF, PPTX, JPG, PNG, DOCX, XLSX.
      </p>
      {fileError && (
        <div
          style={{
            color: "#92400e",
            background: "#fffbeb",
            border: "1px solid #fcd34d",
            borderRadius: 6,
            padding: 8,
            marginTop: 8,
          }}
        >
          {fileError}
        </div>
      )}
      {files.length > 0 && (
        <div
          style={{
            marginTop: 10,
            border: "1px solid #e5e5e5",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          {files.map((file, idx) => (
            <div
              key={`${file.name}-${file.size}-${idx}`}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                padding: "8px 10px",
                borderBottom: idx === files.length - 1 ? "none" : "1px solid #f0f0f0",
                background: "#fff",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontSize: 14,
                  }}
                >
                  {file.name}
                </div>
                <div style={{ fontSize: 12, color: "#666" }}>{formatFileSize(file.size)}</div>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => setFiles((prev) => prev.filter((_, fileIdx) => fileIdx !== idx))}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginTop: 12 }}>
        <label>
          Pages
          <input
            type="number"
            min={3}
            max={30}
            value={pages}
            onChange={(e) => setPages(Number(e.target.value))}
            style={{ width: "100%" }}
          />
        </label>
        <label>
          Aspect
          <select value={aspect} onChange={(e) => setAspect(e.target.value)} style={{ width: "100%" }}>
            <option>16:9</option>
            <option>4:3</option>
            <option>21:9</option>
          </select>
        </label>
        <label>
          Density
          <select
            value={density}
            onChange={(e) => setDensity(e.target.value)}
            style={{ width: "100%" }}
          >
            <option>minimal</option>
            <option>balanced</option>
            <option>dense</option>
            <option>very_dense</option>
          </select>
        </label>
        <label>
          Visual direction
          <select
            value={visualPresetId}
            onChange={(e) => setVisualPresetId(e.target.value)}
            style={{ width: "100%" }}
          >
            <option value="">AI Decide</option>
            {visualPresets.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Style hint
          <input
            placeholder="e.g. MBB slate"
            value={styleHint}
            onChange={(e) => setStyleHint(e.target.value)}
            style={{ width: "100%" }}
          />
        </label>
      </div>
      {selectedPreset && (
        <div style={{ color: "#64748b", fontSize: 12, lineHeight: 1.45, marginTop: 6 }}>
          {selectedPreset.description}
        </div>
      )}
      <button
        style={{ marginTop: 16 }}
        disabled={busy || (!text.trim() && files.length === 0)}
        onClick={() =>
          onSubmit({
            deckName,
            text,
            pages,
            aspect,
            density,
            styleHint,
            visualStylePresetId: visualPresetId || null,
            files,
          })
        }
      >
        {busy ? "Streaming…" : "Create deck"}
      </button>

      {recentDecks && recentDecks.length > 0 && (
        <div style={{ marginTop: 40 }}>
          <h2 style={{ fontSize: 16, marginBottom: 12, color: "#333" }}>Recent decks</h2>
          <div
            style={{
              border: "1px solid #e5e5e5",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            {recentDecks.map((d) => (
              <button
                key={d.thread_id}
                disabled={busy}
                onClick={() => onLoadDeck(d.thread_id)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: "10px 14px",
                  background: "#fff",
                  border: "none",
                  borderBottom: "1px solid #f0f0f0",
                  cursor: busy ? "default" : "pointer",
                  fontFamily: "inherit",
                  fontSize: 14,
                  opacity: busy ? 0.6 : 1,
                }}
                onMouseEnter={(e) => {
                  if (!busy) e.currentTarget.style.background = "#f5f5f5";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "#fff";
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
                  <code style={{ fontSize: 11, color: "#999" }}>{d.thread_id}</code>
                  {d.stage && (
                    <span
                      style={{
                        fontSize: 10,
                        color: "#64748b",
                        background: "#f1f5f9",
                        padding: "1px 5px",
                        borderRadius: 4,
                      }}
                    >
                      {d.stage}
                    </span>
                  )}
                  {d.created_at && (
                    <span style={{ fontSize: 11, color: "#999" }}>
                      {new Date(d.created_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
