from app.graph.state import _merge_html


def test_empty_html_update_clears_existing_slides():
    assert _merge_html({0: "<html>old</html>"}, {}) == {}


def test_html_update_merges_partial_slide_results():
    assert _merge_html({0: "<html>old</html>"}, {1: "<html>new</html>"}) == {
        0: "<html>old</html>",
        1: "<html>new</html>",
    }
