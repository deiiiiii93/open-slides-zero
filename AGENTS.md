# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

> The root `README.md` covers setup, file map, and user-facing non-obvious rules. This file covers **what to know before editing** — data flows that span multiple files, invariants you can break by accident, and conventions the running system depends on.

## Commands

### Backend (Python 3.11, FastAPI, LangGraph)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # one-time
pip install -e ".[dev]"                              # one-time
cp .env.example .env                                 # fill in ZENMUX_API_KEY
set -a && source .env && set +a                      # load env into shell
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
.venv/bin/pytest                                     # all tests
.venv/bin/pytest tests/test_scorer.py::test_matrix_content_picks_grid  # one test
```
Port **8765** is the current convention (8000 was taken by another local service). If you change it, also change `frontend/vite.config.ts` proxy target and the `CORSMiddleware.allow_origins` list in `app/main.py`.

### Frontend (Vite + React + TS)
```bash
cd frontend
npm install
npm run dev   # binds :5174 (strictPort: true), proxies /api/* to :8765
```

### Quick import sanity check (no third-party deps required for the catalog layer)
```bash
.venv/bin/python -c "
import os; os.environ['ZENMUX_API_KEY']='probe'
from app.graph.graph import get_graph
print('nodes:', list(get_graph().nodes.keys()))
"
```

## Architecture worth reading before editing

### Split-stack harness (why there's no LangChain)
- **LangGraph** handles the state machine, SQLite checkpointer, HITL interrupts, and `Send` fan-out.
- **OpenAI SDK pointed at ZenMux** handles every LLM call. Per-subagent model IDs (`anthropic/Codex-sonnet-4.6`, `google/gemini-3.1-pro-preview`, etc.) are a config value in `app/llm/models.py`, not a provider wrapper class.
- Do **not** reach for `langchain.chat_models` or `AgentExecutor` — multi-provider routing is ZenMux's job, and each subagent is a structured-output call, not an open-ended tool-using agent. The one LLM adapter `app/llm/zenmux.py` concentrates everything split-stack costs you (retry, structured-output with `json-repair` fallback, streaming, vision-capability guard).

### Data flow for one full run
```
ingest_node → propose_structures_node → [interrupt: structure]
           → outline_node → style_node → [interrupt: style]
           → layout_node → [interrupt: layout]
           → consolidate_node → Send("html_one", {slide_idx, brief}) × N
           → post_html → ready ⇄ optional image insertion API
                ⇄ (comment → edit_intent_node → apply_edits)
```
- `consolidate_node` produces the `brief: dict[str, Any]` field in state. Every `html_one` Send reads `brief["slides"][i]`. If `brief` is empty or missing, fan-out emits 0 Sends and the graph silently ends with 0 slides rendered — this is a real failure mode.
- `html_slides` is `dict[int, str]` with a custom merge reducer (`_merge_html` in `app/graph/state.py`). It is the *only* field the `Send` fan-out writes to, and the reducer is what makes per-slide parallel commits safe.

### Streaming (two-path design)
Tokens and events emitted by `push_token`/`push_event` in `app/llm/stream.py` flow to the frontend two ways:

1. **Inside a graph run** (`/decks/stream`, `/resume/stream`): `get_stream_writer()` returns LangGraph's writer; `graph.stream(stream_mode=["custom","updates"])` in `app/api/streaming.py` converts the chunks to SSE frames.
2. **Outside a graph run** (`/apply_edits/stream`, combined `/slides/{n}/comments/stream`): the endpoint wraps the work in `with writer_override(queue.put):` — defined in `app/llm/stream.py`. A `_safe_writer()` helper prefers the override over `get_stream_writer()` and silences LangGraph's `RuntimeError("outside runnable context")`.

The SSE endpoints spawn the LLM call onto a thread with `contextvars.copy_context()` so `tagged_stream(...)` and `writer_override(...)` propagate correctly. Don't replace this with a plain `threading.Thread(target=work)` — it'll break tagging.

### Regenerate-from-stage (rewind, don't fake-inject)
`app/api/history.py::_regenerate_from` walks `graph.get_state_history(...)` to find the most recent checkpoint whose `next` queue contains the target node (see `_STAGE_TO_NODE`). It invokes forward from that fork — LangGraph re-executes the node.

Do **not** use `graph.update_state(cfg, values, as_node=X)` to "regenerate from X" — that tells the graph to *pretend* node X just produced `values` and replay the outgoing edge. The node itself never re-runs.

### Html-only edits bypass the graph
`app/api/comments.py::_regenerate_html_only` calls `html_one_node({slide_idx, brief, feedback})` as a plain Python function for each affected slide, then `update_state({"html_slides": updates})`. The reducer merges; unaffected slides remain verbatim. This path exists because:
- Full rewind → consolidate → fan-out re-renders all 8 slides unnecessarily.
- It's brittle when history is polluted (empty `layouts` at the pre-consolidate checkpoint produces an empty brief and 0 Sends).

Comments on a slide are grouped by `slide_idx` and passed as the `feedback` block in the html_one prompt — the model sees the user's literal words, not a classified summary.

### Image insertion stage is ready-time API state, not graph fan-out
`post_html` does not interrupt for images. It sets `current_stage: "ready"` and
`image_insertion_status` to `"available"` when `has_image_insertion_opportunity`
finds image placeholders or assets; otherwise the status is `"unavailable"`.
The ready screen then uses `app/api/images.py` endpoints:

- `POST /decks/{thread_id}/images/plan` builds `image_insertion_plan` by matching
  `image_assets` to extracted `data-image-placeholder` slots.
- `POST /decks/{thread_id}/images/apply` replaces approved placeholders with
  `<img data-inserted-image="true" data-image-asset-id="...">`.
- `POST /decks/{thread_id}/images/generate` and `generate_batch` create new
  assets for unmatched slots, then apply those generated assets.

`app/graph/nodes/image_insert.py::apply_image_mappings` is intentionally
idempotent: it reads from `html_slides_base` when present, writes updated
`html_slides`, and stores the original placeholder HTML in `html_slides_base`.
Do not apply mappings against already-mutated `html_slides` unless there is no
base HTML yet, or repeated image changes will compound against inserted `<img>`
tags. Generated files live under `./threads/{thread_id}/images/`.

### Edit intent is a **list**, collapsed earliest-stage-wins
`edit_intent_node` returns a list of `EditOp`s (one comment may produce multiple ops). `collapse_edit_ops` in `app/graph/nodes/edit.py` picks the **earliest** stage across all ops and merges their `patch_fragment`s. Don't try to apply multiple ops at different stages as separate forks — you'll lose changes to divergent branches.

Stage ordering (earliest→latest): `outline → style → layout → html → image_only`.

### Layout decision engine
`app/catalog/scorer.py::pick_pattern` is a pure function implementing catalog §6 weighted heuristics + §7 tie-breaks. The **LLM proposes 3–5 candidate patterns**, the scorer ranks families and picks. Never move scoring into an LLM prompt; the determinism is load-bearing for HITL explainability and for the golden tests in `tests/test_scorer.py`.

### PPTX export and remote images
`frontend/src/exporter.ts` exports editable PPTX by walking each slide iframe DOM
and translating elements to `pptxgenjs` shapes. Browser-rendered `<img>` nodes
may contain public remote URLs from user-provided image assets, GBIF/iNaturalist,
or other source material. Do not pass those external URLs directly to
`slide.addImage(...)`: `pptxgenjs` fetch failures abort the entire PPTX write.

External HTTP(S) image sources should route through the same-origin
`/images/proxy?url=...` backend endpoint in `app/api/images.py`. The proxy only
allows public HTTP(S) destinations, follows a small redirect chain, enforces an
image content type and size limit, and returns a lightweight placeholder image
when the upstream fetch fails. Keep PPTX image insertion best-effort: a bad
remote image should render an unavailable-image box for that slot, not fail the
whole deck.

### Invariants — each of these has bitten us
1. **Checkpointer is authoritative**. `./threads/{thread_id}/*.md` files are a derived mirror written after each commit; never read them back into state. If a file disappears, regenerate from checkpoint.
2. **Every field a node writes must be declared in `SlideState`** (`app/graph/state.py`). Undeclared keys are silently dropped by LangGraph's state merger. The `brief` field was added precisely because a previous `_brief` (with a leading underscore "for privacy") disappeared this way.
3. **`chat_structured` has a repair path**. When schema validation fails on a raw LLM response, `app/llm/zenmux.py` retries via `json_repair.repair_json` before giving up. CJK content often produces unescaped `"` inside string values; the repair library handles it. Don't remove this fallback.
4. **In LangGraph 1.x, `get_stream_writer()` raises `RuntimeError` outside a runnable context** — it does not return `None`. `_safe_writer()` in `stream.py` catches that.
5. **Anti-slop HTML rules live in two places**: the system prompt in `app/graph/nodes/html_one.py::ANTI_SLOP_RULES`, and the post-generation check in `app/catalog/validator.py`. If you add a new "don't" rule (e.g. a newly overused font), update *both*.
6. **Vision guard**: `app/llm/models.py::vision_capable()` + `VISION_FALLBACK`. When images are attached to a chat and the routed model doesn't declare image input, `zenmux.chat()` reroutes to the fallback and logs. Don't bypass — misrouted images produce cryptic upstream errors.
7. **PPTX export is best-effort around images**. Remote image failures must not abort export; route public remote images through `/images/proxy` and keep the frontend fallback placeholder path intact.
8. **Image insertion must stay idempotent**. Keep `html_slides_base` as the pre-insertion source of truth for remapping image slots; `html_slides` is the current rendered deck and may already contain inserted image tags.

## Adding a new subagent (common extension)

1. Decide the model: add `"new_stage": "<zenmux-model-id>"` to `_DEFAULTS` in `app/llm/models.py`.
2. Create `app/graph/nodes/new_stage.py`:
   ```python
   from ...llm import zenmux
   from ...llm.models import get_model
   from ...llm.stream import push_event, tagged_stream
   def new_stage_node(state):
       push_event({"node": "new_stage", "state": "started"})
       with tagged_stream("new_stage"):
           out = zenmux.chat_structured(
               get_model("new_stage"), messages, SomeSchema, stream=True,
           )
       return {"<state_field>": out.model_dump(), "current_stage": "next_stage"}
   ```
3. Declare any new state fields in `SlideState` (`app/graph/state.py`) — otherwise they'll be dropped.
4. Wire into `app/graph/graph.py`: `g.add_node("new_stage", new_stage_node)` and connect with `add_edge`.
5. Update `_STAGE_TO_NODE` in `app/api/history.py` if you want regenerate-from-stage to target it.
6. Add a label in `frontend/src/LiveStream.tsx::NODE_LABELS` so the live pane shows a friendly name.

## Where to look

- **Plan + rationale**: see project planning notes if they are shared with the
  issue or PR. Do not depend on private local plan files.
- **Long-form design rules**: `README.md` → "Non-obvious design rules"
- **Tests as behavior spec**: `backend/tests/` — the layout scorer, constraint validator, and edit-op collapse have golden fixtures worth reading before changing those modules
