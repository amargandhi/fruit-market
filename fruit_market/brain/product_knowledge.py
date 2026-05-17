"""Sales-facing product knowledge for the phone brain.

This is the local, deterministic copy of the fruit knowledge we also
load into Moss for semantic retrieval. The Gemini brain receives these
fields through tool outputs, so it can sound like a good market seller
without inventing varietals, flavor notes, or pairings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductKnowledge:
    variety: str
    short_description: str
    tasting_notes: str
    best_for: str
    ripeness_cues: str
    sales_tip: str
    pairings: str


_PRODUCTS: dict[str, ProductKnowledge] = {
    "apple": ProductKnowledge(
        variety="Cosmic Crisp-style red apple",
        short_description=(
            "A crisp, juicy red apple with a bright sweet-tart finish."
        ),
        tasting_notes=(
            "Crunchy first bite, honeyed sweetness, a clean tart edge, and "
            "enough juice to feel refreshing rather than heavy."
        ),
        best_for=(
            "Eating out of hand, slicing for lunch boxes, pairing with sharp "
            "cheddar, or serving with peanut butter."
        ),
        ripeness_cues=(
            "Look for firm skin, a deep red color, and no soft bruised spots. "
            "These hold their crunch well on the counter for a short demo day."
        ),
        sales_tip=(
            "If someone asks what tastes best, recommend the apples for a "
            "crisp snack: sweet enough for kids, tart enough for adults, and "
            "good with cheese or nut butter."
        ),
        pairings="Sharp cheddar, peanut butter, cinnamon, oats, pork, or arugula.",
    ),
    "banana": ProductKnowledge(
        variety="Cavendish banana",
        short_description=(
            "A mellow, creamy banana with gentle sweetness and a soft tropical aroma."
        ),
        tasting_notes=(
            "Light vanilla sweetness when yellow, richer honey notes as brown "
            "freckles appear, and a smooth texture that works well in smoothies."
        ),
        best_for=(
            "Breakfast, smoothies, lunch boxes, baking banana bread, or a quick "
            "pre-workout snack."
        ),
        ripeness_cues=(
            "Green tips mean firmer and less sweet; yellow is ready for snacking; "
            "brown freckles mean sweeter and better for baking."
        ),
        sales_tip=(
            "If someone wants something soft and sweet, recommend bananas. For "
            "same-day eating, point them to fully yellow fruit; for tomorrow, "
            "choose slightly green tips."
        ),
        pairings="Yogurt, oats, peanut butter, chocolate, berries, or coffee.",
    ),
}


def knowledge_for(name: str) -> ProductKnowledge | None:
    key = name.strip().lower().removesuffix("s")
    return _PRODUCTS.get(key)
