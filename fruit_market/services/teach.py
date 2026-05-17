"""Real TeachService.

Heuristic parser for free-text teach transcripts. Better than the
stub: handles "$1.50 each", "for $1.50", "1 dollar 50 cents",
"6 of them", "I have 6", "got 6 left", and a few common name
patterns ("these are apples", "this is an apple", "got some
mangoes"). On a parse miss it returns a proposal with sensible
defaults that the operator can edit on the kiosk before confirming.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from fruit_market.services.protocols import Item, TeachProposal
from fruit_market.state.events import ActiveItemSet, ItemTaught

if TYPE_CHECKING:
    from fruit_market.state.projections import CatalogProjection
    from fruit_market.state.store import EventStore


# ─── Parsers ────────────────────────────────────────────────────────


_PRICE_RES: tuple[re.Pattern[str], ...] = (
    # "$1.50", "$1"
    re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)"),
    # "1.50 dollars", "2 dollars"
    re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:dollars?|bucks?)", re.IGNORECASE),
)

_COUNT_RES: tuple[re.Pattern[str], ...] = (
    # "6 of them", "5 left", "8 in stock", "3 pieces", "4 total"
    re.compile(
        r"\b(\d+)\b\s*(?:of\s+them|left|in\s+stock|pieces?|total|units?)",
        re.IGNORECASE,
    ),
    # "I have 6", "we have 6", "got 5"
    re.compile(r"\b(?:i\s+have|we\s+have|got|there\s+are)\s+(\d+)\b", re.IGNORECASE),
)

_NAME_RES: tuple[re.Pattern[str], ...] = (
    # "these are apples", "this is an apple", "this is a banana"
    re.compile(
        r"(?:these\s+are\s+|this\s+is\s+(?:an?\s+)?|these\s+)([a-z][a-z\s]*?)"
        r"(?=\s*(?:[,.]|\$|\bfor\b|\bat\b|\bare\b|\b\d|$))",
        re.IGNORECASE,
    ),
    # "got 5 oranges", "we have 8 lemons left", "have some apples"
    re.compile(
        r"(?:(?:i|we)\s+have|got|have)\s+(?:\d+\s+)?(?:some\s+|a\s+few\s+)?"
        r"([a-z][a-z\s]*?)"
        r"(?=\s*(?:[,.]|\$|\bfor\b|\bat\b|\bleft\b|\bin\s+stock\b|\b\d|$))",
        re.IGNORECASE,
    ),
)


def parse_transcript(transcript: str) -> tuple[str, int, int]:
    """Parse free text into ``(name, price_cents, initial_count)``.

    All three default sensibly on a miss: ``name='item'``,
    ``price_cents=0``, ``initial_count=0``. Callers can still
    confirm the proposal — the kiosk lets the operator edit it.
    """

    text = transcript.strip()

    # Price first; we strip the match so the count regex doesn't see
    # the dollar amount as an integer count.
    price_cents = 0
    price_match: re.Match[str] | None = None
    for pat in _PRICE_RES:
        m = pat.search(text)
        if m:
            price_cents = round(float(m.group(1)) * 100)
            price_match = m
            break
    text_for_count = text.replace(price_match.group(0), " ") if price_match else text

    initial_count = 0
    for pat in _COUNT_RES:
        m = pat.search(text_for_count)
        if m:
            initial_count = int(m.group(1))
            break

    name = "item"
    for pat in _NAME_RES:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip().lower()
            # Trim trailing filler words that the lookahead would
            # have caught individually.
            raw = re.sub(r"\s+(?:and|or|that|which)$", "", raw)
            name = _singularize_name(raw) or "item"
            break

    return name, price_cents, initial_count


def _singularize_name(raw: str) -> str:
    words = raw.split()
    if not words:
        return raw
    last = words[-1]
    if last.endswith("ies") and len(last) > 4:
        last = f"{last[:-3]}y"
    elif (
        last.endswith(("ches", "shes"))
        and len(last) > 5
        or last.endswith(("xes", "zes", "ses", "oes"))
        and len(last) > 4
    ):
        last = last[:-2]
    elif last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        last = last[:-1]
    words[-1] = last
    return " ".join(words)


# ─── Service ────────────────────────────────────────────────────────


def _new_proposal_id() -> str:
    return f"prop_{uuid.uuid4().hex[:10]}"


def _new_item_id() -> str:
    return f"item_{uuid.uuid4().hex[:10]}"


class RealTeachService:
    """Teach proposals live in memory only; they're ephemeral until
    confirmed (at which point the ItemTaught event makes them
    durable). Rejecting just discards the in-memory entry."""

    def __init__(self, store: EventStore, catalog: CatalogProjection) -> None:
        self._store = store
        self._catalog = catalog
        self._proposals: dict[str, TeachProposal] = {}

    def propose(self, transcript: str) -> TeachProposal:
        name, price_cents, initial_count = parse_transcript(transcript)
        proposal = TeachProposal(
            id=_new_proposal_id(),
            name=name,
            price_cents=price_cents,
            initial_count=initial_count,
            reorder_threshold=2,
        )
        self._proposals[proposal.id] = proposal
        return proposal

    def confirm(self, proposal_id: str) -> Item:
        proposal = self._proposals.pop(proposal_id, None)
        if proposal is None:
            raise KeyError(f"unknown proposal: {proposal_id}")
        item_id = _new_item_id()
        with self._store.transaction() as tx:
            tx.append(
                ItemTaught(
                    item_id=item_id,
                    name=proposal.name,
                    price_cents=proposal.price_cents,
                    initial_count=proposal.initial_count,
                    reorder_threshold=proposal.reorder_threshold,
                )
            )
            tx.append(ActiveItemSet(item_id=item_id))
        item = self._catalog.get(item_id)
        assert item is not None, "CatalogProjection didn't apply ItemTaught"
        return item

    def reject(self, proposal_id: str) -> None:
        self._proposals.pop(proposal_id, None)
