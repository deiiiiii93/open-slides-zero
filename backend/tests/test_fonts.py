"""Font conversion endpoint tests."""

from fastapi.testclient import TestClient

from app.api import fonts
from app.main import app


def test_font_as_ttf_route_matches_frontend_proxy(monkeypatch):
    monkeypatch.setattr(fonts, "_woff2_to_ttf", lambda blob: b"ttf-bytes")

    client = TestClient(app)
    res = client.post("/font-as-ttf", content=b"wOF2fake-font")

    assert res.status_code == 200
    assert res.content == b"ttf-bytes"
    assert res.headers["content-type"] == "font/ttf"
