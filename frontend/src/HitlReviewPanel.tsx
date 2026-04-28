// Shared step panels for both live HITL interrupts and ready-time review.

import { useMemo, useState } from "react";
import type { CatalogResponse, DeckState } from "./api";
import { LayoutWireframe } from "./LayoutWireframe";
import { Markdown } from "./Markdown";

type StructureSubmit = { scenario_id: string; structure_id: string };
type StyleSubmit = { feedback: string };
type LayoutSubmit = { overrides: Record<number, string> };
type HtmlRetrySubmit = { retry_failed: true };
type OutlineSubmit = { approved?: true; playground?: true };

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
};

type LayoutStageProps = {
  catalog: CatalogResponse | null;
  layouts?: Array<Record<string, any>>;
  title: string;
  submitLabel: string;
  onSubmit: (payload: LayoutSubmit) => Promise<void>;
  submitDisabledWhenUnchanged?: boolean;
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
        visualStyleMd={payload.visual_style_md}
        visualStyle={payload.visual_style}
        onSubmit={async ({ feedback }) => onResume({ revise: feedback })}
        onApprove={async () => onResume({ approved: true })}
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
        title="④ Review layouts (scores shown)"
        submitLabel="Approve & render HTML"
        onSubmit={async ({ overrides }) => onResume({ approved: true, overrides })}
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
  const [playground, setPlayground] = useState(false);

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>{title}</h3>
      <div
        style={{
          background: "#fafafa",
          padding: 12,
          maxHeight: 460,
          overflow: "auto",
          border: "1px solid #e5e5e5",
          borderRadius: 4,
        }}
      >
        <Markdown>{outlineMd ?? ""}</Markdown>
      </div>
      <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
        <input
          type="checkbox"
          checked={playground}
          onChange={(e) => setPlayground(e.target.checked)}
        />
        Creator playground mode
      </label>
      <div style={{ marginTop: 12 }}>
        <button onClick={() => onSubmit(playground ? { playground: true } : { approved: true })}>
          {playground ? "Open playground" : "Continue"}
        </button>
      </div>
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
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
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
                  border: sid === selectedStructureId ? "2px solid #2563eb" : "1px solid #e5e5e5",
                  padding: 10,
                  textAlign: "left",
                  background: "white",
                  cursor: "pointer",
                }}
              >
                <div style={{ fontWeight: 600 }}>{s?.name_en ?? sid}</div>
                <div style={{ fontSize: 12, color: "#555" }}>{s?.description_en}</div>
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
}: StyleStageProps) {
  const [feedback, setFeedback] = useState("");
  const swatches = hexSwatches(visualStyle?.palette);

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
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
                border: "1px solid #e5e5e5",
                borderRadius: 6,
                overflow: "hidden",
                background: "white",
              }}
            >
              <div style={{ height: 48, background: hex }} />
              <div style={{ padding: "6px 8px", fontSize: 11, lineHeight: 1.3 }}>
                <div style={{ fontWeight: 600, textTransform: "capitalize" }}>{name}</div>
                <code style={{ color: "#666", fontSize: 10 }}>{hex.toUpperCase()}</code>
              </div>
            </div>
          ))}
        </div>
      )}

      <div
        style={{
          background: "#fafafa",
          padding: 12,
          maxHeight: 360,
          overflow: "auto",
          border: "1px solid #e5e5e5",
          borderRadius: 4,
        }}
      >
        <Markdown>{visualStyleMd ?? ""}</Markdown>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        {approveLabel && onApprove && (
          <button onClick={() => void onApprove()}>{approveLabel}</button>
        )}
        <input
          style={{ flex: 1 }}
          placeholder="Revision feedback"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <button disabled={!feedback.trim()} onClick={() => onSubmit({ feedback: feedback.trim() })}>
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

export function LayoutStage({
  catalog,
  layouts,
  title,
  submitLabel,
  onSubmit,
  submitDisabledWhenUnchanged = false,
}: LayoutStageProps) {
  const [overrides, setOverrides] = useState<Record<number, string>>({});
  const rows = layouts ?? [];
  const patternIds = Object.keys(catalog?.patterns ?? {});
  const hasOverrides = Object.values(overrides).some(Boolean);

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>{title}</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        {rows.map((l) => (
          <div
            key={String(l.slide_idx)}
            style={{
              border: "1px solid #e5e5e5",
              padding: 12,
              borderRadius: 6,
              display: "grid",
              gridTemplateColumns: "320px 1fr",
              gap: 12,
              alignItems: "start",
            }}
          >
            <LayoutWireframe
              pattern={String(l.pattern)}
              family={String(l.family)}
              zones={(l.zones as string[]) ?? []}
              width={320}
              aspectRatio="16:9"
            />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, lineHeight: 1.3 }}>
                {Number(l.slide_idx) + 1}. {String(l.title)}
              </div>
              <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
                <code>{String(l.pattern)}</code>
                <span style={{ color: "#999" }}> · {String(l.family)}</span>
              </div>
              <details style={{ marginTop: 6 }}>
                <summary style={{ fontSize: 12, cursor: "pointer" }}>scores</summary>
                <ul style={{ fontSize: 11, margin: "4px 0 0 0", paddingLeft: 16 }}>
                  {((l.ranking_top3 as Array<{ family: string; score: number }>) ?? []).map((r, i) => (
                    <li key={i}>
                      <code>{r.family}</code>: {Number(r.score).toFixed(2)}
                    </li>
                  ))}
                </ul>
              </details>
              <label style={{ display: "block", marginTop: 8, fontSize: 12 }}>
                Override:{" "}
                <select
                  value={overrides[Number(l.slide_idx)] ?? ""}
                  onChange={(e) =>
                    setOverrides((prev) => ({
                      ...prev,
                      [Number(l.slide_idx)]: e.target.value,
                    }))
                  }
                  style={{ maxWidth: "100%" }}
                >
                  <option value="">(keep)</option>
                  {patternIds.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 12 }}>
        <button
          disabled={submitDisabledWhenUnchanged && !hasOverrides}
          onClick={() => onSubmit({ overrides })}
        >
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

export function BriefReview({ briefMd }: { briefMd?: string }) {
  return (
    <section style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>④ Consolidated brief</h3>
      <p style={{ marginTop: 0, color: "#555" }}>
        This is the pre-render merged brief. To change it, go back to structure, style, or layout and create a fork from there.
      </p>
      <div
        style={{
          background: "#fafafa",
          padding: 12,
          border: "1px solid #e5e5e5",
          borderRadius: 4,
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
    <section style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>{title}</h3>
      <p style={{ marginTop: 0, color: "#555" }}>
        Rendered {renderedCount ?? 0} of {expectedCount ?? 0} slides. The deck is paused until the failed slides are re-rendered successfully.
      </p>
      <div style={{ display: "grid", gap: 10 }}>
        {rows.map((row) => (
          <div
            key={String(row.slide_idx)}
            style={{
              border: "1px solid #e5e5e5",
              borderRadius: 6,
              padding: 12,
              background: "#fafafa",
            }}
          >
            <div style={{ fontWeight: 600 }}>
              Slide {Number(row.slide_idx) + 1}
            </div>
            <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
              {String(row.reason ?? "HTML generation failed")}
            </div>
            {row.finish_reason && (
              <div style={{ fontSize: 11, color: "#777", marginTop: 4 }}>
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
