import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CommentLayer } from "./CommentLayer";
import { DeckCanvas } from "./DeckCanvas";
import {
  HtmlStage,
  LayoutStage,
  StyleStage,
} from "./HitlReviewPanel";
import { LiveStream } from "./LiveStream";
import {
  api,
  STREAM_BASE,
  type CatalogResponse,
  type DeckState,
  type PlaygroundLane,
} from "./api";
import { normalizeImagePlaceholders } from "./imagePlaceholders";
import { streamSSE, type StreamEvent } from "./sse";

type Props = {
  deck: DeckState;
  catalog: CatalogResponse | null;
};

type LaneLiveState = {
  laneId: string;
  laneThreadId: string | null;
  buffersByTag: Record<string, string>;
  activeNode: string | null;
  activeSlide: number | null;
  elapsedByTag: Record<string, number>;
  isRunning: boolean;
  error: string | null;
};

type StreamContext = {
  laneId: string | null;
  threadId: string | null;
};

const ARENA_WIDTH = 360;
const LANE_CANVAS_WIDTH = 720;
const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

function firstInterrupt(state: DeckState | null): any {
  const i = state?.interrupts?.[0];
  if (!i) return null;
  return typeof i === "object" && "value" in i ? (i as any).value : i;
}

function mergeValues(prevValues: Record<string, any>, patch: Record<string, any>): Record<string, any> {
  const next = { ...prevValues, ...patch };
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

function laneIdLabel(laneId: string): string {
  return laneId.replace("lane-", "Lane ");
}

function laneLabel(lane: PlaygroundLane): string {
  return laneIdLabel(lane.lane_id);
}

function emptyLaneLive(laneId: string, laneThreadId: string | null = null): LaneLiveState {
  return {
    laneId,
    laneThreadId,
    buffersByTag: {},
    activeNode: null,
    activeSlide: null,
    elapsedByTag: {},
    isRunning: false,
    error: null,
  };
}

function hasLiveContent(live: LaneLiveState): boolean {
  return (
    live.isRunning ||
    live.error != null ||
    live.activeNode != null ||
    Object.keys(live.buffersByTag).length > 0
  );
}

function expectedSlideOrder(state: DeckState | null): number[] {
  const briefSlides = Array.isArray(state?.values?.brief?.slides)
    ? (state?.values?.brief?.slides as Array<{ slide_idx: number }>)
    : [];
  if (briefSlides.length) {
    return briefSlides
      .map((slide) => Number(slide.slide_idx))
      .filter((idx) => Number.isInteger(idx))
      .sort((a, b) => a - b);
  }
  const slides = (state?.values?.html_slides as Record<string, string> | undefined) ?? {};
  return Object.keys(slides)
    .map(Number)
    .filter((idx) => Number.isInteger(idx))
    .sort((a, b) => a - b);
}

export function PlaygroundPanel({ deck, catalog }: Props) {
  const [lanes, setLanes] = useState<PlaygroundLane[]>([]);
  const [maxLanes, setMaxLanes] = useState(5);
  const [activeLaneId, setActiveLaneId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [creatingLane, setCreatingLane] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"lanes" | "arena">("lanes");
  const [currentSlide, setCurrentSlide] = useState(0);

  const [liveByLane, setLiveByLane] = useState<Record<string, LaneLiveState>>({});
  const tagStartByLaneRef = useRef<Record<string, Record<string, number>>>({});
  const abortByLaneRef = useRef<Record<string, AbortController>>({});
  const createAbortRef = useRef<AbortController | null>(null);

  const refreshLanes = useCallback(async () => {
    const result = await api.listPlaygroundLanes(deck.thread_id);
    setLanes(result.lanes);
    setMaxLanes(result.max_lanes);
    setActiveLaneId((current) => current ?? result.lanes[0]?.lane_id ?? null);
  }, [deck.thread_id]);

  useEffect(() => {
    void refreshLanes().catch((e) => setErr(String(e)));
  }, [refreshLanes]);

  const activeLane = useMemo(
    () => lanes.find((lane) => lane.lane_id === activeLaneId) ?? lanes[0] ?? null,
    [activeLaneId, lanes],
  );
  const liveStreams = useMemo(() => {
    const laneOrder = new Map(lanes.map((lane, idx) => [lane.lane_id, idx]));
    return Object.values(liveByLane)
      .filter(hasLiveContent)
      .sort((a, b) => {
        const aOrder = laneOrder.get(a.laneId) ?? Number.MAX_SAFE_INTEGER;
        const bOrder = laneOrder.get(b.laneId) ?? Number.MAX_SAFE_INTEGER;
        return aOrder - bOrder || a.laneId.localeCompare(b.laneId);
      });
  }, [lanes, liveByLane]);

  function upsertLane(nextLane: PlaygroundLane) {
    setLanes((prev) => {
      const idx = prev.findIndex((lane) => lane.lane_id === nextLane.lane_id);
      if (idx === -1) return [...prev, nextLane];
      const copy = [...prev];
      copy[idx] = { ...copy[idx], ...nextLane };
      return copy;
    });
  }

  function updateLaneState(threadId: string, state: DeckState) {
    setLanes((prev) =>
      prev.map((lane) =>
        lane.lane_thread_id === threadId || lane.lane_id === state.values?.lane_id
          ? { ...lane, state }
          : lane,
      ),
    );
  }

  function updateLaneLive(laneId: string, updater: (live: LaneLiveState) => LaneLiveState) {
    setLiveByLane((prev) => {
      const current = prev[laneId] ?? emptyLaneLive(laneId);
      return { ...prev, [laneId]: updater(current) };
    });
  }

  function resetLaneLive(lane: PlaygroundLane) {
    tagStartByLaneRef.current = { ...tagStartByLaneRef.current, [lane.lane_id]: {} };
    setLiveByLane((prev) => ({
      ...prev,
      [lane.lane_id]: {
        ...emptyLaneLive(lane.lane_id, lane.lane_thread_id),
        isRunning: true,
      },
    }));
  }

  function startLiveForLane(ctx: StreamContext, laneId: string, threadId: string | null) {
    ctx.laneId = laneId;
    ctx.threadId = threadId;
    if (!tagStartByLaneRef.current[laneId]) {
      tagStartByLaneRef.current = { ...tagStartByLaneRef.current, [laneId]: {} };
    }
    updateLaneLive(laneId, (live) => ({
      ...live,
      laneThreadId: threadId ?? live.laneThreadId,
      isRunning: true,
      error: null,
    }));
  }

  function finishLiveForLane(laneId: string | null) {
    if (!laneId) return;
    updateLaneLive(laneId, (live) => ({
      ...live,
      isRunning: false,
      activeNode: null,
      activeSlide: null,
    }));
  }

  function applyLaneEvent(ev: StreamEvent, ctx: StreamContext) {
    switch (ev.type) {
      case "thread": {
        const laneId =
          ev.lane_id ??
          ctx.laneId ??
          lanes.find((lane) => lane.lane_thread_id === ev.thread_id)?.lane_id;
        if (laneId) startLiveForLane(ctx, laneId, ev.thread_id);
        if (ev.lane_id) setActiveLaneId(ev.lane_id);
        break;
      }
      case "lane": {
        const lane = ev.lane as PlaygroundLane;
        upsertLane(lane);
        startLiveForLane(ctx, lane.lane_id, lane.lane_thread_id);
        setActiveLaneId(lane.lane_id);
        break;
      }
      case "event": {
        const laneId = ctx.laneId;
        if (!laneId) return;
        updateLaneLive(laneId, (live) => ({
          ...live,
          activeNode: ev.node,
          activeSlide: ev.slide_idx ?? null,
          isRunning: true,
        }));
        break;
      }
      case "token": {
        const laneId = ctx.laneId;
        if (!laneId) return;
        const tag = ev.tag ?? "unknown";
        setLiveByLane((prev) => {
          const live = prev[laneId] ?? emptyLaneLive(laneId);
          const starts = tagStartByLaneRef.current[laneId] ?? {};
          if (!starts[tag]) {
            tagStartByLaneRef.current = {
              ...tagStartByLaneRef.current,
              [laneId]: { ...starts, [tag]: Date.now() },
            };
          }
          const start = tagStartByLaneRef.current[laneId]?.[tag] ?? Date.now();
          const buffersByTag = {
            ...live.buffersByTag,
            [tag]: (live.buffersByTag[tag] ?? "") + ev.text,
          };
          let elapsedByTag = live.elapsedByTag;
          if (
            tag.startsWith("html:") &&
            buffersByTag[tag].includes("</html>") &&
            !elapsedByTag[tag]
          ) {
            elapsedByTag = { ...elapsedByTag, [tag]: Date.now() - start };
          }
          return {
            ...prev,
            [laneId]: {
              ...live,
              buffersByTag,
              elapsedByTag,
              isRunning: true,
              error: null,
            },
          };
        });
        break;
      }
      case "update": {
        const { laneId, threadId } = ctx;
        setLanes((prev) =>
          prev.map((lane) => {
            if (!lane.state || (lane.lane_thread_id !== threadId && lane.lane_id !== laneId)) {
              return lane;
            }
            return {
              ...lane,
              state: {
                ...lane.state,
                values: mergeValues(lane.state.values, ev.patch),
              },
            };
          }),
        );
        break;
      }
      case "interrupt": {
        const laneId = ctx.laneId;
        if (!laneId) return;
        setLanes((prev) =>
          prev.map((lane) =>
            lane.lane_id === laneId && lane.state
              ? { ...lane, state: { ...lane.state, interrupts: [ev.payload] } }
              : lane,
          ),
        );
        break;
      }
      case "done": {
        if (ev.state?.thread_id) updateLaneState(ev.state.thread_id, ev.state as DeckState);
        const laneId =
          ctx.laneId ??
          (ev.state?.values?.lane_id as string | undefined) ??
          lanes.find((lane) => lane.lane_thread_id === ev.state?.thread_id)?.lane_id ??
          null;
        if (laneId) {
          setLiveByLane((prev) => {
            const live = prev[laneId] ?? emptyLaneLive(laneId, ev.state?.thread_id ?? null);
            const starts = tagStartByLaneRef.current[laneId] ?? {};
            const elapsedByTag = { ...live.elapsedByTag };
            for (const [tag, start] of Object.entries(starts)) {
              if (!elapsedByTag[tag]) elapsedByTag[tag] = Date.now() - start;
            }
            return {
              ...prev,
              [laneId]: {
                ...live,
                laneThreadId: ev.state?.thread_id ?? live.laneThreadId,
                elapsedByTag,
                isRunning: false,
                activeNode: null,
                activeSlide: null,
              },
            };
          });
        }
        break;
      }
      case "error": {
        setErr(ev.message);
        const laneId = ctx.laneId;
        if (laneId) {
          updateLaneLive(laneId, (live) => ({
            ...live,
            error: ev.message,
            isRunning: false,
            activeNode: null,
            activeSlide: null,
          }));
        }
        break;
      }
    }
  }

  async function consumeLaneStream(url: string, body: unknown, lane?: PlaygroundLane) {
    const ctx: StreamContext = {
      laneId: lane?.lane_id ?? null,
      threadId: lane?.lane_thread_id ?? null,
    };
    const ctrl = new AbortController();
    if (lane) {
      abortByLaneRef.current[lane.lane_id]?.abort();
      abortByLaneRef.current = { ...abortByLaneRef.current, [lane.lane_id]: ctrl };
      resetLaneLive(lane);
    } else {
      createAbortRef.current?.abort();
      createAbortRef.current = ctrl;
      setCreatingLane(true);
    }
    setErr(null);
    try {
      for await (const ev of streamSSE(url, body, ctrl.signal)) {
        applyLaneEvent(ev, ctx);
      }
      await refreshLanes();
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        const ownsStream = lane
          ? abortByLaneRef.current[lane.lane_id] === ctrl
          : createAbortRef.current === ctrl;
        if (!ownsStream) return;
        const message = String(e);
        setErr(message);
        const laneId = ctx.laneId;
        if (laneId) {
          updateLaneLive(laneId, (live) => ({
            ...live,
            error: message,
            isRunning: false,
            activeNode: null,
            activeSlide: null,
          }));
        }
      }
    } finally {
      const ownsStream = lane
        ? abortByLaneRef.current[lane.lane_id] === ctrl
        : createAbortRef.current === ctrl;
      if (!ownsStream) return;
      finishLiveForLane(ctx.laneId);
      if (lane) {
        setLiveByLane((prev) => ({
          ...prev,
          [lane.lane_id]: {
            ...(prev[lane.lane_id] ?? emptyLaneLive(lane.lane_id, lane.lane_thread_id)),
            isRunning: false,
          },
        }));
        const { [lane.lane_id]: _removed, ...rest } = abortByLaneRef.current;
        void _removed;
        abortByLaneRef.current = rest;
      } else {
        setCreatingLane(false);
        createAbortRef.current = null;
      }
    }
  }

  async function createLane(creatorPrompt: string) {
    if (lanes.length >= maxLanes) return;
    await consumeLaneStream(api.createPlaygroundLaneStreamUrl(deck.thread_id), {
      creator_prompt: creatorPrompt,
    });
    setPrompt("");
  }

  async function resumeLane(lane: PlaygroundLane, payload: Record<string, unknown>) {
    await consumeLaneStream(
      `${STREAM_BASE}/decks/${lane.lane_thread_id}/resume/stream`,
      { payload },
      lane,
    );
  }

  async function cutoffLane(lane: PlaygroundLane) {
    setErr(null);
    try {
      const result = await api.cutoffPlaygroundLane(deck.thread_id, lane.lane_id);
      upsertLane(result.lane);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function saveMasterpiece(lane: PlaygroundLane) {
    setErr(null);
    try {
      await api.saveLaneMasterpiece(deck.thread_id, lane.lane_id);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function commentOnLane(
    lane: PlaygroundLane,
    slideIdx: number,
    text: string,
    box: { x: number; y: number; w: number; h: number },
  ) {
    await consumeLaneStream(api.commentStreamUrl(lane.lane_thread_id, slideIdx), { text, box }, lane);
  }

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: liveStreams.length > 0 ? "minmax(0, 1fr) 360px" : "minmax(0, 1fr)",
        gap: 16,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: 12,
            border: "1px solid #e5e5e5",
            borderRadius: 6,
            background: "#fff",
            marginBottom: 12,
          }}
        >
          <div>
            <h3 style={{ margin: 0 }}>Creator playground</h3>
            <div style={{ color: "#64748b", fontSize: 13, marginTop: 3 }}>
              {lanes.length}/{maxLanes} lanes
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => setView("lanes")}
              style={{ border: view === "lanes" ? "1px solid #2563eb" : "1px solid #e5e5e5" }}
            >
              Lanes
            </button>
            <button
              onClick={() => setView("arena")}
              style={{ border: view === "arena" ? "1px solid #2563eb" : "1px solid #e5e5e5" }}
            >
              Arena
            </button>
          </div>
        </div>

        {err && (
          <div style={{ color: "crimson", padding: 8, border: "1px solid crimson", marginBottom: 8 }}>
            {err}
          </div>
        )}

        {view === "arena" ? (
          <ArenaView
            lanes={lanes}
            currentSlide={currentSlide}
            onSelectSlide={setCurrentSlide}
          />
        ) : (
          <>
            <div
              style={{
                border: "1px solid #e5e5e5",
                borderRadius: 6,
                padding: 12,
                background: "#fff",
                marginBottom: 12,
              }}
            >
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={4}
                placeholder="Extra instructions for a new lane"
                style={{ width: "100%", boxSizing: "border-box", fontFamily: "inherit", padding: 8 }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
                <span style={{ color: "#64748b", fontSize: 12 }}>
                  Blank lanes are allowed as a baseline.
                </span>
                <button disabled={creatingLane || lanes.length >= maxLanes} onClick={() => void createLane(prompt)}>
                  {creatingLane ? "Working..." : "Create lane"}
                </button>
              </div>
            </div>

            {lanes.length > 0 && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                {lanes.map((lane) => (
                  <button
                    key={lane.lane_id}
                    onClick={() => setActiveLaneId(lane.lane_id)}
                    style={{
                      border: lane.lane_id === activeLane?.lane_id ? "1px solid #2563eb" : "1px solid #e5e5e5",
                      background: lane.lane_id === activeLane?.lane_id ? "#eff6ff" : "#fff",
                      color: lane.cutoff ? "#92400e" : "#111827",
                    }}
                  >
                    {laneLabel(lane)}
                    {lane.cutoff ? " · cut off" : ""}
                  </button>
                ))}
              </div>
            )}

            {activeLane ? (
              <LaneDetail
                lane={activeLane}
                catalog={catalog}
                currentSlide={currentSlide}
                setCurrentSlide={setCurrentSlide}
                busy={Boolean(liveByLane[activeLane.lane_id]?.isRunning)}
                onResume={resumeLane}
                onComment={commentOnLane}
                onCutoff={cutoffLane}
                onSaveMasterpiece={saveMasterpiece}
              />
            ) : (
              <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
                Create a lane to start exploring alternatives.
              </div>
            )}
          </>
        )}
      </div>

      {liveStreams.length > 0 && (
        <aside
          style={{
            position: "sticky",
            top: 16,
            alignSelf: "start",
            display: "flex",
            flexDirection: "column",
            gap: 12,
            maxHeight: "calc(100vh - 120px)",
            overflowY: "auto",
          }}
        >
          {liveStreams.map((live) => (
            <LiveStream
              key={live.laneId}
              title={`${laneIdLabel(live.laneId)} live`}
              subtitle={live.error ? live.error : live.laneThreadId ?? undefined}
              isActive={live.isRunning}
              maxHeight={420}
              buffersByTag={live.buffersByTag}
              activeNode={live.activeNode}
              activeSlide={live.activeSlide}
              elapsedByTag={live.elapsedByTag}
            />
          ))}
        </aside>
      )}
    </section>
  );
}

function LaneDetail({
  lane,
  catalog,
  currentSlide,
  setCurrentSlide,
  busy,
  onResume,
  onComment,
  onCutoff,
  onSaveMasterpiece,
}: {
  lane: PlaygroundLane;
  catalog: CatalogResponse | null;
  currentSlide: number;
  setCurrentSlide: (idx: number) => void;
  busy: boolean;
  onResume: (lane: PlaygroundLane, payload: Record<string, unknown>) => Promise<void>;
  onComment: (
    lane: PlaygroundLane,
    slideIdx: number,
    text: string,
    box: { x: number; y: number; w: number; h: number },
  ) => Promise<void>;
  onCutoff: (lane: PlaygroundLane) => Promise<void>;
  onSaveMasterpiece: (lane: PlaygroundLane) => Promise<void>;
}) {
  const state = lane.state;
  const gate = firstInterrupt(state) as any;
  const stage = (state?.values?.current_stage as string | undefined) ?? "pending";
  const slides = (state?.values?.html_slides as Record<number, string>) ?? {};
  const slideOrder = expectedSlideOrder(state);
  const hasSlides = slideOrder.length > 0;
  const aspectRatio = (state?.values?.aspect_ratio as keyof typeof CANVAS | undefined) ?? "16:9";
  const [, baseH] = CANVAS[aspectRatio] ?? CANVAS["16:9"];
  const overlayHeight = (baseH * LANE_CANVAS_WIDTH) / (CANVAS[aspectRatio]?.[0] ?? CANVAS["16:9"][0]);
  const canComment = hasSlides && stage === "ready" && !busy && !lane.cutoff;
  const canContinue = Boolean(state && !gate && stage !== "ready" && !lane.cutoff && state.next.length > 0);

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 6, background: "#fff", padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0 }}>{laneLabel(lane)}</h3>
          <div style={{ color: "#64748b", fontSize: 12, marginTop: 4 }}>
            stage: <code>{stage}</code>
          </div>
          {lane.creator_prompt && (
            <div style={{ color: "#374151", fontSize: 13, marginTop: 8, whiteSpace: "pre-wrap" }}>
              {lane.creator_prompt}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          <button
            disabled={busy || lane.cutoff}
            onClick={() => void onCutoff(lane)}
          >
            Cut off lane
          </button>
          <button
            disabled={busy || !lane.creator_prompt.trim()}
            onClick={() => void onSaveMasterpiece(lane)}
          >
            Save masterpiece
          </button>
        </div>
      </div>

      {lane.cutoff && (
        <div style={{ color: "#92400e", background: "#fffbeb", border: "1px solid #fcd34d", borderRadius: 6, padding: 8, marginBottom: 12 }}>
          This lane is cut off. Existing outputs are preserved.
        </div>
      )}

      {!state && <div style={{ color: "#64748b" }}>Lane state is loading.</div>}

      {!lane.cutoff && gate?.gate === "style" && (
        <StyleStage
          title="Lane style"
          submitLabel="Revise"
          approveLabel="Approve style"
          visualStyleMd={gate.visual_style_md}
          visualStyle={gate.visual_style}
          onSubmit={async ({ feedback }) => onResume(lane, { revise: feedback })}
          onApprove={async () => onResume(lane, { approved: true })}
        />
      )}

      {!lane.cutoff && gate?.gate === "layout" && (
        <LayoutStage
          catalog={catalog}
          layouts={gate.layouts}
          title="Lane layouts"
          submitLabel="Approve layouts"
          onSubmit={async ({ overrides }) => onResume(lane, { approved: true, overrides })}
        />
      )}

      {!lane.cutoff && gate?.gate === "html" && (
        <HtmlStage
          title="Retry lane HTML"
          submitLabel="Retry failed slides"
          failedSlides={gate.failed_slides}
          renderedCount={gate.rendered_count}
          expectedCount={gate.expected_count}
          onSubmit={async () => onResume(lane, { retry_failed: true })}
        />
      )}

      {state && !gate && stage !== "ready" && !lane.cutoff && (
        <div style={{ color: "#64748b", display: "flex", alignItems: "center", gap: 10 }}>
          <span>Lane generation is between steps.</span>
          {canContinue && (
            <button disabled={busy} onClick={() => void onResume(lane, {})}>
              Continue lane
            </button>
          )}
        </div>
      )}

      {hasSlides && (
        <div style={{ marginTop: 12 }}>
          <DeckCanvas
            slides={slides}
            slideOrder={slideOrder}
            currentSlide={currentSlide}
            onSelectSlide={setCurrentSlide}
            aspectRatio={aspectRatio as any}
            width={LANE_CANVAS_WIDTH}
          >
            {canComment && (
              <CommentLayer
                width={LANE_CANVAS_WIDTH}
                height={overlayHeight}
                onSubmit={(text, box) => {
                  void onComment(lane, currentSlide, text, box);
                }}
              />
            )}
          </DeckCanvas>
        </div>
      )}
    </div>
  );
}

function ArenaView({
  lanes,
  currentSlide,
  onSelectSlide,
}: {
  lanes: PlaygroundLane[];
  currentSlide: number;
  onSelectSlide: (idx: number) => void;
}) {
  const slideOrder = useMemo(() => {
    const first = lanes.find((lane) => expectedSlideOrder(lane.state).length > 0);
    return first ? expectedSlideOrder(first.state) : [];
  }, [lanes]);

  useEffect(() => {
    if (slideOrder.length > 0 && !slideOrder.includes(currentSlide)) {
      onSelectSlide(slideOrder[0]);
    }
  }, [currentSlide, onSelectSlide, slideOrder]);

  if (lanes.length === 0) {
    return (
      <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
        Create lanes before opening the arena.
      </div>
    );
  }

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 6, background: "#fff", padding: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {slideOrder.map((idx) => (
          <button
            key={idx}
            onClick={() => onSelectSlide(idx)}
            style={{
              border: idx === currentSlide ? "1px solid #2563eb" : "1px solid #e5e5e5",
              background: idx === currentSlide ? "#eff6ff" : "#fff",
            }}
          >
            Slide {idx + 1}
          </button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
        {lanes.map((lane) => (
          <ArenaLane key={lane.lane_id} lane={lane} slideIdx={currentSlide} />
        ))}
      </div>
    </div>
  );
}

function ArenaLane({ lane, slideIdx }: { lane: PlaygroundLane; slideIdx: number }) {
  const state = lane.state;
  const slides = (state?.values?.html_slides as Record<number, string>) ?? {};
  const html = slides[slideIdx] ? normalizeImagePlaceholders(slides[slideIdx]) : undefined;
  const aspect = (state?.values?.aspect_ratio as string | undefined) ?? "16:9";
  const [baseW, baseH] = CANVAS[aspect] ?? CANVAS["16:9"];
  const scale = ARENA_WIDTH / baseW;

  return (
    <div style={{ border: "1px solid #e5e5e5", borderRadius: 6, padding: 10, background: "#fafafa" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <strong>{laneLabel(lane)}</strong>
        <span style={{ color: lane.cutoff ? "#92400e" : "#64748b", fontSize: 12 }}>
          {lane.cutoff ? "cut off" : state?.values?.current_stage ?? "pending"}
        </span>
      </div>
      {html ? (
        <div style={{ width: ARENA_WIDTH, height: baseH * scale, overflow: "hidden", background: "white" }}>
          <div style={{ transform: `scale(${scale})`, transformOrigin: "top left", width: baseW, height: baseH }}>
            <iframe
              title={`${lane.lane_id}-slide-${slideIdx}`}
              srcDoc={html}
              sandbox="allow-same-origin"
              style={{ width: baseW, height: baseH, border: "1px solid #111", background: "white" }}
            />
          </div>
        </div>
      ) : (
        <div style={{ height: 210, display: "grid", placeItems: "center", color: "#64748b", background: "#fff", border: "1px solid #e5e5e5" }}>
          Slide {slideIdx + 1} not rendered
        </div>
      )}
    </div>
  );
}
