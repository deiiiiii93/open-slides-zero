"""FastAPI entry — wires the deck/hitl/comments/history routers and a health probe.

Run with:  uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Auto-load .env from the backend root so ZENMUX_API_KEY etc are available
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import advanced_chat, comments, decks, exports, history, hitl, images, owners, playground, streaming

logging.basicConfig(level=os.getenv("OSZ_LOG_LEVEL", "INFO"))

app = FastAPI(title="Open Slides Zero", version="0.1.0")

# CORS for local dev with the Vite frontend on :5174
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(decks.router, tags=["decks"])
app.include_router(advanced_chat.router, tags=["advanced_chat"])
app.include_router(hitl.router, tags=["hitl"])
app.include_router(comments.router, tags=["comments"])
app.include_router(images.router, tags=["images"])
app.include_router(exports.router, tags=["exports"])
app.include_router(history.router, tags=["history"])
app.include_router(streaming.router, tags=["streaming"])
app.include_router(playground.router, tags=["playground"])
app.include_router(owners.router, tags=["identity"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
