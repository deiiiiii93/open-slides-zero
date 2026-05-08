"""ZenMux image generation adapter.

ZenMux exposes image generation through its Vertex-AI-compatible endpoint.
This module keeps the dependency lazy so normal deck generation and tests do
not import the image SDK unless the user explicitly confirms a generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import get_model
from .runtime_config import (
    effective_zenmux_vertex_base_url,
    require_zenmux_api_key,
)

ZENMUX_VERTEX_BASE_URL = "https://zenmux.ai/api/vertex-ai"
ZENMUX_API_KEY_ENV = "ZENMUX_API_KEY"


def generate_image(
    prompt: str,
    output_path: str | Path,
    *,
    model: str | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """Generate one image and save it to ``output_path``.

    The caller owns user confirmation. This function should only be called
    after the user has reviewed and submitted the prompt.
    """
    key = require_zenmux_api_key()

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - exercised only without deps installed
        raise RuntimeError(
            "Image generation requires google-genai. Reinstall backend dependencies."
        ) from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    client = genai.Client(
        vertexai=True,
        api_key=key,
        http_options=types.HttpOptions(
            api_version="v1",
            base_url=effective_zenmux_vertex_base_url(),
        ),
    )
    response = client.models.generate_images(
        model=model or get_model("image_gen"),
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio=aspect_ratio,
            output_mime_type="image/png",
        ),
    )
    generated = getattr(response, "generated_images", None) or []
    if not generated:
        raise RuntimeError("Image model returned no images.")

    generated[0].image.save(str(output))
    return {
        "path": str(output),
        "mime_type": "image/png",
        "model": model or get_model("image_gen"),
    }
