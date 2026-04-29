from __future__ import annotations

from app.catalog.scenarios import SCENARIO_DEFINITIONS, structures_for
from app.catalog.structures import STRUCTURE_DEFINITIONS, list_structures
from app.graph.nodes import outline


def test_gallery_catalog_entries_are_exposed():
    scenario = next(s for s in SCENARIO_DEFINITIONS if s["id"] == "gallery")
    structure = STRUCTURE_DEFINITIONS["gallery"]

    assert scenario["name_en"] == "Gallery"
    assert scenario["name_zh"] == "图集相册"
    assert structures_for("gallery") == ["gallery"]
    assert structure["name_en"] == "Gallery"
    assert not structure.get("legacy")
    assert "gallery" in {s["id"] for s in list_structures()}


def test_gallery_outline_prompt_requests_image_slots(monkeypatch):
    captured: dict[str, object] = {}

    def fake_chat_structured(_model, messages, schema, **_kwargs):
        captured["messages"] = messages
        return schema(
            language="en",
            summary="gallery",
            slides=[
                {
                    "title": "Opening Album",
                    "role": "cover",
                    "bullets": ["Collection context"],
                    "image_slots": ["wide cover photo of the collection"],
                },
                {
                    "title": "Closing",
                    "role": "closing",
                    "bullets": ["Final caption"],
                },
            ],
        )

    monkeypatch.setattr(outline.zenmux, "chat_structured", fake_chat_structured)

    result = outline.outline_node({
        "scenario_id": "gallery",
        "structure_id": "gallery",
        "expected_pages": 2,
        "materials": [{"parsed": "A small travel album with captions."}],
    })

    joined = "\n\n".join(str(message["content"]) for message in captured["messages"])
    assert "Gallery-specific requirements" in joined
    assert "populate image_slots" in joined
    assert "Keep image-slot instructions out of bullets" in joined
    assert result["outline_slides"][0]["image_slots"] == [
        "wide cover photo of the collection"
    ]
    assert "_image_slots:_" in result["outline_md"]
