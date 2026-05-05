from app.graph.state import _merge_html, _merge_html_generation_metadata


def test_empty_html_update_clears_existing_slides():
    assert _merge_html({0: "<html>old</html>"}, {}) == {}


def test_html_update_merges_partial_slide_results():
    assert _merge_html({0: "<html>old</html>"}, {1: "<html>new</html>"}) == {
        0: "<html>old</html>",
        1: "<html>new</html>",
    }


def test_empty_html_metadata_update_clears_existing_metadata():
    assert _merge_html_generation_metadata({0: {"status": "succeeded"}}, {}) == {}


def test_html_metadata_update_merges_partial_slide_results():
    assert _merge_html_generation_metadata(
        {0: {"status": "succeeded"}},
        {1: {"status": "failed"}},
    ) == {
        0: {"status": "succeeded"},
        1: {"status": "failed"},
    }


def test_html_metadata_update_deletes_one_slide():
    assert _merge_html_generation_metadata(
        {0: {"status": "succeeded"}, 1: {"status": "failed"}},
        {1: None},
    ) == {0: {"status": "succeeded"}}
