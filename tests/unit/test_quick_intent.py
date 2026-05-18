"""Tests for the deterministic purchase-intent parser.

The webhook's hot path depends on ``detect_purchase`` correctly
classifying clear "buy N {fruit}" phrasings AND letting questions
fall through to the LLM brain. Both directions are tested below.
"""

from __future__ import annotations

import pytest

from fruit_market.brain.quick_intent import detect_purchase


@pytest.mark.parametrize(
    ("transcript", "expected_item", "expected_qty"),
    [
        ("I want 2 bananas",                  "banana", 2),
        ("Can I get 3 apples please?",        "apple",  3),
        ("Could I get 2 oranges",             "orange", 2),
        ("Buy me a pear",                     "pear",   1),
        ("I'd like one avocado",              "avocado", 1),
        ("Order two mangos",                  "mango",  2),
        ("Get me 5 lemons",                   "lemon",  5),
        ("Take 4 apples",                     "apple",  4),
        ("Grab 6 grapes please",              "grape",  6),
        # Singular noun, "a" as qty
        ("Can I have a banana?",              "banana", 1),
        # Number word + plural alias
        ("Two nanas please",                  "banana", 2),
        # Berry alias
        ("Three strawberries",                "strawberry", 3),
        # Couple / pair
        ("Grab me a couple of apples",        "apple",  2),
        # Filler words
        ("I want 3 of those bananas",         "banana", 3),
        # Implicit qty=1 with "an"
        ("I want an apple",                   "apple",  1),
    ],
)
def test_detect_purchase_matches_clear_buy_intent(
    transcript: str, expected_item: str, expected_qty: int,
) -> None:
    result = detect_purchase(transcript)
    assert result is not None, f"expected match for {transcript!r}"
    assert result.item_name == expected_item
    assert result.qty == expected_qty


@pytest.mark.parametrize(
    "transcript",
    [
        # Questions route to Gemini
        "What do you have today?",
        "How much for two apples?",
        "Do you have lemons?",
        "Do you sell bananas?",
        "Tell me about the strawberries",
        "What's the freshest fruit?",
        # Greetings / chit-chat
        "Hi there",
        "Hello",
        "Good morning",
        # Empty / whitespace
        "",
        "   ",
        # No fruit mentioned
        "I want a cake",
        "Two cars please",
        # Number without fruit
        "I want 5 things",
        # Fruit without quantity word
        "Apples are delicious",
        "I love bananas",
        # Zero / weird qty
        "Zero apples",
        # Statements not requests
        "I already have 3 apples",
    ],
)
def test_detect_purchase_returns_none_for_non_purchases(
    transcript: str,
) -> None:
    assert detect_purchase(transcript) is None


def test_purchase_label_singular_vs_plural() -> None:
    from fruit_market.brain.quick_intent import Purchase

    assert Purchase("apple", 1).label == "1 apple"
    assert Purchase("apple", 3).label == "3 apples"
    assert Purchase("banana", 1).label == "1 banana"
    assert Purchase("banana", 5).label == "5 bananas"
