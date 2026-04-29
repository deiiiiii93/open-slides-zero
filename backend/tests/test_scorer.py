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


def test_preset_bias_flips_close_call():
    # 4:3 aspect with light text gives a horizontal/vertical close call. The
    # cultural_luxury preset bumps vertical (cover_full_bleed,
    # editorial_full_bleed_campaign) enough to flip the family.
    base = SlideSignal(
        content_type="content",
        semantic_family="freeform",
        content_shape="freeform_text",
        aspect_ratio="4:3",
        item_count=2,
        text_length=60,
    )
    family_no_preset, _ = pick_family(base)

    biased = SlideSignal(
        content_type="content",
        semantic_family="freeform",
        content_shape="freeform_text",
        aspect_ratio="4:3",
        item_count=2,
        text_length=60,
        preset_layout_bias={
            "prefer": [
                "cover_full_bleed",
                "cover_split_image",
                "editorial_hero_split",
                "narrative_focus",
                "editorial_full_bleed_campaign",
            ],
            "avoid": [
                "data_dashboard",
                "three_parallel_columns",
                "content_card_grid",
                "image_gallery_grid",
                "editorial_execution_grid",
            ],
        },
    )
    family_with_preset, ranking = pick_family(biased)
    assert family_no_preset != family_with_preset
    assert family_with_preset == "vertical"
    # The ranking components should expose preset:prefer:* and preset:avoid:* rows
    # so the HITL UI can show why the family flipped.
    top = next(r for r in ranking if r.family == "vertical")
    assert any(k.startswith("preset:prefer:") for k in top.components)
    grid = next(r for r in ranking if r.family == "grid")
    assert any(k.startswith("preset:avoid:") for k in grid.components)


def test_preset_bias_cannot_override_clear_content_fit():
    # A real image-gallery signal pulls grid +3.65 from content/semantic/shape.
    # editorial_authority's preset bias has only one grid pattern in `prefer`
    # (+0.6 capped) — not enough to flip away from grid.
    sig = SlideSignal(
        content_type="gallery",
        semantic_family="gallery",
        content_shape="image_gallery",
        item_count=3,
        text_length=120,
        preset_layout_bias={
            "prefer": [
                "editorial_thesis_panel",
                "editorial_reason_cards",
                "paginated_document",
                "safe_vertical_stack",
                "content_f_shape",
            ],
            "avoid": [
                "radial_compact",
                "editorial_full_bleed_campaign",
                "cover_full_bleed",
                "cover_split_image",
            ],
        },
    )
    decision = pick_pattern(sig)
    assert decision["family"] == "grid"
    assert decision["pattern"] == "image_gallery_grid"


def test_preset_bias_steers_pattern_within_family():
    # When several LLM candidates are in the chosen family, the one that the
    # preset prefers wins over a generic in-family candidate.
    sig = SlideSignal(
        content_type="comparison",
        semantic_family="comparison",
        content_shape="parallel_columns",
        item_count=3,
        text_length=120,
        preset_layout_bias={
            "prefer": ["data_split_metric"],   # horizontal family
            "avoid": [],
        },
    )
    decision = pick_pattern(
        sig,
        llm_candidates=["three_parallel_columns", "data_split_metric"],
    )
    assert decision["family"] == "horizontal"
    assert decision["pattern"] == "data_split_metric"


def test_no_preset_bias_leaves_components_unchanged():
    # Regression guard: when preset_layout_bias is None, no preset:* keys appear
    # and the ranking matches what the §6.1–§6.8 rules produce alone.
    sig = SlideSignal(
        content_type="comparison",
        content_shape="parallel_columns",
        item_count=3,
        text_length=120,
    )
    ranking = score_families(sig)
    for row in ranking:
        for key in row.components:
            assert not key.startswith("preset:"), (
                f"unexpected preset component without bias: {key}"
            )
