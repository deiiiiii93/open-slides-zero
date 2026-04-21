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
import { HitlReviewPanel } from "./HitlReviewPanel";
import { LiveStream } from "./LiveStream";
import { Markdown } from "./Markdown";
import {
  api,
  STREAM_BASE,
  type CatalogResponse,
  type CreateDeckBody,
  type DeckState,
  type Material,
} from "./api";
import { streamSSE, type StreamEvent } from "./sse";

const CANVAS_W = 960;
const CANVAS_H = 540;

export function App() {
  const [deck, setDeck] = useState<DeckState | null>(null);
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [currentSlide, setCurrentSlide] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Streaming state
  const [buffersByTag, setBuffersByTag] = useState<Record<string, string>>({});
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [activeSlide, setActiveSlide] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(
    async (id: string) => {
      const d = await api.getDeck(id);
      setDeck(d);
      if (!catalog) setCatalog(await api.getCatalog(id));
    },
    [catalog],
  );

  useEffect(() => {
    const saved = localStorage.getItem("osz.thread_id");
    if (saved) void refresh(saved).catch((e) => setErr(String(e)));
  }, [refresh]);

  // Handle a single SSE event stream to completion.
  async function consumeStream(url: string, body: unknown): Promise<void> {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBuffersByTag({});
    setActiveNode(null);
    setActiveSlide(null);
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
        setBuffersByTag((b) => ({ ...b, [tag]: (b[tag] ?? "") + ev.text }));
        break;
      }
      case "update": {
        // Opportunistically merge patch into deck.values so markdown previews
        // appear as soon as each node commits.
        setDeck((prev) =>
          prev ? { ...prev, values: { ...prev.values, ...sanitize(ev.patch) } } : prev,
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

  // --- Create / resume handlers ---

  async function onCreate(form: {
    text: string;
    pages: number;
    aspect: string;
    density: string;
    styleHint: string;
  }) {
    const mats: Material[] = [];
    if (form.text.trim()) mats.push({ kind: "text", uri: `text:${form.text}` });
    const body: CreateDeckBody = {
      expected_pages: form.pages,
      aspect_ratio: form.aspect,
      density_preference: form.density,
      language: "en",
      visual_style_preference: form.styleHint || null,
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

  async function onNewDeck() {
    abortRef.current?.abort();
    localStorage.removeItem("osz.thread_id");
    setDeck(null);
    setErr(null);
    setBuffersByTag({});
  }

  // --- Render ---

  if (!deck) return <CreateForm onSubmit={onCreate} busy={busy} err={err} />;

  const stage = (deck.values?.current_stage as string) ?? "";
  const hasInterrupt = (deck.interrupts?.length ?? 0) > 0;
  const slides = (deck.values?.html_slides as Record<number, string>) ?? {};
  const hasSlides = Object.keys(slides).length > 0;
  const outlineMd = deck.values?.outline_md as string | undefined;
  const briefMd = deck.values?.consolidated_brief_md as string | undefined;

  const showLive = busy || Object.keys(buffersByTag).length > 0;

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
          <code style={{ color: "#666" }}>{deck.thread_id}</code>
          <span style={{ color: "#ccc" }}>·</span>
          <span style={{ fontSize: 13, color: "#555" }}>
            stage: <code>{stage}</code>
          </span>
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
        <button onClick={onNewDeck}>New deck</button>
      </header>

      {err && (
        <div style={{ color: "crimson", padding: 8, border: "1px solid crimson", marginBottom: 8 }}>
          {err}
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
          {hasInterrupt && (
            <HitlReviewPanel deck={deck} catalog={catalog} onResume={onResume} />
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
              currentSlide={currentSlide}
              onSelectSlide={setCurrentSlide}
              aspectRatio={(deck.values?.aspect_ratio as any) ?? "16:9"}
              width={CANVAS_W}
            >
              <CommentLayer width={CANVAS_W} height={CANVAS_H} onSubmit={onComment} />
            </DeckCanvas>
          )}
        </main>

        {showLive && (
          <aside style={{ position: "sticky", top: 16, alignSelf: "start" }}>
            <LiveStream
              buffersByTag={buffersByTag}
              activeNode={activeNode}
              activeSlide={activeSlide}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

// ---------------- Create form ----------------

function CreateForm({
  onSubmit,
  busy,
  err,
}: {
  onSubmit: (f: {
    text: string;
    pages: number;
    aspect: string;
    density: string;
    styleHint: string;
  }) => void;
  busy: boolean;
  err: string | null;
}) {
  const [text, setText] = useState("");
  const [pages, setPages] = useState(8);
  const [aspect, setAspect] = useState("16:9");
  const [density, setDensity] = useState("balanced");
  const [styleHint, setStyleHint] = useState("");

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
        Material (text, bullets, or pasted notes):
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={10}
          style={{ display: "block", width: "100%", fontFamily: "inherit", padding: 8 }}
        />
      </label>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginTop: 12 }}>
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
          Style hint
          <input
            placeholder="e.g. MBB slate"
            value={styleHint}
            onChange={(e) => setStyleHint(e.target.value)}
            style={{ width: "100%" }}
          />
        </label>
      </div>
      <button
        style={{ marginTop: 16 }}
        disabled={busy || !text.trim()}
        onClick={() => onSubmit({ text, pages, aspect, density, styleHint })}
      >
        {busy ? "Streaming…" : "Create deck"}
      </button>
    </div>
  );
}
