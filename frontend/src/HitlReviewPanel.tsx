// Three HITL review panels — one per gate. The `gate` field on the interrupt
// payload determines which UI to show.

import { useState } from "react";
import type { DeckState, CatalogResponse } from "./api";
import { LayoutWireframe } from "./LayoutWireframe";
import { Markdown } from "./Markdown";

type Props = {
  deck: DeckState;
  catalog: CatalogResponse | null;
  onResume: (payload: Record<string, unknown>) => Promise<void>;
};

function firstInterrupt(deck: DeckState): any {
  const i = deck.interrupts?.[0];
  if (!i) return null;
  return typeof i === "object" && "value" in i ? (i as any).value : i;
}

export function HitlReviewPanel({ deck, catalog, onResume }: Props) {
  const payload = firstInterrupt(deck);
  if (!payload) return null;

  const gate = payload.gate as "structure" | "style" | "layout";

  if (gate === "structure") {
    return <StructureGate payload={payload} catalog={catalog} onResume={onResume} />;
  }
  if (gate === "style") {
    return <StyleGate payload={payload} onResume={onResume} />;
  }
  if (gate === "layout") {
    return <LayoutGate payload={payload} catalog={catalog} onResume={onResume} />;
  }
  return <pre>{JSON.stringify(payload, null, 2)}</pre>;
}

// ---------------- Structure ----------------

function StructureGate({
  payload,
  catalog,
  onResume,
}: {
  payload: any;
  catalog: CatalogResponse | null;
  onResume: (p: Record<string, unknown>) => Promise<void>;
}) {
  const [scenarioId, setScenarioId] = useState<string>(payload.scenario_id ?? "");
  const [structureId, setStructureId] = useState<string>(payload.candidates?.[0] ?? "");

  const structuresById = Object.fromEntries(
    (catalog?.structures ?? []).map((s) => [s.id, s]),
  );

  const allowedStructures =
    catalog?.scenarios.find((s) => s.id === scenarioId)?.structures ?? payload.candidates ?? [];

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>① Pick scenario + narrative structure</h3>
      <label>
        Scenario:{" "}
        <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
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
                onClick={() => setStructureId(sid)}
                style={{
                  border: sid === structureId ? "2px solid #2563eb" : "1px solid #e5e5e5",
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
          disabled={!scenarioId || !structureId}
          onClick={() => onResume({ scenario_id: scenarioId, structure_id: structureId })}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

// ---------------- Style ----------------

function hexSwatches(palette: Record<string, any> | undefined): { name: string; hex: string }[] {
  if (!palette) return [];
  const ordered = ["primary", "secondary", "accent", "neutral_dark", "neutral_light", "background"];
  const out: { name: string; hex: string }[] = [];
  for (const k of ordered) {
    const v = palette[k];
    if (v) out.push({ name: k, hex: v });
  }
  // Top-level extras (not ordered, not the nested 'roles' dict)
  for (const [k, v] of Object.entries(palette)) {
    if (!ordered.includes(k) && k !== "roles" && typeof v === "string" && v.startsWith("#")) {
      out.push({ name: k, hex: v });
    }
  }
  // Colors nested inside palette.roles
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

function StyleGate({
  payload,
  onResume,
}: {
  payload: any;
  onResume: (p: Record<string, unknown>) => Promise<void>;
}) {
  const [feedback, setFeedback] = useState("");
  const palette = payload.visual_style?.palette;
  const swatches = hexSwatches(palette);

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>② Review visual style</h3>

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
        <Markdown>{payload.visual_style_md ?? ""}</Markdown>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button onClick={() => onResume({ approved: true })}>Approve & continue</button>
        <input
          style={{ flex: 1 }}
          placeholder="Revision feedback (e.g. ‘use a warmer palette, more editorial serifs’)"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <button disabled={!feedback.trim()} onClick={() => onResume({ revise: feedback.trim() })}>
          Revise
        </button>
      </div>
    </div>
  );
}

// ---------------- Layout ----------------

function LayoutGate({
  payload,
  catalog,
  onResume,
}: {
  payload: any;
  catalog: CatalogResponse | null;
  onResume: (p: Record<string, unknown>) => Promise<void>;
}) {
  const [overrides, setOverrides] = useState<Record<number, string>>({});
  const layouts = payload.layouts ?? [];
  const patternIds = Object.keys(catalog?.patterns ?? {});

  return (
    <div style={{ padding: 16, border: "1px solid #e5e5e5", borderRadius: 6 }}>
      <h3>③ Review layouts (scores shown)</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
        {layouts.map((l: any) => (
          <div
            key={l.slide_idx}
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
              pattern={l.pattern}
              family={l.family}
              zones={l.zones ?? []}
              width={320}
              aspectRatio="16:9"
            />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, lineHeight: 1.3 }}>
                {l.slide_idx + 1}. {l.title}
              </div>
              <div style={{ fontSize: 12, color: "#555", marginTop: 4 }}>
                <code>{l.pattern}</code>
                <span style={{ color: "#999" }}> · {l.family}</span>
              </div>
              <details style={{ marginTop: 6 }}>
                <summary style={{ fontSize: 12, cursor: "pointer" }}>scores</summary>
                <ul style={{ fontSize: 11, margin: "4px 0 0 0", paddingLeft: 16 }}>
                  {(l.ranking_top3 ?? []).map((r: any, i: number) => (
                    <li key={i}>
                      <code>{r.family}</code>: {Number(r.score).toFixed(2)}
                    </li>
                  ))}
                </ul>
              </details>
              <label style={{ display: "block", marginTop: 8, fontSize: 12 }}>
                Override:{" "}
                <select
                  value={overrides[l.slide_idx] ?? ""}
                  onChange={(e) =>
                    setOverrides({ ...overrides, [l.slide_idx]: e.target.value })
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
          onClick={() => onResume({ approved: true, overrides })}
        >
          Approve & render HTML
        </button>
      </div>
    </div>
  );
}
