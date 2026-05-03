"""Tests for digest_materials_node — default + advanced modes, fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from app.graph.nodes import digest as digest_mod
from app.graph.nodes.digest import (
    HARD_CEILING,
    TARGET_BUDGET,
    digest_materials_node,
)


def _material(name: str, text: str, kind: str = "text") -> dict:
    return {"kind": kind, "uri": f"file:{name}", "name": name, "parsed": text}


def test_empty_materials_returns_empty_digest_no_llm_call(monkeypatch):
    def fail_chat(*args, **kwargs):
        raise AssertionError("LLM should not be called for empty materials")

    monkeypatch.setattr(digest_mod.zenmux, "chat", fail_chat)
    monkeypatch.setattr(digest_mod.zenmux, "embeddings", fail_chat)

    out = digest_materials_node({"materials": [], "agent_mode": "default"})

    assert out == {"materials_digest": [], "materials_index": None}


def test_default_mode_passthrough_when_under_budget(monkeypatch):
    """Total chars below TARGET_BUDGET → every material's text passes through verbatim."""

    def fail_chat(*args, **kwargs):
        raise AssertionError("LLM condense must not run when under budget")

    monkeypatch.setattr(digest_mod.zenmux, "chat", fail_chat)

    materials = [
        _material("a.txt", "alpha " * 200),  # ~1200 chars
        _material("b.txt", "beta " * 300),   # ~1500 chars
    ]
    out = digest_materials_node({"materials": materials, "agent_mode": "default"})

    digest = out["materials_digest"]
    assert len(digest) == 2
    for entry, original in zip(digest, materials):
        assert entry["retained_text"] == original["parsed"]
        assert entry["was_condensed"] is False
        assert entry["retained_chars"] == entry["original_chars"]
    assert out["materials_index"] is None


def test_default_mode_proportional_truncation_preserves_every_material(monkeypatch):
    """Total between TARGET and HARD_CEILING → proportional head+tail trim, no LLM."""

    def fail_chat(*args, **kwargs):
        raise AssertionError("LLM must not run in proportional band")

    monkeypatch.setattr(digest_mod.zenmux, "chat", fail_chat)

    # Construct a corpus comfortably between TARGET (80K) and HARD_CEILING (200K).
    big = "X" * 90_000
    medium = "Y" * 30_000
    small = "Z" * 2_000
    materials = [
        _material("big.txt", big),
        _material("medium.txt", medium),
        _material("small.txt", small),
    ]
    total = sum(len(m["parsed"]) for m in materials)
    assert TARGET_BUDGET < total <= HARD_CEILING

    out = digest_materials_node({"materials": materials, "agent_mode": "default"})
    digest = out["materials_digest"]

    assert len(digest) == 3
    # Every material survives in the digest — none silently dropped.
    assert {d["name"] for d in digest} == {"big.txt", "medium.txt", "small.txt"}
    # The small one is preserved verbatim because its share floor exceeds its size.
    small_entry = next(d for d in digest if d["name"] == "small.txt")
    assert small_entry["retained_text"] == small
    assert small_entry["was_condensed"] is False
    # The big one is trimmed and contains the elision marker.
    big_entry = next(d for d in digest if d["name"] == "big.txt")
    assert big_entry["retained_chars"] < big_entry["original_chars"]
    assert "elided" in big_entry["retained_text"]
    assert big_entry["note"] == "head_tail_trim"
    # Total fits the budget.
    assert sum(d["retained_chars"] for d in digest) <= TARGET_BUDGET * 1.05  # tolerance for marker


def test_default_mode_llm_condense_when_over_ceiling(monkeypatch):
    """Total > HARD_CEILING → LLM condense runs on the largest material(s)."""

    def fake_chat(_model, messages, **_kwargs):
        # Return a short condensed string sourced from the user prompt.
        user = messages[-1]["content"]
        return "CONDENSED:" + user[:200]

    monkeypatch.setattr(digest_mod.zenmux, "chat", fake_chat)

    # 250K chars total — comfortably above HARD_CEILING.
    huge = "H" * 220_000
    small = "s" * 5_000
    materials = [_material("huge.txt", huge), _material("small.txt", small)]
    total = sum(len(m["parsed"]) for m in materials)
    assert total > HARD_CEILING

    out = digest_materials_node({"materials": materials, "agent_mode": "default"})
    digest = out["materials_digest"]

    huge_entry = next(d for d in digest if d["name"] == "huge.txt")
    small_entry = next(d for d in digest if d["name"] == "small.txt")
    assert huge_entry["was_condensed"] is True
    assert huge_entry["retained_text"].startswith("CONDENSED:")
    # Small material should not be condensed (under threshold).
    assert small_entry["was_condensed"] is False
    assert sum(d["retained_chars"] for d in digest) <= TARGET_BUDGET


def test_default_mode_falls_back_to_trim_when_condense_raises(monkeypatch):
    """If the LLM condense call raises, the entry head+tail trims and records a note."""

    def boom_chat(*args, **kwargs):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr(digest_mod.zenmux, "chat", boom_chat)

    huge = "H" * 220_000
    materials = [_material("huge.txt", huge)]
    out = digest_materials_node({"materials": materials, "agent_mode": "default"})
    huge_entry = out["materials_digest"][0]

    assert huge_entry["was_condensed"] is False
    assert "condense_failed" in (huge_entry["note"] or "")
    assert "elided" in huge_entry["retained_text"]
    assert huge_entry["retained_chars"] <= TARGET_BUDGET


def test_advanced_mode_writes_index_and_picks_diverse_chunks(tmp_path, monkeypatch):
    """Advanced mode embeds chunks, persists manifest + chunks, returns RAG digest."""
    monkeypatch.setenv("OSZ_ARTIFACTS_DIR", str(tmp_path))
    # Reload the store module so it picks up the new ROOT.
    import importlib

    from app.artifacts import store

    importlib.reload(store)
    importlib.reload(digest_mod)

    # Deterministic fake embeddings: distinct unit vectors per chunk so MMR has
    # something meaningful to diversify over.
    def fake_embeddings(model, inputs, **kwargs):
        out = []
        dim = 8
        for i, _ in enumerate(inputs):
            v = [0.0] * dim
            v[i % dim] = 1.0
            out.append(v)
        return out

    monkeypatch.setattr(digest_mod.zenmux, "embeddings", fake_embeddings)

    # Force chunking by constructing a long material.
    long_text = ("paragraph %d. " % 0).ljust(2_000, "x")
    long_text = ("\n\n".join(f"paragraph {i}. " + ("x" * 1_400) for i in range(80)))
    materials = [_material("long.txt", long_text)]

    out = digest_materials_node(
        {
            "materials": materials,
            "agent_mode": "advanced",
            "thread_id": "advanced-test",
        }
    )

    digest = out["materials_digest"]
    manifest = out["materials_index"]

    assert manifest is not None
    assert manifest["embedding_model"]
    assert len(manifest["materials"]) == 1
    assert manifest["materials"][0]["chunks"]  # at least one chunk

    # Digest text should be bounded.
    assert digest[0]["retained_chars"] <= TARGET_BUDGET
    assert digest[0]["was_condensed"] is True

    # On-disk artifacts exist.
    index_dir = Path(tmp_path) / "advanced-test" / "index"
    assert (index_dir / "manifest.json").exists()
    assert (index_dir / "m0.chunks.jsonl").exists()
    parsed_manifest = json.loads((index_dir / "manifest.json").read_text())
    assert parsed_manifest["materials"][0]["chunks_path"] == "index/m0.chunks.jsonl"


def test_advanced_mode_falls_back_to_default_on_embedding_failure(tmp_path, monkeypatch):
    """If embeddings raise, advanced mode degrades to default-mode digest with note."""
    monkeypatch.setenv("OSZ_ARTIFACTS_DIR", str(tmp_path))

    import importlib

    from app.artifacts import store

    importlib.reload(store)
    importlib.reload(digest_mod)

    def boom_embeddings(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(digest_mod.zenmux, "embeddings", boom_embeddings)
    monkeypatch.setattr(
        digest_mod.zenmux, "chat", lambda *a, **k: "should not be called for tiny material"
    )

    materials = [_material("small.txt", "small payload " * 50)]
    out = digest_materials_node(
        {
            "materials": materials,
            "agent_mode": "advanced",
            "thread_id": "fallback-test",
        }
    )

    assert out["materials_index"] is None
    entry = out["materials_digest"][0]
    assert "advanced_failed" in (entry["note"] or "")
    # Tiny payload still passes through.
    assert entry["retained_text"] == materials[0]["parsed"]
