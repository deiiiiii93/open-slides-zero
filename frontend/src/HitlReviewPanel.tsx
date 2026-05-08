// Shared step panels for both live HITL interrupts and ready-time review.

import { useEffect, useMemo, useState } from "react";
import type { CatalogResponse, DeckState } from "./api";
import { LayoutWireframe } from "./LayoutWireframe";
import { layoutPatternInfo } from "./layoutPatternMetadata";
import { Markdown } from "./Markdown";

type StructureSubmit = { scenario_id: string; structure_id: string };
type StyleSubmit = { feedback: string };
type LayoutSubmit = { overrides: Record<number, string>; visual_style_preset_id?: string | null };
type HtmlRetrySubmit = { retry_failed: true };
type OutlineSubmit = { approved?: true; visual_playground?: true; revise?: string };

type StructureStageProps = {
  catalog: CatalogResponse | null;
  scenarioId?: string;
  structureId?: string;
  candidates?: string[];
  title: string;
  submitLabel: string;
  onSubmit: (payload: StructureSubmit) => Promise<void>;
};

type StyleStageProps = {
  visualStyleMd?: string;
  visualStyle?: Record<string, any>;
  title: string;
  submitLabel: string;
  onSubmit: (payload: StyleSubmit) => Promise<void>;
  approveLabel?: string;
  onApprove?: () => Promise<void>;
  playgroundLabel?: string;
  onPlayground?: () => Promise<void>;
};

type LayoutStageProps = {
  catalog: CatalogResponse | null;
  layouts?: Array<Record<string, any>>;
  title: string;
  submitLabel: string;
  onSubmit: (payload: LayoutSubmit) => Promise<void>;
  submitDisabledWhenUnchanged?: boolean;
  selectedVisualStylePresetId?: string | null;
};

type HtmlStageProps = {
  failedSlides?: Array<Record<string, any>>;
  renderedCount?: number;
  expectedCount?: number;
  title: string;
  submitLabel: string;
  onSubmit: (payload: HtmlRetrySubmit) => Promise<void>;
};

type OutlineStageProps = {
  outlineMd?: string;
  title: string;
  onSubmit: (payload: OutlineSubmit) => Promise<void>;
};

function firstInterrupt(deck: DeckState): any {
  const i = deck.interrupts?.[0];
  if (!i) return null;
  return typeof i === "object" && "value" in i ? (i as any).value : i;
}

export function HitlReviewPanel({ deck, catalog, onResume }: {
  deck: DeckState;
  catalog: CatalogResponse | null;
  onResume: (payload: Record<string, unknown>) => Promise<void>;
}) {
  const payload = firstInterrupt(deck);
  if (!payload) return null;

  const gate = payload.gate as "structure" | "outline" | "style" | "layout" | "html";
  if (gate === "structure") {
    return (
      <StructureStage
        catalog={catalog}
        scenarioId={payload.scenario_id}
        structureId={payload.candidates?.[0]}
        candidates={payload.candidates}
        title="① Pick scenario + narrative structure"
        submitLabel="Continue"
        onSubmit={onResume}
      />
    );
  }
  if (gate === "style") {
    return (
      <StyleStage
        title="③ Review visual style"
        submitLabel="Revise"
        approveLabel="Approve & continue"
        playgroundLabel="Open Creator Playground"
        visualStyleMd={(deck.values?.visual_style_md as string | undefined) ?? payload.visual_style_md}
        visualStyle={(deck.values?.visual_style as Record<string, any> | undefined) ?? payload.visual_style}
        onSubmit={async ({ feedback }) => onResume({ revise: feedback })}
        onApprove={async () => onResume({ approved: true })}
        onPlayground={async () => onResume({ playground: true })}
      />
    );
  }
  if (gate === "outline") {
    return (
      <OutlineStage
        title="② Review outline"
        outlineMd={payload.outline_md}
        onSubmit={onResume}
      />
    );
  }
  if (gate === "layout") {
    return (
      <LayoutStage
        catalog={catalog}
        layouts={payload.layouts}
        selectedVisualStylePresetId={payload.visual_style_preset_id}
        title="④ Review layouts (scores shown)"
        submitLabel="Approve & render HTML"
        onSubmit={async ({ overrides, visual_style_preset_id }) =>
          onResume({ approved: true, overrides, visual_style_preset_id })
        }
      />
    );
  }
  if (gate === "html") {
    return (
      <HtmlStage
        title="⑤ Retry failed HTML slides"
        submitLabel="Retry failed slides"
        failedSlides={payload.failed_slides}
        renderedCount={payload.rendered_count}
        expectedCount={payload.expected_count}
        onSubmit={async (_payload) => onResume({ retry_failed: true })}
      />
    );
  }
  return <pre>{JSON.stringify(payload, null, 2)}</pre>;
}

export function OutlineStage({ outlineMd, title, onSubmit }: OutlineStageProps) {
  const [approved, setApproved] = useState(false);
  const [feedback, setFeedback] = useState("");

  return (
    <div
      style={{
        padding: 24,
        marginBottom: 16,
        border: "1.5px solid #0a0a0a",
        borderRadius: 0,
        background: "#f5f3ee",
      }}
    >
      <h3>{title}</h3>
      <div
        style={{
          background: "#e8e3d8",
          padding: 12,
          maxHeight: 460,
          overflow: "auto",
          border: "1.5px solid #0a0a0a",
          borderRadius: 0,
        }}
      >
        <Markdown>{outlineMd ?? ""}</Markdown>
      </div>
      {!approved ? (
        <>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            rows={3}
            placeholder="Outline revision comments"
            style={{
              width: "100%",
              boxSizing: "border-box",
              marginTop: 12,
              padding: 8,
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              background: "#f5f3ee",
              fontFamily: "inherit",
            }}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            <button
              className="osz-button"
              disabled={!feedback.trim()}
              onClick={() => onSubmit({ revise: feedback.trim() })}
            >
              Regenerate outline
            </button>
            <button className="osz-button osz-button-primary" onClick={() => setApproved(true)}>
              Approve outline
            </button>
          </div>
        </>
      ) : (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            border: "1.5px solid #0a0a0a",
            borderRadius: 0,
            background: "#e8e3d8",
            display: "grid",
            gap: 10,
          }}
        >
          <div style={{ fontWeight: 700 }}>Choose visual style path</div>
          <div style={{ color: "#5c5852", fontSize: 13 }}>
            Visual Playground generates sample-slide previews before style lock. Legacy style uses the normal visual style generator.
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className="osz-button osz-button-primary"
              onClick={() => onSubmit({ visual_playground: true })}
            >
              Use Visual Playground
            </button>
            <button className="osz-button" onClick={() => onSubmit({ approved: true })}>
              Continue with legacy visual style
            </button>
            <button className="osz-button" onClick={() => setApproved(false)}>
              Back to outline review
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function StructureStage({
  catalog,
  scenarioId,
  structureId,
  candidates,
  title,
  submitLabel,
  onSubmit,
}: StructureStageProps) {
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>(scenarioId ?? "");
  const [selectedStructureId, setSelectedStructureId] = useState<string>(structureId ?? "");

  const structuresById = useMemo(
    () => Object.fromEntries((catalog?.structures ?? []).map((s) => [s.id, s])),
    [catalog],
  );

  const allowedStructures =
    catalog?.scenarios.find((s) => s.id === selectedScenarioId)?.structures ?? candidates ?? [];

  return (
    <div style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
      <h3>{title}</h3>
      <label>
        Scenario:{" "}
        <select value={selectedScenarioId} onChange={(e) => setSelectedScenarioId(e.target.value)}>
          <option value="">—</option>
          {(catalog?.scenarios ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name_en} / {s.name_zh}
            </option>
          ))}
        </select>
      </label>
      <div style={{ marginTop: 12 }}>
        <strong>Structure:</strong>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8, marginTop: 8 }}>
          {allowedStructures.map((sid: string) => {
            const s = structuresById[sid];
            return (
              <button
                key={sid}
                onClick={() => setSelectedStructureId(sid)}
                style={{
                  border: sid === selectedStructureId ? "2px solid #0a0a0a" : "1px solid #948e83",
                  borderRadius: 0,
                  background: sid === selectedStructureId ? "#e8e3d8" : "#f5f3ee",
                  padding: 10,
                  textAlign: "left",
                  cursor: "pointer",
                }}
              >
                <div style={{ fontWeight: 600 }}>{s?.name_en ?? sid}</div>
                <div style={{ fontSize: 12, color: "#5c5852" }}>{s?.description_en}</div>
              </button>
            );
          })}
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <button
          disabled={!selectedScenarioId || !selectedStructureId}
          onClick={() =>
            onSubmit({
              scenario_id: selectedScenarioId,
              structure_id: selectedStructureId,
            })
          }
        >
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

function hexSwatches(palette: Record<string, any> | undefined): { name: string; hex: string }[] {
  if (!palette) return [];
  const ordered = ["primary", "secondary", "accent", "neutral_dark", "neutral_light", "background"];
  const out: { name: string; hex: string }[] = [];
  for (const k of ordered) {
    const v = palette[k];
    if (v) out.push({ name: k, hex: v });
  }
  for (const [k, v] of Object.entries(palette)) {
    if (!ordered.includes(k) && k !== "roles" && typeof v === "string" && v.startsWith("#")) {
      out.push({ name: k, hex: v });
    }
  }
  const roles = palette.roles;
  if (roles && typeof roles === "object") {
    for (const [k, v] of Object.entries(roles)) {
      if (typeof v === "string" && v.startsWith("#")) {
        out.push({ name: k, hex: v });
      }
    }
  }
  return out;
}

export function StyleStage({
  visualStyleMd,
  visualStyle,
  title,
  submitLabel,
  onSubmit,
  approveLabel,
  onApprove,
  playgroundLabel,
  onPlayground,
}: StyleStageProps) {
  const [feedback, setFeedback] = useState("");
  const swatches = hexSwatches(visualStyle?.palette);

  return (
    <div style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
      <h3>{title}</h3>
      {swatches.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))",
            gap: 8,
            marginBottom: 12,
          }}
        >
          {swatches.map(({ name, hex }) => (
            <div
              key={name}
              style={{
                border: "1px solid #948e83",
                borderRadius: 0,
                overflow: "hidden",
                background: "#f5f3ee",
              }}
            >
              <div style={{ height: 48, background: hex }} />
              <div style={{ padding: "6px 8px", fontSize: 11, lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600, textTransform: "capitalize" }}>{name}</div>
                <code style={{ color: "#5c5852", fontSize: 10 }}>{hex.toUpperCase()}</code>
              </div>
            </div>
          ))}
        </div>
      )}

      <div
        style={{
          background: "#e8e3d8",
          padding: 12,
          maxHeight: 360,
          overflow: "auto",
          border: "1.5px solid #0a0a0a",
          borderRadius: 0,
        }}
      >
        <Markdown>{visualStyleMd ?? ""}</Markdown>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        {approveLabel && onApprove && (
          <button className="osz-button osz-button-primary" onClick={() => void onApprove()}>
            {approveLabel}
          </button>
        )}
        {playgroundLabel && onPlayground && (
          <button className="osz-button" onClick={() => void onPlayground()}>
            {playgroundLabel}
          </button>
        )}
        <input
          style={{ flex: 1 }}
          placeholder="Revision feedback"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <button
          className="osz-button"
          disabled={!feedback.trim()}
          onClick={() => onSubmit({ feedback: feedback.trim() })}
        >
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

type LayoutPatternEntry = CatalogResponse["patterns"][string];

const COMPACT_PATTERN_LIMIT = 6;

function layoutZones(layout: Record<string, any>): string[] {
  const zones = layout.zones;
  return Array.isArray(zones) ? zones.map((zone) => String(zone)) : [];
}

function patternEntryFor(
  patternId: string,
  patterns: Record<string, LayoutPatternEntry>,
  layout: Record<string, any>,
): LayoutPatternEntry {
  return (
    patterns[patternId] ?? {
      family: String(layout.family ?? "adaptive"),
      kind: "content",
      zones: layoutZones(layout),
    }
  );
}

function preferredKindsForLayout(
  layout: Record<string, any>,
  currentPattern: LayoutPatternEntry,
): string[] {
  const role = String(layout.story_role ?? "").toLowerCase();
  const shape = String(layout.content_shape ?? "").toLowerCase();
  const currentKind = String(currentPattern.kind ?? "content");
  const sequentialHints = ["timeline", "step", "sequence", "flow", "roadmap", "journey"];

  if (role === "cover" || currentKind === "cover") return ["cover"];
  if (role === "closing" || role === "close" || currentKind === "closing") return ["closing"];
  if (
    currentKind === "timeline" ||
    sequentialHints.some((hint) => role.includes(hint) || shape.includes(hint))
  ) {
    return ["timeline", "content"];
  }
  return ["content"];
}

function familyRanksForLayout(layout: Record<string, any>): Map<string, number> {
  const ranks = new Map<string, number>();
  const currentFamily = String(layout.family ?? "");
  if (currentFamily) ranks.set(currentFamily, 0);
  const ranking = (layout.ranking_top3 as Array<{ family?: string }> | undefined) ?? [];
  ranking.forEach((entry, index) => {
    const family = entry.family;
    if (family && !ranks.has(family)) ranks.set(family, index + 1);
  });
  return ranks;
}

function uniquePatternIds(patternIds: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const patternId of patternIds) {
    if (!patternId || seen.has(patternId)) continue;
    out.push(patternId);
    seen.add(patternId);
  }
  return out;
}

function sortedPatternIdsForLayout(
  patternIds: string[],
  patterns: Record<string, LayoutPatternEntry>,
  layout: Record<string, any>,
  currentPatternId: string,
  selectedPatternId: string,
): { compatible: string[]; incompatible: string[]; preferredKinds: string[] } {
  const currentPattern = patternEntryFor(currentPatternId, patterns, layout);
  const preferredKinds = preferredKindsForLayout(layout, currentPattern);
  const familyRanks = familyRanksForLayout(layout);
  const originalIndex = new Map(patternIds.map((patternId, index) => [patternId, index]));

  const score = (patternId: string) => {
    if (patternId === currentPatternId) return -1000;
    if (patternId === selectedPatternId) return -900;
    const pattern = patternEntryFor(patternId, patterns, layout);
    const kindRank = preferredKinds.includes(pattern.kind) ? preferredKinds.indexOf(pattern.kind) : 20;
    const familyRank = familyRanks.get(pattern.family) ?? 99;
    return familyRank * 100 + kindRank * 10 + (originalIndex.get(patternId) ?? 999);
  };

  const compatible = patternIds
    .filter((patternId) => preferredKinds.includes(patternEntryFor(patternId, patterns, layout).kind))
    .sort((a, b) => score(a) - score(b));
  const incompatible = patternIds
    .filter((patternId) => !preferredKinds.includes(patternEntryFor(patternId, patterns, layout).kind))
    .sort((a, b) => score(a) - score(b));

  return { compatible, incompatible, preferredKinds };
}

function LayoutPatternCard({
  patternId,
  pattern,
  selected,
  current,
  onSelect,
}: {
  patternId: string;
  pattern: LayoutPatternEntry;
  selected: boolean;
  current: boolean;
  onSelect: () => void;
}) {
  const info = layoutPatternInfo(patternId);

  return (
    <button
      type="button"
      aria-pressed={selected}
      title={`${info.label}: ${info.bestFor}`}
      onClick={onSelect}
      style={{
        minWidth: 0,
        height: "100%",
        border: current ? "1.5px solid #0a0a0a" : "1px solid #948e83",
        outline: selected ? "2px solid #0a0a0a" : "2px solid transparent",
        outlineOffset: 1,
        borderRadius: 0,
        background: selected ? "#e8e3d8" : "#f5f3ee",
        padding: 8,
        display: "grid",
        gap: 7,
        textAlign: "left",
        cursor: "pointer",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "start" }}>
        <div style={{ fontSize: 12, fontWeight: 700, lineHeight: 1.2 }}>{info.label}</div>
        <span
          style={{
            flexShrink: 0,
            background: current ? "#0a0a0a" : "transparent",
            color: current ? "#f5f3ee" : "#5c5852",
            border: "1px solid #0a0a0a",
            borderRadius: 0,
            padding: "3px 8px",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 1.4,
            textTransform: "uppercase" as const,
            lineHeight: 1.4,
          }}
        >
          {current ? "AI" : pattern.kind}
        </span>
      </div>
      <div style={{ display: "flex", justifyContent: "center", overflow: "hidden" }}>
        <LayoutWireframe
          pattern={patternId}
          family={pattern.family}
          zones={pattern.zones}
          width={132}
          aspectRatio="16:9"
        />
      </div>
      <div style={{ fontSize: 11, color: "#5c5852", lineHeight: 1.35 }}>{info.bestFor}</div>
      {info.caution && (
        <div style={{ fontSize: 10, color: "#8a5a14", lineHeight: 1.3 }}>{info.caution}</div>
      )}
      <code style={{ fontSize: 10, color: "#948e83", whiteSpace: "normal", wordBreak: "break-word" }}>
        {patternId}
      </code>
    </button>
  );
}

export function LayoutStage({
  catalog,
  layouts,
  title,
  submitLabel,
  onSubmit,
  submitDisabledWhenUnchanged = false,
  selectedVisualStylePresetId = null,
}: LayoutStageProps) {
  const [overrides, setOverrides] = useState<Record<number, string>>({});
  const [expandedRows, setExpandedRows] = useState<Record<number, boolean>>({});
  const [visualPresetId, setVisualPresetId] = useState(selectedVisualStylePresetId ?? "");
  const [foldedAfterApprove, setFoldedAfterApprove] = useState(false);
  const rows = layouts ?? [];
  const patterns = catalog?.patterns ?? {};
  const patternIds = useMemo(() => Object.keys(patterns), [patterns]);
  const visualPresets = catalog?.visual_style_presets ?? [];
  const selectedPreset = visualPresets.find((preset) => preset.id === visualPresetId);
  const hasOverrides = Object.values(overrides).some(Boolean);
  const hasPresetChange = visualPresetId !== (selectedVisualStylePresetId ?? "");

  useEffect(() => {
    setVisualPresetId(selectedVisualStylePresetId ?? "");
  }, [selectedVisualStylePresetId]);

  async function submitLayoutReview() {
    setFoldedAfterApprove(true);
    await onSubmit({
      overrides,
      visual_style_preset_id: visualPresetId || null,
    });
  }

  return (
    <div style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <h3 style={{ marginTop: 0 }}>{title}</h3>
        {foldedAfterApprove && (
          <button className="osz-button" type="button" onClick={() => setFoldedAfterApprove(false)}>
            Reopen review
          </button>
        )}
      </div>
      {foldedAfterApprove ? (
        <div style={{ color: "#5c5852", fontSize: 13, lineHeight: 1.45 }}>
          Layout review approved. Rendering HTML is continuing in the live panel.
        </div>
      ) : (
        <>
      {visualPresets.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <label style={{ display: "block", fontSize: 13, fontWeight: 600 }}>
            Visual direction
            <select
              value={visualPresetId}
              onChange={(e) => setVisualPresetId(e.target.value)}
              style={{ display: "block", width: "100%", maxWidth: 420, marginTop: 6 }}
            >
              <option value="">AI Decide</option>
              {visualPresets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
          </label>
          <div style={{ color: "#5c5852", fontSize: 12, lineHeight: 1.45, marginTop: 6, maxWidth: 680 }}>
            {selectedPreset?.description ??
              "Optional final visual preference appended directly to the HTML generation prompt."}
          </div>
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 720px), 1fr))",
          gap: 12,
        }}
      >
        {rows.map((l) => {
          const slideIdx = Number(l.slide_idx);
          const currentPatternId = String(l.pattern);
          const selectedPatternId = overrides[slideIdx] || currentPatternId;
          const rowPatternIds = uniquePatternIds([currentPatternId, selectedPatternId, ...patternIds]);
          const { compatible, incompatible, preferredKinds } = sortedPatternIdsForLayout(
            rowPatternIds,
            patterns,
            l,
            currentPatternId,
            selectedPatternId,
          );
          const expanded = Boolean(expandedRows[slideIdx]);
          const expandedPatternIds = uniquePatternIds([
            currentPatternId,
            selectedPatternId,
            ...compatible,
            ...incompatible,
          ]);
          const compactPatternIds = uniquePatternIds([
            currentPatternId,
            selectedPatternId,
            ...compatible,
          ]).slice(0, COMPACT_PATTERN_LIMIT);
          const displayPatternIds = expanded ? expandedPatternIds : compactPatternIds;
          const hiddenCount = Math.max(0, expandedPatternIds.length - compactPatternIds.length);
          const currentPattern = patternEntryFor(currentPatternId, patterns, l);
          const selectedPattern = patternEntryFor(selectedPatternId, patterns, l);

          return (
            <div
              key={String(l.slide_idx)}
              style={{
                border: "1.5px solid #0a0a0a",
                padding: 12,
                borderRadius: 0,
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
                gap: 14,
                alignItems: "start",
                background: "#f5f3ee",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <LayoutWireframe
                  pattern={selectedPatternId}
                  family={selectedPattern.family}
                  zones={selectedPattern.zones}
                  width={280}
                  aspectRatio="16:9"
                />
                <div style={{ fontWeight: 600, lineHeight: 1.3, marginTop: 10 }}>
                  {slideIdx + 1}. {String(l.title)}
                </div>
                <div style={{ fontSize: 12, color: "#5c5852", marginTop: 4 }}>
                  AI selected <code>{currentPatternId}</code>
                  <span style={{ color: "#948e83" }}> · {String(currentPattern.family)}</span>
                </div>
                {overrides[slideIdx] && (
                  <div style={{ fontSize: 12, color: "#0a0a0a", marginTop: 6 }}>
                    Override selected: <code>{overrides[slideIdx]}</code>
                  </div>
                )}
                <details style={{ marginTop: 8 }}>
                  <summary style={{ fontSize: 12, cursor: "pointer" }}>scores</summary>
                  <ul style={{ fontSize: 11, margin: "4px 0 0 0", paddingLeft: 16 }}>
                    {((l.ranking_top3 as Array<{ family: string; score: number }>) ?? []).map((r, i) => (
                      <li key={i}>
                        <code>{r.family}</code>: {Number(r.score).toFixed(2)}
                      </li>
                    ))}
                  </ul>
                </details>
              </div>
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    justifyContent: "space-between",
                    gap: 10,
                    marginBottom: 8,
                  }}
                >
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>Choose layout</div>
                    <div style={{ fontSize: 11, color: "#5c5852", marginTop: 2 }}>
                      Showing {preferredKinds.join(" / ")} layouts first. Pick the AI card to keep the original.
                    </div>
                  </div>
                  {hiddenCount > 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedRows((prev) => ({
                          ...prev,
                          [slideIdx]: !expanded,
                        }))
                      }
                      style={{ flexShrink: 0, fontSize: 11 }}
                    >
                      {expanded ? "Show fewer" : `Show all (${expandedPatternIds.length})`}
                    </button>
                  )}
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                    gap: 8,
                    alignItems: "stretch",
                  }}
                >
                  {displayPatternIds.map((patternId) => {
                    const pattern = patternEntryFor(patternId, patterns, l);
                    return (
                      <LayoutPatternCard
                        key={patternId}
                        patternId={patternId}
                        pattern={pattern}
                        selected={patternId === selectedPatternId}
                        current={patternId === currentPatternId}
                        onSelect={() =>
                          setOverrides((prev) => {
                            const next = { ...prev };
                            if (patternId === currentPatternId) {
                              delete next[slideIdx];
                            } else {
                              next[slideIdx] = patternId;
                            }
                            return next;
                          })
                        }
                      />
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 12 }}>
        <button
          className="osz-button osz-button-primary"
          disabled={submitDisabledWhenUnchanged && !hasOverrides && !hasPresetChange}
          onClick={() => void submitLayoutReview()}
        >
          {submitLabel}
        </button>
      </div>
        </>
      )}
    </div>
  );
}

export function BriefReview({ briefMd }: { briefMd?: string }) {
  return (
    <section style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
      <h3>④ Consolidated brief</h3>
      <p style={{ marginTop: 0, color: "#5c5852" }}>
        This is the pre-render merged brief. To change it, go back to structure, style, or layout and create a fork from there.
      </p>
      <div
        style={{
          background: "#e8e3d8",
          padding: 12,
          border: "1.5px solid #0a0a0a",
          borderRadius: 0,
          maxHeight: 560,
          overflow: "auto",
        }}
      >
        <Markdown>{briefMd ?? ""}</Markdown>
      </div>
    </section>
  );
}

export function HtmlStage({
  failedSlides,
  renderedCount,
  expectedCount,
  title,
  submitLabel,
  onSubmit,
}: HtmlStageProps) {
  const rows = failedSlides ?? [];

  return (
    <section style={{ padding: 24, border: "1.5px solid #0a0a0a", borderRadius: 0, background: "#f5f3ee" }}>
      <h3>{title}</h3>
      <p style={{ marginTop: 0, color: "#5c5852" }}>
        Rendered {renderedCount ?? 0} of {expectedCount ?? 0} slides. The deck is paused until the failed slides are re-rendered successfully.
      </p>
      <div style={{ display: "grid", gap: 10 }}>
        {rows.map((row) => (
          <div
            key={String(row.slide_idx)}
            style={{
              border: "1.5px solid #0a0a0a",
              borderRadius: 0,
              padding: 12,
              background: "#e8e3d8",
            }}
          >
            <div style={{ fontWeight: 600 }}>
              Slide {Number(row.slide_idx) + 1}
            </div>
            <div style={{ fontSize: 12, color: "#5c5852", marginTop: 4 }}>
              {String(row.reason ?? "HTML generation failed")}
            </div>
            {row.finish_reason && (
              <div style={{ fontSize: 11, color: "#948e83", marginTop: 4 }}>
                finish_reason: <code>{String(row.finish_reason)}</code>
              </div>
            )}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 12 }}>
        <button onClick={() => onSubmit({ retry_failed: true })}>{submitLabel}</button>
      </div>
    </section>
  );
}
