import { useMemo, useState } from "react";
import { normalizeImagePlaceholders } from "./imagePlaceholders";
import { Markdown } from "./Markdown";
import type {
  DeckState,
  VisualPlaygroundCandidate,
  VisualPlaygroundGenerateBody,
  VisualPlaygroundStatus,
} from "./api";

const MAX_CANDIDATES = 5;
const DEFAULT_CANDIDATES = 3;
const CANVAS: Record<string, [number, number]> = {
  "16:9": [960, 540],
  "4:3": [960, 720],
  "21:9": [960, 411],
};

type Props = {
  deck: DeckState;
  busy: boolean;
  onGenerate: (body: VisualPlaygroundGenerateBody) => Promise<void>;
  onSelect: (candidateId: string) => Promise<void>;
  onContinue?: (destination: "layout" | "playground") => Promise<void>;
};

function candidatesFromDeck(deck: DeckState): VisualPlaygroundCandidate[] {
  const raw = deck.values?.visual_playground_candidates;
  return Array.isArray(raw) ? raw : [];
}

function statusFromDeck(deck: DeckState): VisualPlaygroundStatus {
  const raw = deck.values?.visual_playground_status;
  return raw && typeof raw === "object" ? raw : {};
}

function selectedCandidateId(deck: DeckState): string | null {
  return (
    (deck.values?.visual_playground_selected_candidate_id as string | null | undefined) ??
    (statusFromDeck(deck).selected_candidate_id as string | null | undefined) ??
    null
  );
}

function hasVisualPlaygroundSource(deck: DeckState): boolean {
  return Boolean(
    Array.isArray(deck.values?.outline_slides) &&
      deck.values.outline_slides.length > 0,
  );
}

function swatches(candidate: VisualPlaygroundCandidate): Array<{ name: string; hex: string }> {
  const palette = candidate.visual_style?.palette;
  if (!palette || typeof palette !== "object") return [];
  const names = ["primary", "secondary", "accent", "neutral_dark", "neutral_light", "background"];
  return names
    .map((name) => {
      const value = palette[name];
      return typeof value === "string" && value.startsWith("#")
        ? { name, hex: value }
        : null;
    })
    .filter((item): item is { name: string; hex: string } => Boolean(item));
}

function frameSize(aspectRatio: string, width: number): { width: number; baseW: number; baseH: number; height: number; scale: number } {
  const [baseW, baseH] = CANVAS[aspectRatio] ?? CANVAS["16:9"];
  return { width, baseW, baseH, height: (baseH * width) / baseW, scale: width / baseW };
}

export function VisualPlaygroundPanel({ deck, busy, onGenerate, onSelect, onContinue }: Props) {
  const [candidateCount, setCandidateCount] = useState(DEFAULT_CANDIDATES);
  const [guidance, setGuidance] = useState("");
  const [htmlCriticEnabled, setHtmlCriticEnabled] = useState(true);
  const [acknowledged, setAcknowledged] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const candidates = candidatesFromDeck(deck);
  const status = statusFromDeck(deck);
  const selectedId = selectedCandidateId(deck);
  const sourceReady = hasVisualPlaygroundSource(deck);
  const running = busy && status.state === "running";
  const inVisualPlayground = deck.values?.current_stage === "visual_playground";
  const canContinue = Boolean(onContinue && selectedId && inVisualPlayground);
  const canGenerate = !busy && acknowledged && sourceReady;
  const aspectRatio = (deck.values?.aspect_ratio as string | undefined) ?? "16:9";
  const previewFrame = useMemo(() => frameSize(aspectRatio, 320), [aspectRatio]);

  async function generate() {
    await onGenerate({
      candidate_count: candidateCount,
      guidance: guidance.trim() || null,
      html_critic_enabled: htmlCriticEnabled,
    });
  }

  async function selectCandidate(candidateId: string) {
    setSelectingId(candidateId);
    try {
      await onSelect(candidateId);
    } finally {
      setSelectingId(null);
    }
  }

  return (
    <section
      style={{
        border: "1.5px solid #0a0a0a",
        borderRadius: 0,
        background: "#f5f3ee",
        marginTop: 12,
        padding: 14,
        display: "grid",
        gap: 12,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 16 }}>Visual playground</h3>
          <div style={{ color: "#5c5852", fontSize: 12, marginTop: 4, lineHeight: 1.45 }}>
            Generate sample-slide previews for visual style choices. Creator Playground and Outline Chat stay separate.
          </div>
        </div>
        {status.state && (
          <span
            style={{
              border: "1px solid #0a0a0a",
              borderRadius: 0,
              padding: "5px 9px",
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: 1.2,
              textTransform: "uppercase",
              color: "#0a0a0a",
              background: status.state === "running" ? "#e8e3d8" : "#f5f3ee",
            }}
          >
            {status.state}
          </span>
        )}
      </div>

      <div
        style={{
          border: "1px solid #8a5a14",
          borderRadius: 0,
          background: "#f5f3ee",
          color: "#8a5a14",
          padding: 10,
          fontSize: 13,
          lineHeight: 1.45,
        }}
      >
        <strong>This mode can be very token-consuming.</strong>{" "}
        These previews lock only visual style; final layout and slide composition may change in later stages.
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(130px, 180px) minmax(0, 1fr)",
          gap: 10,
          alignItems: "start",
        }}
      >
        <label
          style={{
            display: "grid",
            gridTemplateRows: "auto 48px",
            gap: 5,
            fontSize: 13,
            color: "#5c5852",
          }}
        >
          Candidates
          <select
            value={candidateCount}
            disabled={busy}
            onChange={(event) => setCandidateCount(Number(event.target.value))}
            style={{
              height: 48,
              boxSizing: "border-box",
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              background: "#f5f3ee",
              padding: "6px 8px",
              color: "#0a0a0a",
            }}
          >
            {Array.from({ length: MAX_CANDIDATES }, (_, idx) => idx + 1).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label
          style={{
            display: "grid",
            gridTemplateRows: "auto 48px",
            gap: 5,
            fontSize: 13,
            color: "#5c5852",
          }}
        >
          Optional guidance
          <input
            value={guidance}
            disabled={busy}
            onChange={(event) => setGuidance(event.target.value)}
            placeholder="e.g. compare editorial luxury against clean product clarity"
            style={{
              height: 48,
              boxSizing: "border-box",
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              background: "#f5f3ee",
              padding: "6px 8px",
              color: "#0a0a0a",
              fontFamily: "inherit",
            }}
          />
        </label>
      </div>

      <label style={{ display: "flex", gap: 8, alignItems: "start", color: "#5c5852", fontSize: 13 }}>
        <input
          type="checkbox"
          checked={htmlCriticEnabled}
          disabled={busy}
          onChange={(event) => setHtmlCriticEnabled(event.target.checked)}
          style={{ marginTop: 2 }}
        />
        <span>
          HTML critic
          <span style={{ display: "block", color: "#948e83", fontSize: 12, lineHeight: 1.35 }}>
            Review preview HTML after validation for stronger composition. Disable for faster, cheaper previews.
          </span>
        </span>
      </label>

      <label style={{ display: "flex", gap: 8, alignItems: "start", color: "#5c5852", fontSize: 13 }}>
        <input
          type="checkbox"
          checked={acknowledged}
          disabled={busy}
          onChange={(event) => setAcknowledged(event.target.checked)}
          style={{ marginTop: 2 }}
        />
        <span>
          I understand this can consume many tokens and only the selected visual style is locked here.
        </span>
      </label>

      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <span style={{ color: "#948e83", fontSize: 12 }}>
          {status.state === "running"
            ? `Generated ${status.completed_candidates ?? 0}/${status.candidate_count ?? candidateCount} candidates`
            : candidates.length > 0
              ? `${candidates.length} candidate${candidates.length === 1 ? "" : "s"} available`
              : sourceReady
                ? "No visual candidates yet"
                : "Outline is not ready"}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button
            type="button"
            className={`osz-button ${canGenerate ? "osz-button-primary" : ""}`}
            disabled={!canGenerate}
            onClick={() => void generate()}
          >
            {running ? "Generating..." : "Generate visual candidates"}
          </button>
          {onContinue && inVisualPlayground && (
            <>
              <button
                type="button"
                className={`osz-button ${canContinue ? "osz-button-primary" : ""}`}
                disabled={busy || !canContinue}
                onClick={() => void onContinue("layout")}
              >
                Continue to layout
              </button>
              <button
                type="button"
                className="osz-button"
                disabled={busy || !canContinue}
                onClick={() => void onContinue("playground")}
              >
                Open Creator Playground
              </button>
            </>
          )}
        </div>
      </div>

      {candidates.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 360px), 1fr))",
            gap: 12,
          }}
        >
          {candidates.map((candidate) => {
            const selected = selectedId === candidate.candidate_id;
            const candidateSwatches = swatches(candidate);
            return (
              <article
                key={candidate.candidate_id}
                style={{
                  border: selected ? "2px solid #0a0a0a" : "1.5px solid #0a0a0a",
                  borderRadius: 0,
                  background: selected ? "#e8e3d8" : "#f5f3ee",
                  padding: 10,
                  display: "grid",
                  gap: 10,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "start" }}>
                  <div style={{ minWidth: 0 }}>
                    <h4 style={{ margin: 0, fontSize: 15 }}>{candidate.label}</h4>
                    {candidate.rationale && (
                      <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.4, marginTop: 4 }}>
                        {candidate.rationale}
                      </div>
                    )}
                  </div>
                  <button
                    type="button"
                    className={`osz-button ${!busy && !selected && selectingId !== candidate.candidate_id ? "osz-button-primary" : ""}`}
                    disabled={busy || selected || selectingId === candidate.candidate_id}
                    onClick={() => void selectCandidate(candidate.candidate_id)}
                  >
                    {selected
                      ? "Style selected"
                      : selectingId === candidate.candidate_id
                        ? "Selecting..."
                        : "Use this style"}
                  </button>
                </div>

                {candidateSwatches.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {candidateSwatches.map(({ name, hex }) => (
                      <span
                        key={name}
                        title={`${name}: ${hex}`}
                        style={{
                          width: 28,
                          height: 28,
                          border: "1px solid #0a0a0a",
                          borderRadius: 0,
                          background: hex,
                          display: "inline-block",
                        }}
                      />
                    ))}
                  </div>
                )}

                {candidate.error && (
                  <div style={{ color: "#8b1a1a", fontSize: 12, lineHeight: 1.4 }}>
                    {candidate.error}
                  </div>
                )}

                <div style={{ display: "grid", gap: 8 }}>
                  {candidate.preview_slides.map((slide) => (
                    <div
                      key={`${candidate.candidate_id}-${slide.slide_idx}`}
                      style={{
                        border: "1px solid #0a0a0a",
                        borderRadius: 0,
                        background: "#f5f3ee",
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 8,
                          padding: "6px 8px",
                          borderBottom: "1px solid #0a0a0a",
                          fontSize: 11,
                          color: "#5c5852",
                        }}
                      >
                        <span>Slide {slide.slide_idx + 1}: {slide.title ?? "Preview"}</span>
                        {slide.pattern && <code>{slide.pattern}</code>}
                      </div>
                      {slide.html ? (
                        <div
                          style={{
                            width: previewFrame.width,
                            maxWidth: "100%",
                            height: previewFrame.height,
                            overflow: "hidden",
                            background: "white",
                          }}
                        >
                          <div
                            style={{
                              width: previewFrame.baseW,
                              height: previewFrame.baseH,
                              transform: `scale(${previewFrame.scale})`,
                              transformOrigin: "top left",
                            }}
                          >
                            <iframe
                              title={`${candidate.candidate_id}-slide-${slide.slide_idx}`}
                              srcDoc={normalizeImagePlaceholders(slide.html)}
                              sandbox="allow-same-origin"
                              style={{
                                width: previewFrame.baseW,
                                height: previewFrame.baseH,
                                border: 0,
                                display: "block",
                                background: "white",
                              }}
                            />
                          </div>
                        </div>
                      ) : (
                        <div style={{ padding: 12, color: "#8b1a1a", fontSize: 12 }}>
                          {slide.error || "Preview did not render."}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <details>
                  <summary style={{ cursor: "pointer", fontSize: 12, color: "#5c5852" }}>
                    Style spec
                  </summary>
                  <div style={{ marginTop: 8, fontSize: 12, background: "#e8e3d8", padding: 8 }}>
                    <Markdown>{candidate.visual_style_md}</Markdown>
                  </div>
                </details>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
