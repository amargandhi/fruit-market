"""Sales-knowledge lookup unit tests.

These exercise the canonical fruit table + alias resolution. They
exist so the phone agent (step 9 of the demo chain) never gives a
judge a blank "I don't know about that fruit" answer for anything
on the standard fruit-stall menu.
"""

from __future__ import annotations

from fruit_market.brain.product_knowledge import knowledge_for, known_fruits


def test_known_fruits_covers_full_stall_menu() -> None:
    """The stall demo should be able to talk about more than just
    apples + bananas — judges may ask about oranges, peaches, etc."""

    names = set(known_fruits())
    # The 13 fruits the phone agent must always have details for.
    assert names >= {
        "apple", "banana", "orange", "lemon", "pear", "grape",
        "strawberry", "cherry", "peach", "watermelon", "pineapple",
        "mango", "avocado",
    }


def test_canonical_lookup_returns_full_record() -> None:
    apple = knowledge_for("apple")
    assert apple is not None
    assert "red apple" in apple.variety.lower()
    assert apple.tasting_notes
    assert apple.pairings


def test_plural_lookup_strips_trailing_s() -> None:
    assert knowledge_for("oranges") is not None
    assert knowledge_for("lemons") is not None


def test_alias_lookup_resolves_to_canonical() -> None:
    """The phone agent often hears playful or shorthand names —
    'nanas' → 'banana', 'avo' → 'avocado', 'berries' → 'strawberry'."""

    assert knowledge_for("nanas").variety == knowledge_for("banana").variety
    assert knowledge_for("avo").variety == knowledge_for("avocado").variety
    assert knowledge_for("berries").variety == knowledge_for("strawberry").variety
    assert knowledge_for("mangos").variety == knowledge_for("mango").variety
    assert knowledge_for("strawberries").variety == knowledge_for("strawberry").variety


def test_unknown_fruit_returns_none() -> None:
    assert knowledge_for("durian") is None
    assert knowledge_for("") is None
    assert knowledge_for("   ") is None


def test_case_insensitive_lookup() -> None:
    assert knowledge_for("APPLE") is not None
    assert knowledge_for("Apple") is not None
    assert knowledge_for("  apple  ") is not None
