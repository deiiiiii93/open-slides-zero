# Open Slides Zero

End-to-end slide-deck agent: materials → outline → visual style → layout → HTML,
with three human review gates and a comment-driven edit loop. LLM calls go
through **ZenMux** (OpenAI-compatible gateway). State + history + HITL are
managed by **LangGraph**; each subagent makes direct OpenAI-SDK calls so per-node
model routing stays explicit.

## Architecture (at a glance)

```
Frontend (Vite + React, :5173)
    │   fetch → /api/*  (proxied)
    ▼
FastAPI (:8000) ── app/api/{decks,hitl,comments,history}
    │
    ▼
LangGraph StateGraph
    ingest → propose_structure → [interrupt: structure]
             → outline → style → [interrupt: style]
             → layout  → [interrupt: layout]
             → consolidate → Send(html_one, i) × N → ready
             (⇄ edit_intent on comments)
    │
    ▼
SqliteSaver checkpointer  (authoritative state)
    │
    ▼
./threads/{thread_id}/*.md, slides/*.html  (derived mirror)
```

**Harness choice.** LangGraph does the state machine / checkpointing / HITL /
Send-fan-out. LLM calls are direct `OpenAI(base_url=ZENMUX_BASE_URL).chat…`
so we can route to any provider behind ZenMux (OpenAI, Anthropic, Gemini,
Qwen, Sapiens) without fighting a ChatModel wrapper. See the plan at
`/Users/fuxinyao/.claude/plans/we-re-building-an-agent-calm-puzzle.md`.

## Backend setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in ZENMUX_API_KEY
export $(cat .env | xargs)    # or use direnv / dotenv-cli
uvicorn app.main:app --reload --port 8000
```

Tests (no network required — validator + scorer + edit-collapse):

```bash
pytest
```

## Frontend setup

```bash
cd frontend
npm install
npm run dev          # opens :5173, proxies /api → :8000
```

## Usage (manual demo)

1. Paste source material into the form; pick pages/aspect/density; Create.
2. Gate ① — pick scenario + narrative structure from the shortlist.
3. Gate ② — review the generated palette/typography/density; approve or revise.
4. Gate ③ — inspect per-slide layout + scorer rankings; override patterns if needed.
5. Deck renders slide-by-slide as iframes at 960×540 (scaled to fit).
6. Draw a box on any slide, leave a comment. The agent classifies the intent
   into one or more edit-ops, collapses to the earliest affected stage, and
   regenerates downstream.

## Per-subagent model routing

Defaults live in `backend/app/llm/models.py`. Override any node via env:

```bash
OSZ_MODEL_OUTLINE=anthropic/claude-sonnet-4.6
OSZ_MODEL_STYLE_VISION=google/gemini-3.1-pro-preview
OSZ_MODEL_HTML=anthropic/claude-sonnet-4.6
```

Vision guard: if a non-vision-capable model is routed to a node with images,
the adapter reroutes to `VISION_FALLBACK` and logs a warning.

## File map

```
backend/
  app/
    main.py                     FastAPI entry
    api/
      decks.py                  POST /decks, GET /decks/{id}, upload materials
      hitl.py                   POST /decks/{id}/resume
      comments.py               comment intake + apply_edits (earliest-stage-wins)
      history.py                history, regenerate-from-stage, rewind
      common.py                 graph singleton + state serializers
    graph/
      state.py                  SlideState TypedDict + Pydantic leaves
      graph.py                  StateGraph wiring, interrupts, Send fan-out
      nodes/
        ingest.py               material parsing (text + vision for images)
        outline.py              propose_structures_node + outline_node
        style.py                visual style (palette/typography/density/tone)
        layout.py               per-slide pattern picking via LLM + Python scorer
        consolidate.py          deterministic brief merge (no LLM)
        html_one.py             Send target: one slide's HTML with anti-slop rules
        edit.py                 comment → edit-op list + earliest-stage collapse
    llm/
      zenmux.py                 OpenAI(ZenMux) client, retry, structured output,
                                stream, vision guard
      models.py                 NODE → model id mapping (+ env override)
      vision.py                 image_url part helpers
      stream.py                 LangGraph get_stream_writer() passthrough
    catalog/
      structures.py             STRUCTURE_DEFINITIONS (SCQA, Pyramid, BLUF, ...)
      scenarios.py              SCENARIO_DEFINITIONS (executive, sales pitch, ...)
      layouts.py                Layout Catalog §1-5 + §8/10/12 constants
      scorer.py                 §6/§7 weighted decision engine (pure)
      validator.py              §10.3/§10.4 HTML constraint checks (pure)
    artifacts/
      store.py                  ./threads/{id}/ markdown+html mirror writer
  tests/
    test_scorer.py              golden layout decisions
    test_validator.py           canvas/overflow/pagination/font rules
    test_edit_collapse.py       multi-intent comment collapse

frontend/
  src/
    main.tsx
    App.tsx                     workflow shell
    DeckCanvas.tsx              iframe-per-slide canvas
    CommentLayer.tsx            drag-box + comment overlay
    HitlReviewPanel.tsx         structure / style / layout review UIs
    api.ts                      thin fetch wrapper
```

## Non-obvious design rules (read before changing)

1. **Checkpointer is authoritative**, disk files are derived. Don't read files back.
2. `html_slides` is `dict[int, str]` merged by a reducer so the `Send` fan-out
   can commit per slide without clobbering peers.
3. Regenerate-from-stage explicitly **nulls downstream fields** in the patch —
   LangGraph doesn't auto-invalidate them.
4. Comments collapse to the **earliest affected stage**; downstream stages
   always rerun. Don't try to apply multi-intent edits as separate forks.
5. Layout selection: **LLM proposes 3-5 pattern candidates; Python scores**.
   The scorer is deterministic and testable with golden fixtures.
6. HTML system prompt encodes anti-slop rules verbatim (no Inter/Roboto/Arial
   /Fraunces, no gradients, no rounded-corner-left-accent cards, no SVG
   imagery, `text-wrap: pretty`, modern grid).
