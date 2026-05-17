"""Teach transcript parser — heuristic but well-bounded."""

from __future__ import annotations

import pytest

from fruit_market.services.teach import parse_transcript


@pytest.mark.parametrize(
    ("transcript", "name", "price_cents", "count"),
    [
        ("These are bananas, $1.00, 6 of them", "banana", 100, 6),
        ("These are apples, $1.50, 4 of them", "apple", 150, 4),
        ("got some mangoes for $2.75, 7 left", "mango", 275, 7),
        ("this is a mango for $2", "mango", 200, 0),
        ("got 5 oranges for $0.75 each", "orange", 75, 5),
        ("we have 8 lemons left", "lemon", 0, 8),
        ("This is an apple, 2 dollars, 3 total", "apple", 200, 3),
    ],
)
def test_known_phrases(
    transcript: str, name: str, price_cents: int, count: int
) -> None:
    parsed_name, parsed_price, parsed_count = parse_transcript(transcript)
    assert parsed_name == name
    assert parsed_price == price_cents
    assert parsed_count == count


def test_unparseable_falls_back_to_defaults() -> None:
    name, price, count = parse_transcript("aaaaaaaaa zzzz")
    # We accept any reasonable defaults; the kiosk lets the
    # operator edit before confirming.
    assert isinstance(name, str)
    assert price == 0
    assert count == 0


def test_price_does_not_pollute_count() -> None:
    """Regression: ``$1.50`` digits must not be picked up as the count."""

    _, price, count = parse_transcript("apples $1.50, 6 of them")
    assert price == 150
    assert count == 6
