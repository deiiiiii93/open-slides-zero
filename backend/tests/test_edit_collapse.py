"""Edit-op collapse tests — verifies multi-intent comments fold correctly."""

from app.graph.nodes.edit import collapse_edit_ops


def test_single_op_round_trips():
    ops = [{
        "target_stage": "html",
        "rationale": "typo",
        "affected_slides": [2],
        "patch_fragment": {},
    }]
    stage, patch, affected = collapse_edit_ops(ops)
    assert stage == "html"
    assert patch == {}
    assert affected == [2]


def test_compound_comment_collapses_to_earliest_stage():
    ops = [
        {"target_stage": "style",  "patch_fragment": {"visual_style_preference": "navy"},
         "affected_slides": [], "rationale": "palette"},
        {"target_stage": "layout", "patch_fragment": {},
         "affected_slides": [5], "rationale": "switch slide 5 to timeline"},
    ]
    stage, patch, affected = collapse_edit_ops(ops)
    # Earliest wins → style
    assert stage == "style"
    assert patch == {"visual_style_preference": "navy"}
    assert affected == [5]


def test_merging_patch_fragments():
    ops = [
        {"target_stage": "outline", "patch_fragment": {"expected_pages": 10}},
        {"target_stage": "outline", "patch_fragment": {"language": "en"}},
    ]
    stage, patch, _ = collapse_edit_ops(ops)
    assert stage == "outline"
    assert patch == {"expected_pages": 10, "language": "en"}
