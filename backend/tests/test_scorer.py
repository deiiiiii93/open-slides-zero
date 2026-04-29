"""Golden-fixture sanity tests for the layout scorer.

These tests are pure — no LLM, no network — and validate that the decision
engine's §6 heuristics produce the family the Layout Catalog says they should.
"""

from app.catalog import layouts as L
from app.catalog.scorer import SlideSignal, pick_family, pick_pattern, score_families


def test_matrix_content_picks_grid():
    sig = SlideSignal(content_type="matrix", content_shape="matrix_2x2",
                      semantic_family="comparison", item_count=4, text_length=80)
    family, _ = pick_family(sig)
    assert family == "grid"


def test_long_list_falls_to_vertical_or_paginated():
    sig = SlideSignal(content_type="content", item_count=12, text_length=900)
    family, _ = pick_family(sig)
    assert family in {"vertical", "paginated"}


def test_cover_forces_narrative():
    sig = SlideSignal(is_cover=True, content_type="cover", item_count=1, text_length=40)
    family, _ = pick_family(sig)
    assert family == "narrative"


def test_radial_branches_picks_radial():
    sig = SlideSignal(content_shape="radial_branches", semantic_family="hierarchy",
                      item_count=6, text_length=120)
    family, _ = pick_family(sig)
    assert family == "radial"


def test_21_9_prefers_horizontal():
    sig = SlideSignal(content_type="comparison", aspect_ratio="21:9",
                      item_count=3, text_length=100)
    family, _ = pick_family(sig)
    assert family == "horizontal"


def test_pick_pattern_honors_llm_candidate_when_in_family():
    sig = SlideSignal(content_type="comparison", content_shape="parallel_columns",
                      item_count=3, text_length=120)
    decision = pick_pattern(sig, llm_candidates=["three_parallel_columns"])
    assert decision["pattern"] == "three_parallel_columns"
    assert decision["family"] == "horizontal"


def test_pick_pattern_override_beats_everything():
    sig = SlideSignal(content_type="matrix", content_shape="matrix_2x2", item_count=4)
    decision = pick_pattern(sig, override="radial_core")   # legacy name
    assert decision["pattern"] == "radial_compact"
    assert decision["family"] == "radial"
    assert decision["source"] == "override"


def test_image_gallery_signal_picks_gallery_grid():
    sig = SlideSignal(
        content_type="gallery",
        semantic_family="gallery",
        content_shape="image_gallery",
        item_count=3,
        text_length=120,
    )
    decision = pick_pattern(sig)
    assert decision["family"] == "grid"
    assert decision["pattern"] == "image_gallery_grid"


def test_scorer_never_produces_unknown_family():
    sig = SlideSignal()
    ranked = score_families(sig)
    assert {r.family for r in ranked} == set(L.FAMILIES.keys())


def test_high_risk_content_tiebreak_prefers_safe_family():
    # §7 tie-break only engages when top-2 are within 0.1. Construct a signal
    # where horizontal and vertical are actually close, plus high risk, and
    # verify the safe family wins.
    sig = SlideSignal(
        item_count=20,        # > MAX_TOTAL_ELEMENTS=17 → high risk
        text_length=1200,     # > MAX_TEXT_LENGTH=1150 → high risk
        content_type="content",
        semantic_family="freeform",
        content_shape="freeform_text",
        aspect_ratio="16:9",
    )
    family, _ = pick_family(sig)
    assert family in {"vertical", "adaptive", "paginated"}
