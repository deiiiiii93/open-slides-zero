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
  type CreatePlaygroundLaneBody,
  type DeckState,
  type PlaygroundModelOptions,
  type PlaygroundLane,
  type ThinkingEffort,
} from "./api";
import {
  exportHtmlSingle,
  exportHtmlZip,
  exportPngZip,
  exportPlaygroundLanesPackage,
  exportPptx,
  hasExportableSlides,
} from "./exporter";
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
const MODEL_STAGE_ORDER = ["style", "layout", "html"] as const;
type ModelStage = (typeof MODEL_STAGE_ORDER)[number];

function useElementWidth(fallback: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const nextWidth = entry.contentRect.width;
      if (nextWidth > 0) setWidth(nextWidth);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, width] as const;
}

function firstInterrupt(state: DeckState | null): any {
  const i = state?.interrupts?.[0];
  if (!i) return null;
  return typeof i === "object" && "value" in i ? (i as any).value : i;
}

function firstTaskError(state: DeckState | null): string | null {
  const task = state?.tasks?.find((item) => item.error);
  if (!task?.error) return null;
  return task.name ? `${task.name}: ${task.error}` : task.error;
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

function selectedModelOverrides(
  overrides: Partial<Record<ModelStage, string>>,
): CreatePlaygroundLaneBody["model_overrides"] | undefined {
  const entries = MODEL_STAGE_ORDER
    .map((stage) => [stage, overrides[stage]?.trim()] as const)
    .filter((entry): entry is readonly [ModelStage, string] => Boolean(entry[1]));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function selectedThinkingEffortOverrides(
  overrides: Partial<Record<ModelStage, ThinkingEffort>>,
): CreatePlaygroundLaneBody["thinking_effort_overrides"] | undefined {
  const entries = MODEL_STAGE_ORDER
    .map((stage) => [stage, overrides[stage]] as const)
    .filter((entry): entry is readonly [ModelStage, ThinkingEffort] => Boolean(entry[1]));
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function modelLabel(modelOptions: PlaygroundModelOptions | null, modelId: string): string {
  for (const stage of MODEL_STAGE_ORDER) {
    const found = modelOptions?.stages[stage]?.options.find((option) => option.id === modelId);
    if (found) return found.label;
  }
  return modelId;
}

function thinkingEffortLabel(
  modelOptions: PlaygroundModelOptions | null,
  effort: string,
): string {
  return modelOptions?.thinking_efforts?.options.find((option) => option.id === effort)?.label ?? effort;
}

function readJsonLocalStorage<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJsonLocalStorage(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota or privacy mode — silent */
  }
}

export function PlaygroundPanel({ deck, catalog }: Props) {
  const [lanes, setLanes] = useState<PlaygroundLane[]>([]);
  const [maxLanes, setMaxLanes] = useState(5);
  const [activeLaneId, setActiveLaneId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [modelOptions, setModelOptions] = useState<PlaygroundModelOptions | null>(null);
  const modelOverridesKey = `osz.playground.${deck.thread_id}.modelOverrides`;
  const effortOverridesKey = `osz.playground.${deck.thread_id}.effortOverrides`;
  const [modelOverrides, setModelOverrides] = useState<Partial<Record<ModelStage, string>>>(
    () => readJsonLocalStorage(modelOverridesKey, {} as Partial<Record<ModelStage, string>>),
  );
  const [thinkingEffortOverrides, setThinkingEffortOverrides] = useState<
    Partial<Record<ModelStage, ThinkingEffort>>
  >(
    () => readJsonLocalStorage(effortOverridesKey, {} as Partial<Record<ModelStage, ThinkingEffort>>),
  );

  useEffect(() => {
    writeJsonLocalStorage(modelOverridesKey, modelOverrides);
  }, [modelOverrides, modelOverridesKey]);

  useEffect(() => {
    writeJsonLocalStorage(effortOverridesKey, thinkingEffortOverrides);
  }, [thinkingEffortOverrides, effortOverridesKey]);
  const [creatingLane, setCreatingLane] = useState(false);
  const [deletingLaneId, setDeletingLaneId] = useState<string | null>(null);
  const [exporting, setExporting] = useState<string | null>(null);
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
    setActiveLaneId((current) =>
      current && result.lanes.some((lane) => lane.lane_id === current)
        ? current
        : result.lanes[0]?.lane_id ?? null,
    );
  }, [deck.thread_id]);

  useEffect(() => {
    void refreshLanes().catch((e) => setErr(String(e)));
  }, [refreshLanes]);

  useEffect(() => {
    void api.listPlaygroundModelOptions()
      .then(setModelOptions)
      .catch((e) => setErr(String(e)));
  }, []);

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
  const hasExportableLane = useMemo(() => lanes.some((lane) => hasExportableSlides(lane.state)), [lanes]);

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
        const stateError = firstTaskError(ev.state as DeckState | null);
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
                error: stateError ?? live.error,
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
    const body: CreatePlaygroundLaneBody = {
      creator_prompt: creatorPrompt,
      model_overrides: selectedModelOverrides(modelOverrides),
      thinking_effort_overrides: selectedThinkingEffortOverrides(thinkingEffortOverrides),
    };
    await consumeLaneStream(api.createPlaygroundLaneStreamUrl(deck.thread_id), body);
    setPrompt("");
    setModelOverrides({});
    setThinkingEffortOverrides({});
  }

  async function resumeLane(lane: PlaygroundLane, payload: Record<string, unknown>) {
    await consumeLaneStream(
      `${STREAM_BASE}/decks/${lane.lane_thread_id}/resume/stream`,
      { payload },
      lane,
    );
  }

  async function deleteLane(lane: PlaygroundLane) {
    if (deletingLaneId) return;
    const confirmed = window.confirm(
      `Delete ${laneLabel(lane)}? This permanently removes its checkpoints and generated files.`,
    );
    if (!confirmed) return;

    setErr(null);
    setDeletingLaneId(lane.lane_id);
    try {
      abortByLaneRef.current[lane.lane_id]?.abort();
      await api.deletePlaygroundLane(deck.thread_id, lane.lane_id);
      setLiveByLane((prev) => {
        const { [lane.lane_id]: _removed, ...rest } = prev;
        void _removed;
        return rest;
      });
      const { [lane.lane_id]: _removedTagStarts, ...tagStartRest } = tagStartByLaneRef.current;
      void _removedTagStarts;
      tagStartByLaneRef.current = tagStartRest;
      const { [lane.lane_id]: _removedAbort, ...abortRest } = abortByLaneRef.current;
      void _removedAbort;
      abortByLaneRef.current = abortRest;
      await refreshLanes();
    } catch (e) {
      setErr(String(e));
    } finally {
      setDeletingLaneId(null);
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

  async function runLaneExport(
    lane: PlaygroundLane,
    label: string,
    fn: (state: DeckState) => Promise<void>,
  ) {
    if (!lane.state) return;
    setErr(null);
    setExporting(`${laneLabel(lane)} ${label}`);
    try {
      await fn(lane.state);
    } catch (e) {
      setErr(`Export failed: ${String(e)}`);
    } finally {
      setExporting(null);
    }
  }

  async function downloadAllLanes() {
    setErr(null);
    setExporting("all lanes");
    try {
      await exportPlaygroundLanesPackage(deck, lanes);
    } catch (e) {
      setErr(`Export failed: ${String(e)}`);
    } finally {
      setExporting(null);
    }
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
            border: "1.5px solid #0a0a0a",
            borderRadius: 0,
            background: "#f5f3ee",
            marginBottom: 12,
          }}
        >
          <div>
            <h3 style={{ margin: 0 }}>Creator playground</h3>
            <div style={{ color: "#948e83", fontSize: 13, marginTop: 3 }}>
              {lanes.length}/{maxLanes} lanes
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
            <button
              disabled={!hasExportableLane || exporting !== null}
              onClick={() => void downloadAllLanes()}
            >
              {exporting === "all lanes" ? "Packaging..." : "Download all lanes"}
            </button>
            <button
              onClick={() => setView("lanes")}
              style={{
                padding: "8px 16px",
                border: view === "lanes" ? "1.5px solid #0a0a0a" : "1px solid #0a0a0a",
                borderRadius: 0,
                background: view === "lanes" ? "#0a0a0a" : "#f5f3ee",
                color: view === "lanes" ? "#f5f3ee" : "#0a0a0a",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: 1.4,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              Lanes
            </button>
            <button
              onClick={() => setView("arena")}
              style={{
                padding: "8px 16px",
                border: view === "arena" ? "1.5px solid #0a0a0a" : "1px solid #0a0a0a",
                borderRadius: 0,
                background: view === "arena" ? "#0a0a0a" : "#f5f3ee",
                color: view === "arena" ? "#f5f3ee" : "#0a0a0a",
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: 1.4,
                textTransform: "uppercase",
                cursor: "pointer",
              }}
            >
              Arena
            </button>
          </div>
        </div>

        {err && (
          <div style={{ color: "#8b1a1a", padding: 8, border: "1px solid #8b1a1a", marginBottom: 8 }}>
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
                border: "1.5px solid #0a0a0a",
                borderRadius: 0,
                padding: 12,
                background: "#f5f3ee",
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
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
                  gap: 10,
                  marginTop: 8,
                }}
              >
                {MODEL_STAGE_ORDER.map((stage) => {
                  const stageOptions = modelOptions?.stages[stage];
                  const effortValue = thinkingEffortOverrides[stage] ?? "";
                  return (
                    <div
                      key={stage}
                      style={{ display: "grid", gap: 6, color: "#5c5852", fontSize: 12 }}
                    >
                      <label style={{ display: "grid", gap: 4 }}>
                        <span>{stageOptions?.label ?? stage} model</span>
                        <select
                          value={modelOverrides[stage] ?? ""}
                          disabled={!stageOptions}
                          onChange={(e) =>
                            setModelOverrides((prev) => ({
                              ...prev,
                              [stage]: e.target.value || undefined,
                            }))
                          }
                          style={{
                            width: "100%",
                            minHeight: 34,
                            border: "1.5px solid #0a0a0a",
                            borderRadius: 0,
                            padding: "6px 8px",
                            background: "#f5f3ee",
                          }}
                        >
                          <option value="">Default routing</option>
                          {stageOptions?.options.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label style={{ display: "grid", gap: 4 }}>
                        <span>{stageOptions?.label ?? stage} thinking effort</span>
                        <select
                          value={effortValue}
                          disabled={!modelOptions}
                          onChange={(e) => {
                            const value = e.target.value as ThinkingEffort | "";
                            setThinkingEffortOverrides((prev) => ({
                              ...prev,
                              [stage]: value || undefined,
                            }));
                          }}
                          style={{
                            width: "100%",
                            minHeight: 34,
                            border: "1.5px solid #0a0a0a",
                            borderRadius: 0,
                            padding: "6px 8px",
                            background: "#f5f3ee",
                          }}
                        >
                          <option value="">Provider default</option>
                          {modelOptions?.thinking_efforts?.options.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  );
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
                <span style={{ color: "#948e83", fontSize: 12 }}>
                  Blank lanes are allowed as a baseline.
                </span>
                <button
                  disabled={creatingLane || deletingLaneId !== null || lanes.length >= maxLanes}
                  onClick={() => void createLane(prompt)}
                >
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
                      border: lane.lane_id === activeLane?.lane_id ? "1.5px solid #0a0a0a" : "1px solid #0a0a0a",
                      borderRadius: 0,
                      background: lane.lane_id === activeLane?.lane_id ? "#e8e3d8" : "#f5f3ee",
                      color: "#0a0a0a",
                      padding: 14,
                      cursor: "pointer",
                    }}
                  >
                    {laneLabel(lane)}
                  </button>
                ))}
              </div>
            )}

            {activeLane ? (
              <LaneDetail
                lane={activeLane}
                catalog={catalog}
                modelOptions={modelOptions}
                currentSlide={currentSlide}
                setCurrentSlide={setCurrentSlide}
                busy={Boolean(liveByLane[activeLane.lane_id]?.isRunning)}
                deleting={deletingLaneId === activeLane.lane_id}
                exporting={exporting}
                onResume={resumeLane}
                onComment={commentOnLane}
                onDelete={deleteLane}
                onSaveMasterpiece={saveMasterpiece}
                onExport={runLaneExport}
              />
            ) : (
              <div style={{ padding: 16, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
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
  modelOptions,
  currentSlide,
  setCurrentSlide,
  busy,
  deleting,
  exporting,
  onResume,
  onComment,
  onDelete,
  onSaveMasterpiece,
  onExport,
}: {
  lane: PlaygroundLane;
  catalog: CatalogResponse | null;
  modelOptions: PlaygroundModelOptions | null;
  currentSlide: number;
  setCurrentSlide: (idx: number) => void;
  busy: boolean;
  deleting: boolean;
  exporting: string | null;
  onResume: (lane: PlaygroundLane, payload: Record<string, unknown>) => Promise<void>;
  onComment: (
    lane: PlaygroundLane,
    slideIdx: number,
    text: string,
    box: { x: number; y: number; w: number; h: number },
  ) => Promise<void>;
  onDelete: (lane: PlaygroundLane) => Promise<void>;
  onSaveMasterpiece: (lane: PlaygroundLane) => Promise<void>;
  onExport: (lane: PlaygroundLane, label: string, fn: (state: DeckState) => Promise<void>) => Promise<void>;
}) {
  const [showExport, setShowExport] = useState(false);
  const state = lane.state;
  const gate = firstInterrupt(state) as any;
  const stage = (state?.values?.current_stage as string | undefined) ?? "pending";
  const slides = (state?.values?.html_slides as Record<number, string>) ?? {};
  const slideOrder = expectedSlideOrder(state);
  const taskError = firstTaskError(state);
  const hasSlides = slideOrder.length > 0;
  const canExport = hasExportableSlides(state);
  const laneModelOverrides =
    (state?.values?.lane_model_overrides as Partial<Record<ModelStage, string>> | null | undefined) ?? null;
  const laneThinkingEffortOverrides =
    (state?.values?.lane_thinking_effort_overrides as
      | Partial<Record<ModelStage, ThinkingEffort>>
      | null
      | undefined) ?? null;
  const modelOverrideEntries = MODEL_STAGE_ORDER
    .map((stage) => {
      const modelId = laneModelOverrides?.[stage];
      return modelId ? { stage, modelId } : null;
    })
    .filter((entry): entry is { stage: ModelStage; modelId: string } => Boolean(entry));
  const thinkingEffortEntries = MODEL_STAGE_ORDER
    .map((stage) => {
      const effort = laneThinkingEffortOverrides?.[stage];
      return effort ? { stage, effort } : null;
    })
    .filter((entry): entry is { stage: ModelStage; effort: ThinkingEffort } => Boolean(entry));
  const aspectRatio = (state?.values?.aspect_ratio as keyof typeof CANVAS | undefined) ?? "16:9";
  const [, baseH] = CANVAS[aspectRatio] ?? CANVAS["16:9"];
  const overlayHeight = (baseH * LANE_CANVAS_WIDTH) / (CANVAS[aspectRatio]?.[0] ?? CANVAS["16:9"][0]);
  const canComment = hasSlides && stage === "ready" && !busy;
  const canContinue = Boolean(state && !gate && stage !== "ready" && state.next.length > 0);
  const continueDisabled = busy && !taskError;

  return (
    <div style={{ border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee", padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0 }}>{laneLabel(lane)}</h3>
          <div style={{ color: "#948e83", fontSize: 12, marginTop: 4 }}>
            stage: <code>{stage}</code>
          </div>
          {modelOverrideEntries.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {modelOverrideEntries.map(({ stage, modelId }) => (
                <span
                  key={stage}
                  style={{
                    border: "1px solid #0a0a0a",
                    borderRadius: 0,
                    background: "#e8e3d8",
                    color: "#1c1c1e",
                    padding: "8px 12px",
                    fontSize: 12,
                    lineHeight: 1.55,
                  }}
                >
                  <div
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      letterSpacing: 1.4,
                      textTransform: "uppercase",
                      color: "#5c5852",
                      marginBottom: 4,
                    }}
                  >
                    Suggestion
                  </div>
                  {modelOptions?.stages[stage]?.label ?? stage}: {modelLabel(modelOptions, modelId)}
                </span>
              ))}
            </div>
          )}
          {thinkingEffortEntries.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {thinkingEffortEntries.map(({ stage, effort }) => (
                <span
                  key={stage}
                  style={{
                    border: "1px solid #0a0a0a",
                    borderRadius: 0,
                    background: "#e8e3d8",
                    color: "#1c1c1e",
                    padding: "8px 12px",
                    fontSize: 12,
                    lineHeight: 1.55,
                  }}
                >
                  <div
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      letterSpacing: 1.4,
                      textTransform: "uppercase",
                      color: "#5c5852",
                      marginBottom: 4,
                    }}
                  >
                    Refinement
                  </div>
                  {modelOptions?.stages[stage]?.label ?? stage} effort:{" "}
                  {thinkingEffortLabel(modelOptions, effort)}
                </span>
              ))}
            </div>
          )}
          {lane.creator_prompt && (
            <div style={{ color: "#1c1c1e", fontSize: 13, marginTop: 8, whiteSpace: "pre-wrap" }}>
              {lane.creator_prompt}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <div style={{ position: "relative" }}>
            <button
              disabled={!canExport || exporting !== null}
              onClick={() => setShowExport((open) => !open)}
            >
              {exporting?.startsWith(laneLabel(lane)) ? "Exporting..." : "Export"}
            </button>
            {showExport && canExport && exporting === null && (
              <div
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  right: 0,
                  minWidth: 190,
                  background: "#f5f3ee",
                  border: "1.5px solid #0a0a0a",
                  borderRadius: 0,
                  boxShadow: "none",
                  zIndex: 100,
                  padding: "4px 0",
                }}
              >
                {[
                  { key: "html", label: "HTML (single file)", fn: exportHtmlSingle },
                  { key: "zip", label: "HTML (zip of slides)", fn: exportHtmlZip },
                  { key: "pngs", label: "PNGs (zip of slides)", fn: exportPngZip },
                  { key: "pptx", label: "PPTX (editable)", fn: exportPptx },
                ].map((opt) => (
                  <button
                    key={opt.key}
                    onClick={() => {
                      setShowExport(false);
                      void onExport(lane, opt.key, opt.fn);
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
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#e8e3d8")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "#f5f3ee")}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            disabled={busy || deleting || exporting !== null}
            onClick={() => void onDelete(lane)}
          >
            {deleting ? "Deleting..." : "Delete lane"}
          </button>
          <button
            disabled={busy || !lane.creator_prompt.trim()}
            onClick={() => void onSaveMasterpiece(lane)}
          >
            Save masterpiece
          </button>
        </div>
      </div>

      {!state && <div style={{ color: "#948e83" }}>Lane state is loading.</div>}

      {gate?.gate === "style" && (
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

      {gate?.gate === "layout" && (
        <LayoutStage
          catalog={catalog}
          layouts={gate.layouts}
          selectedVisualStylePresetId={gate.visual_style_preset_id}
          title="Lane layouts"
          submitLabel="Approve layouts"
          onSubmit={async ({ overrides, visual_style_preset_id }) =>
            onResume(lane, { approved: true, overrides, visual_style_preset_id })
          }
        />
      )}

      {gate?.gate === "html" && (
        <HtmlStage
          title="Retry lane HTML"
          submitLabel="Retry failed slides"
          failedSlides={gate.failed_slides}
          renderedCount={gate.rendered_count}
          expectedCount={gate.expected_count}
          onSubmit={async () => onResume(lane, { retry_failed: true })}
        />
      )}

      {taskError && (
        <div style={{ color: "#8b1a1a", border: "1px solid #8b1a1a", borderRadius: 0, padding: 8, marginBottom: 12 }}>
          {taskError}
        </div>
      )}

      {state && !gate && stage !== "ready" && (
        <div style={{ color: "#948e83", display: "flex", alignItems: "center", gap: 10 }}>
          <span>
            {taskError
              ? "Lane stopped before the next step."
              : state.recovery_hint === "crashed"
                ? "Lane was interrupted unexpectedly. Resume from the last checkpoint?"
                : "Lane generation is between steps."}
          </span>
          {canContinue && (
            <button disabled={continueDisabled} onClick={() => void onResume(lane, {})}>
              {taskError
                ? "Retry lane"
                : state.recovery_hint === "crashed"
                  ? "Resume from last checkpoint"
                  : "Continue lane"}
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
      <div style={{ padding: 16, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
        Create lanes before opening the arena.
      </div>
    );
  }

  return (
    <div style={{ border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee", padding: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {slideOrder.map((idx) => (
          <button
            key={idx}
            onClick={() => onSelectSlide(idx)}
            style={{
              padding: "8px 12px",
              border: idx === currentSlide ? "1.5px solid #0a0a0a" : "1px solid #0a0a0a",
              borderRadius: 0,
              background: idx === currentSlide ? "#e8e3d8" : "#f5f3ee",
              color: "#0a0a0a",
              cursor: "pointer",
            }}
          >
            Slide {idx + 1}
          </button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 360px), 1fr))", gap: 12 }}>
        {lanes.map((lane) => (
          <ArenaLane key={lane.lane_id} lane={lane} slideIdx={currentSlide} />
        ))}
      </div>
    </div>
  );
}

function ArenaLane({ lane, slideIdx }: { lane: PlaygroundLane; slideIdx: number }) {
  const [cardRef, cardWidth] = useElementWidth(ARENA_WIDTH);
  const state = lane.state;
  const slides = (state?.values?.html_slides as Record<number, string>) ?? {};
  const html = slides[slideIdx] ? normalizeImagePlaceholders(slides[slideIdx]) : undefined;
  const aspect = (state?.values?.aspect_ratio as string | undefined) ?? "16:9";
  const [baseW, baseH] = CANVAS[aspect] ?? CANVAS["16:9"];
  const frameBorder = 1;
  const frameWidth = Math.max(0, cardWidth);
  const innerWidth = Math.max(0, frameWidth - frameBorder * 2);
  const scale = innerWidth / baseW;
  const frameHeight = baseH * scale + frameBorder * 2;

  return (
    <div ref={cardRef} style={{ border: "1.5px solid #0a0a0a", borderRadius: 0, padding: 10, background: "#f5f3ee", minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <strong>{laneLabel(lane)}</strong>
        <span style={{ color: "#948e83", fontSize: 12 }}>
          {state?.values?.current_stage ?? "pending"}
        </span>
      </div>
      {html ? (
        <div
          style={{
            width: "100%",
            height: frameHeight,
            overflow: "hidden",
            background: "white",
            border: `${frameBorder}px solid #0a0a0a`,
            boxSizing: "border-box",
          }}
        >
          <div style={{ transform: `scale(${scale})`, transformOrigin: "top left", width: baseW, height: baseH }}>
            <iframe
              title={`${lane.lane_id}-slide-${slideIdx}`}
              srcDoc={html}
              sandbox="allow-same-origin"
              style={{ width: baseW, height: baseH, border: 0, display: "block", background: "white" }}
            />
          </div>
        </div>
      ) : (
        <div style={{ height: 210, display: "grid", placeItems: "center", color: "#948e83", background: "#f5f3ee", border: "1px solid #0a0a0a" }}>
          Slide {slideIdx + 1} not rendered
        </div>
      )}
    </div>
  );
}
