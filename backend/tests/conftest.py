from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_glm_ocr_env(monkeypatch: pytest.MonkeyPatch):
    """Default: tests run with GLM-OCR unconfigured so the zenmux fallback path
    is exercised. Tests that want to cover the GLM-OCR branch should set these
    env vars explicitly via their own monkeypatch.setenv calls.
    """
    for key in ("OSZ_MODEL_OCR", "OSZ_MODEL_OCR_BASE_URL", "OSZ_MODEL_OCR_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OSZ_TEST_OWNER_TOKEN", "test-owner-token-for-direct-endpoint-calls")
