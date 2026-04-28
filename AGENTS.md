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
           → post_html → ready
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

### Edit intent is a **list**, collapsed earliest-stage-wins
`edit_intent_node` returns a list of `EditOp`s (one comment may produce multiple ops). `collapse_edit_ops` in `app/graph/nodes/edit.py` picks the **earliest** stage across all ops and merges their `patch_fragment`s. Don't try to apply multiple ops at different stages as separate forks — you'll lose changes to divergent branches.

Stage ordering (earliest→latest): `outline → style → layout → html → image_only`.

### Layout decision engine
`app/catalog/scorer.py::pick_pattern` is a pure function implementing catalog §6 weighted heuristics + §7 tie-breaks. The **LLM proposes 3–5 candidate patterns**, the scorer ranks families and picks. Never move scoring into an LLM prompt; the determinism is load-bearing for HITL explainability and for the golden tests in `tests/test_scorer.py`.

### Invariants — each of these has bitten us
1. **Checkpointer is authoritative**. `./threads/{thread_id}/*.md` files are a derived mirror written after each commit; never read them back into state. If a file disappears, regenerate from checkpoint.
2. **Every field a node writes must be declared in `SlideState`** (`app/graph/state.py`). Undeclared keys are silently dropped by LangGraph's state merger. The `brief` field was added precisely because a previous `_brief` (with a leading underscore "for privacy") disappeared this way.
3. **`chat_structured` has a repair path**. When schema validation fails on a raw LLM response, `app/llm/zenmux.py` retries via `json_repair.repair_json` before giving up. CJK content often produces unescaped `"` inside string values; the repair library handles it. Don't remove this fallback.
4. **In LangGraph 1.x, `get_stream_writer()` raises `RuntimeError` outside a runnable context** — it does not return `None`. `_safe_writer()` in `stream.py` catches that.
5. **Anti-slop HTML rules live in two places**: the system prompt in `app/graph/nodes/html_one.py::ANTI_SLOP_RULES`, and the post-generation check in `app/catalog/validator.py`. If you add a new "don't" rule (e.g. a newly overused font), update *both*.
6. **Vision guard**: `app/llm/models.py::vision_capable()` + `VISION_FALLBACK`. When images are attached to a chat and the routed model doesn't declare image input, `zenmux.chat()` reroutes to the fallback and logs. Don't bypass — misrouted images produce cryptic upstream errors.

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
