"""Projections rebuild correct state from a stream of events."""

from __future__ import annotations

from fruit_market.state.events import (
    ActiveItemSet,
    CountSet,
    ItemTaught,
    OrderCancelled,
    OrderPacked,
    OrderPaid,
    OrderReserved,
)
from fruit_market.state.projections import CatalogProjection, OrdersProjection


def test_catalog_projection_replays_teach_active_count() -> None:
    events = [
        (1, ItemTaught(item_id="i1", name="banana", price_cents=100, initial_count=6)),
        (2, ItemTaught(item_id="i2", name="apple", price_cents=150, initial_count=4)),
        (3, ActiveItemSet(item_id="i1")),
        (4, CountSet(item_id="i1", count=5, source="model")),
    ]
    proj = CatalogProjection.hydrate(events)
    assert len(proj.list()) == 2
    assert proj.active() is not None
    assert proj.active().id == "i1"  # type: ignore[union-attr]
    assert proj.get("i1").physical_count == 5  # type: ignore[union-attr]
    assert proj.get("i2").physical_count == 4  # type: ignore[union-attr]


def test_orders_projection_walks_full_lifecycle() -> None:
    events = [
        (
            1,
            OrderReserved(
                order_id="o1",
                item_id="i1",
                qty=2,
                total_cents=200,
                customer_phone="+15551234567",
            ),
        ),
        (2, OrderPaid(order_id="o1", stripe_session_id="cs_test_1")),
        (3, OrderPacked(order_id="o1")),
    ]
    proj = OrdersProjection.hydrate(events)
    o = proj.get("o1")
    assert o is not None
    assert o.status == "packed"
    assert o.stripe_session_id == "cs_test_1"
    assert proj.list_active() == []  # packed is no longer active


def test_orders_projection_handles_cancel_after_paid() -> None:
    events = [
        (
            1,
            OrderReserved(
                order_id="o1",
                item_id="i1",
                qty=1,
                total_cents=100,
                customer_phone="+15551234567",
            ),
        ),
        (2, OrderPaid(order_id="o1", stripe_session_id="cs_test_1")),
        (3, OrderCancelled(order_id="o1", reason="operator")),
    ]
    proj = OrdersProjection.hydrate(events)
    assert proj.get("o1").status == "cancelled"  # type: ignore[union-attr]
    assert proj.list_active() == []
