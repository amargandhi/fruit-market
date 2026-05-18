"""Fast deterministic intent parser for the phone webhook hot path.

When a customer says "I want 2 bananas", we don't need an LLM to
turn that into a purchase — a regex catches 95% of demo phrasings
in microseconds. The webhook responds within ~200 ms ("Got it,
texting you the link now") and spawns the slow Stripe+SMS work
in a background task. Gemini stays in the loop for questions and
chit-chat — anything this parser doesn't recognize falls through
to the LLM brain.

The parser is intentionally narrow: it only matches clear
"buy N {fruit}" phrasings. Ambiguous or conversational phrasings
("what do you have", "can you tell me about apples", "do you sell
blueberries") return None and route to Gemini.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Number words → integer. Covers the common spoken phrasings.
# We deliberately don't go past 12 because larger orders are
# unusual in a fruit-stall demo, and any "twenty-five" style
# compound number drops through to the LLM brain.
_WORD_TO_INT: dict[str, int] = {
    "a": 1, "an": 1, "one": 1, "single": 1,
    "two": 2, "couple": 2, "pair": 2,
    "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "dozen": 12,
}

# Verbs that signal a buy intent. Order matters only insofar as
# a longer phrase wins; the regex below uses alternation.
_BUY_VERBS = (
    "buy", "purchase", "order", "want", "need", "take",
    "grab", "get", "would like", "like", "could i get",
    "can i get", "may i have", "can i have", "i'd like",
    "id like", "i would like",
    # Bare "have" is intentionally NOT in this list — "I already
    # have 3 apples" is a statement of fact, not a buy. Longer
    # phrases like "can i have" / "may i have" are explicit and
    # safe.
)

# Fruit nouns we know how to fulfil. Singular + plural variants.
# Keep this in sync with the seeded catalog + product_knowledge.
_FRUIT_NOUNS = (
    "apple", "apples",
    "banana", "bananas", "nana", "nanas",
    "orange", "oranges",
    "lemon", "lemons",
    "pear", "pears",
    "grape", "grapes",
    "strawberry", "strawberries", "berry", "berries",
    "cherry", "cherries",
    "peach", "peaches",
    "watermelon", "watermelons",
    "pineapple", "pineapples",
    "mango", "mangos", "mangoes",
    "avocado", "avocados", "avo", "avos",
    "kiwi", "kiwis",
    "plum", "plums",
)

# Pre-compiled patterns. The first group captures the quantity
# (number or word); the second captures the fruit noun. The
# verb prefix is optional so "two bananas please" works as well
# as "I want two bananas".
_QTY_PATTERN = r"(?P<qty>\d+|" + "|".join(_WORD_TO_INT.keys()) + r")"
_FRUIT_PATTERN = r"(?P<fruit>" + "|".join(_FRUIT_NOUNS) + r")"
_BUY_PREFIX = r"(?:" + "|".join(re.escape(v) for v in _BUY_VERBS) + r")\s+(?:me\s+|some\s+)?"

_PURCHASE_RE = re.compile(
    # Optional buy-verb prefix, then quantity, optional filler
    # ("of", "of those", "more", "fresh", "nice"), then fruit noun.
    rf"\b(?:{_BUY_PREFIX})?{_QTY_PATTERN}\s+"
    rf"(?:(?:of\s+)?(?:those|them|the|more|fresh|nice)\s+|of\s+)?"
    rf"{_FRUIT_PATTERN}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Purchase:
    """A successfully-parsed purchase intent."""

    item_name: str   # canonical singular form ("banana", "apple")
    qty: int         # > 0

    @property
    def label(self) -> str:
        """Human-readable quantity + item, e.g. ``"2 bananas"``."""

        suffix = "" if self.qty == 1 else "s"
        return f"{self.qty} {self.item_name}{suffix}"


def detect_purchase(transcript: str) -> Purchase | None:
    """Return a :class:`Purchase` if the transcript clearly asks
    to buy N fruit, else None.

    Examples that match:
        "I want 2 bananas"
        "Can I get 3 apples please?"
        "Grab me a couple of oranges"
        "Two pears, please"
        "I'd like one avocado"

    Examples that DON'T match (deliberately — these route to Gemini):
        "What do you have today?"
        "How much are the apples?"
        "What's the freshest fruit?"
        "Do you sell pineapples?"        (asks availability, not buying)
        "Tell me about the bananas"
    """

    if not transcript or not transcript.strip():
        return None

    # Reject questions outright — "do you have", "what's", "how much",
    # "are these" — these need an LLM to answer, not a checkout.
    lowered = transcript.strip().lower()
    if any(lowered.startswith(q) for q in (
        "what ", "what's", "whats", "how ", "how's", "hows",
        "where ", "when ", "why ", "who ", "tell me", "can you tell",
        "do you have", "do you sell", "do you stock",
        "are these", "are they", "are those",
    )):
        return None

    # Reject statements of fact masquerading as purchase intent.
    # "I already have 3 apples" is not a buy; "I had 2 yesterday" isn't either.
    if any(p in lowered for p in (
        "i already have", "i have already", "already have",
        "i had ", "i'd had", "i ate", "i bought",
    )):
        return None

    match = _PURCHASE_RE.search(transcript)
    if match is None:
        return None

    qty_raw = match.group("qty")
    qty = int(qty_raw) if qty_raw.isdigit() else _WORD_TO_INT.get(qty_raw.lower(), 0)
    if qty <= 0:
        return None

    fruit_raw = match.group("fruit").lower()
    item_name = _canonical_fruit(fruit_raw)
    return Purchase(item_name=item_name, qty=qty)


def _canonical_fruit(raw: str) -> str:
    """Map a recognised noun (singular or plural / alias) to the
    canonical singular catalog name."""

    aliases = {
        "nana": "banana", "nanas": "banana",
        "berry": "strawberry", "berries": "strawberry",
        "strawberries": "strawberry",
        "cherries": "cherry",
        "peaches": "peach",
        "mangoes": "mango", "mangos": "mango",
        "avo": "avocado", "avos": "avocado", "avocados": "avocado",
        "kiwis": "kiwi",
        "plums": "plum",
    }
    if raw in aliases:
        return aliases[raw]
    # Naive de-pluralisation (strip trailing 's') for the rest.
    return raw.removesuffix("s")
