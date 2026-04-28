# Contributing

Thanks for considering a contribution to Open Slides Zero.

## Development Setup

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Frontend:

```bash
cd frontend
npm install
```

Real API keys belong only in ignored local `.env` files. Do not commit secrets,
SQLite databases, generated `threads/` artifacts, dependency folders, or build
output.

## Checks

Run backend tests:

```bash
cd backend
.venv/bin/pytest
```

Run frontend build:

```bash
cd frontend
npm run build
```

Run the graph import sanity check from the repository root:

```bash
backend/.venv/bin/python -c "import os; os.environ['ZENMUX_API_KEY']='probe'; from app.graph.graph import get_graph; print('nodes:', list(get_graph().nodes.keys()))"
```

## Pull Requests

- Keep changes scoped to one concern.
- Include tests for behavior changes when practical.
- Update docs when setup, public behavior, ports, or environment variables
  change.
- Preserve deterministic layout scoring in `backend/app/catalog/scorer.py`.
- Declare every LangGraph state field in `backend/app/graph/state.py`; unknown
  fields are dropped by the state merger.
