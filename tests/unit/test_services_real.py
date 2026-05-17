"""Real services backed by a temporary SQLite event store.

These tests exercise the real (factory-built) Services bundle to
prove the end-to-end flow lands on disk and replays correctly.
The bundle replaces the in-memory stubs the contract tests use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fruit_market.services.factory import make_real_services
from fruit_market.state.store import EventStore

if TYPE_CHECKING:
    from pathlib import Path

    from fruit_market.services.protocols import Services


@pytest.fixture
def real_services(tmp_path: Path) -> Services:
    store = EventStore(tmp_path / "events.db")
    return make_real_services(store=store)


def test_teach_creates_item_and_sets_active(real_services: Services) -> None:
    proposal = real_services.teach.propose(
        "These are bananas, $1.00, 6 of them"
    )
    item = real_services.teach.confirm(proposal.id)
    assert item.name == "banana"
    assert item.price_cents == 100
    assert item.physical_count == 6
    active = real_services.catalog.get_active_item()
    assert active is not None
    assert active.id == item.id


def test_quote_uses_catalog_price(real_services: Services) -> None:
    proposal = real_services.teach.propose("These are apples, $1.50, 4 of them")
    item = real_services.teach.confirm(proposal.id)
    assert real_services.pricing.quote(item.id, 3) == 450


def test_reserve_decrements_inventory_atomically(real_services: Services) -> None:
    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    order = real_services.orders.reserve(item.id, 2, "+15551234567")
    assert order.status == "reserved"
    assert real_services.inventory.get_physical_count(item.id) == 4


def test_reserve_rejects_overdraw(real_services: Services) -> None:
    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    with pytest.raises(ValueError, match="not enough stock"):
        real_services.orders.reserve(item.id, 99, "+15551234567")


def test_mark_paid_is_idempotent_for_same_session(real_services: Services) -> None:
    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    order = real_services.orders.reserve(item.id, 2, "+15551234567")
    real_services.orders.mark_paid(order.id, "cs_test_X")
    real_services.orders.mark_paid(order.id, "cs_test_X")  # idempotent retry
    assert real_services.orders.get(order.id).status == "paid"  # type: ignore[union-attr]


def test_cancel_returns_stock(real_services: Services) -> None:
    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    order = real_services.orders.reserve(item.id, 2, "+15551234567")
    assert real_services.inventory.get_physical_count(item.id) == 4
    real_services.orders.cancel(order.id, reason="timeout")
    assert real_services.inventory.get_physical_count(item.id) == 6
    assert real_services.orders.get(order.id).status == "cancelled"  # type: ignore[union-attr]


def test_inventory_reconcile_emits_stock_low_below_threshold(
    real_services: Services, tmp_path: Path
) -> None:
    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    # Reorder threshold defaults to 2 in the parser; descend to 2.
    real_services.inventory.reconcile_physical_count(
        item_id=item.id, count=2, source="model", confidence=0.9
    )
    # Re-open store and replay to count StockLow events.
    store = EventStore(tmp_path / "events.db")
    types = [type(e).__name__ for _, e in store.replay()]
    assert types.count("StockLow") == 1


def test_full_call_through_pack(real_services: Services) -> None:
    """End-to-end happy path the demo will run on stage."""

    item = real_services.teach.confirm(
        real_services.teach.propose("These are bananas, $1.00, 6 of them").id
    )
    quoted = real_services.pricing.quote(item.id, 3)
    assert quoted == 300

    order = real_services.orders.reserve(item.id, 3, "+15551234567")
    real_services.orders.mark_paid(order.id, "cs_test_demo")
    real_services.orders.mark_packed(order.id)

    final = real_services.orders.get(order.id)
    assert final is not None
    assert final.status == "packed"
    assert final.total_cents == quoted
    assert real_services.inventory.get_physical_count(item.id) == 3
