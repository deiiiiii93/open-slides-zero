"""Regression test: stray image_slots in a non-Gallery outline must NOT force
every slide into image_gallery_grid.

Background: previously, any slide with non-empty `image_slots` was unconditionally
flipped to content_type/semantic_family/content_shape = gallery, which triple-bumped
the scorer's grid family. With the digest node feeding more context to the outline
LLM, the model started populating image_slots for non-Gallery structures (e.g.
hero_journey on a text-only article), forcing every middle slide into
image_gallery_grid. The guard now requires either structure_id == "gallery" or
materials with kind == "image".
"""

from __future__ import annotations

from app.graph.nodes import layout as layout_mod
from app.graph.nodes.layout import _BulkSignals, _PerSlideSignals, layout_node


def _enrich_for(slides):
    """Build a deterministic _BulkSignals fixture mirroring what the LLM returns."""
    per: list[_PerSlideSignals] = []
    for i, sl in enumerate(slides):
        role = sl.get("role", "content")
        # Realistic non-gallery enrichment: text-driven content with reasonable
        # candidate patterns. Importantly, no "gallery" content_type and no
        # image_gallery_grid in candidates — the LLM never asks for image grid
        # here, so the only way it could appear is via the image_slots override.
        per.append(
            _PerSlideSignals(
                slide_idx=i,
                content_type="cover" if role == "cover" else "content",
                semantic_family="narrative",
                content_shape="freeform_text",
                item_count=len(sl.get("bullets") or []),
                text_length=sum(len(b) for b in (sl.get("bullets") or [])),
                story_role=role,
                candidate_patterns=(
                    ["cover_center_title", "cover_left_image"]
                    if role == "cover"
                    else (
                        ["closing_centered"]
                        if role in ("close", "closing")
                        else ["section_header_full", "two_col_left_text", "callout_center"]
                    )
                ),
            )
        )
    return _BulkSignals(slides=per)


def test_text_only_hero_journey_with_stray_image_slots_does_not_force_gallery(monkeypatch):
    """Hero-journey deck, text-only materials, but the outline LLM leaked an
    image_slot onto every middle slide. The layout node must ignore those
    slots and pick text-driven layouts."""
    outline_slides = [
        {"title": "Cover", "role": "cover", "bullets": ["a", "b"], "image_slots": []},
        {
            "title": "Old world",
            "role": "context",
            "bullets": ["fact1", "fact2", "fact3"],
            "image_slots": ["a moody photo of an empty city"],  # stray
        },
        {
            "title": "Hero's dilemma",
            "role": "tension",
            "bullets": ["q1", "q2", "q3"],
            "image_slots": ["a portrait of struggle", "a chart of decline"],  # stray
        },
        {
            "title": "Turning point",
            "role": "decision",
            "bullets": ["d1", "d2", "d3"],
            "image_slots": ["abstract pivot illustration"],  # stray
        },
        {"title": "Closing", "role": "closing", "bullets": ["wrap"], "image_slots": []},
    ]
    state = {
        "outline_slides": outline_slides,
        "structure_id": "hero_journey",
        "scenario_id": "vision_keynote",
        "materials": [
            {"kind": "text", "uri": "text:long article body", "parsed": "..."}
        ],
        "language": "en",
        "languages": ["en"],
        "aspect_ratio": "16:9",
        "density_preference": "balanced",
    }

    enrich = _enrich_for(outline_slides)
    monkeypatch.setattr(
        layout_mod.zenmux,
        "chat_structured",
        lambda *args, **kwargs: enrich,
    )

    out = layout_node(state)
    layouts = out["layouts"]
    middle = [l for l in layouts if l["story_role"] not in ("cover", "closing", "close")]

    # Every middle slide had image_slots populated; with the guard, NONE of them
    # should land on image_gallery_grid.
    assert all(l["pattern"] != "image_gallery_grid" for l in middle), (
        "stray image_slots forced gallery layout on a non-gallery deck: "
        + ", ".join(l["pattern"] for l in middle)
    )
    assert all(l["family"] != "grid" or l["family"] == "grid" and "gallery" not in l["pattern"]
               for l in middle)


def test_gallery_structure_engages_image_override(monkeypatch):
    """When structure_id == 'gallery', the image_slots override path fires:
    content_shape becomes 'image_gallery' on each non-cover slide, proving the
    guard correctly authorizes the override. (Final pattern selection still
    depends on overall scoring, which is a separate concern.)"""
    outline_slides = [
        {"title": "Cover", "role": "cover", "bullets": ["title"], "image_slots": []},
        {
            "title": "Album page",
            "role": "context",
            "bullets": ["caption"],
            "image_slots": ["beach 1", "beach 2", "beach 3"],
        },
    ]
    state = {
        "outline_slides": outline_slides,
        "structure_id": "gallery",
        "scenario_id": "creator_album",
        "materials": [{"kind": "text", "uri": "text:notes", "parsed": "notes"}],
        "language": "en",
        "languages": ["en"],
        "aspect_ratio": "16:9",
        "density_preference": "balanced",
    }
    enrich = _enrich_for(outline_slides)
    monkeypatch.setattr(
        layout_mod.zenmux, "chat_structured", lambda *args, **kwargs: enrich
    )

    out = layout_node(state)
    middle = [l for l in out["layouts"] if l["story_role"] not in ("cover", "closing", "close")]
    assert middle, "expected at least one non-cover slide"
    # Override path fires: content_shape is mutated to image_gallery and grid
    # appears prominently in the ranked families.
    assert all(l["content_shape"] == "image_gallery" for l in middle)
    for l in middle:
        ranked_families = {r["family"] for r in l.get("ranking_top3") or []}
        assert "grid" in ranked_families


def test_image_materials_still_authorize_gallery_override(monkeypatch):
    """Even on non-gallery structure, if the user attached image materials, the
    image_slots should still be honored — the user has actual images to display."""
    outline_slides = [
        {"title": "Cover", "role": "cover", "bullets": ["x"], "image_slots": []},
        {
            "title": "Photo evidence",
            "role": "proof",
            "bullets": ["caption"],
            "image_slots": ["photo 1", "photo 2"],
        },
    ]
    state = {
        "outline_slides": outline_slides,
        "structure_id": "scqa",
        "scenario_id": "investigation",
        "materials": [
            {"kind": "text", "uri": "text:notes", "parsed": "notes"},
            {"kind": "image", "uri": "image:/tmp/p1.jpg", "name": "p1.jpg"},
        ],
        "language": "en",
        "languages": ["en"],
        "aspect_ratio": "16:9",
        "density_preference": "balanced",
    }
    enrich = _enrich_for(outline_slides)
    monkeypatch.setattr(
        layout_mod.zenmux, "chat_structured", lambda *args, **kwargs: enrich
    )

    out = layout_node(state)
    proof = next(l for l in out["layouts"] if l["story_role"] == "proof")
    # Override path fires because user-attached image materials authorize it.
    assert proof["content_shape"] == "image_gallery"
    assert "grid" in {r["family"] for r in proof.get("ranking_top3") or []}
