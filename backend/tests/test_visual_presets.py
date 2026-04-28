from app.api import decks


def test_catalog_exposes_visual_style_presets():
    catalog = decks.get_catalog("any-thread")
    presets = catalog["visual_style_presets"]

    assert [preset["id"] for preset in presets] == [
        "product_clarity",
        "editorial_authority",
        "strategic_prestige",
        "cultural_luxury",
        "design_portfolio_expression",
        "cartoon_fairytale_worlds",
    ]
    assert [preset["label"] for preset in presets] == [
        "Product Clarity",
        "Editorial Authority",
        "Strategic Prestige",
        "Cultural Luxury",
        "Design-Portfolio Expression",
        "Cartoon / Fairytale Worlds",
    ]
    assert all(preset["description"] for preset in presets)
