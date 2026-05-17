"""Read-side projections over the event log.

Each projection holds an in-memory view derived from the events.
Projections never write events — they only read. Services own the
write side and call ``apply()`` after the store commits.

Two projections cover the MVP:

* :class:`CatalogProjection` — items by id, plus the active item.
  Inventory counts live on the ``Item`` record itself (updated by
  ``CountSet``), so there's no separate inventory projection.
* :class:`OrdersProjection` — orders by id, plus a list of the
  active (not packed, not cancelled) orders for the kiosk board.

Both projections expose a :meth:`hydrate` classmethod that replays
the entire log to rebuild state — used at startup, and a useful
self-test for the SSE reconnect path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from fruit_market.services.protocols import Item, Order
from fruit_market.state.events import (
    ActiveItemSet,
    CountSet,
    Event,
    ItemTaught,
    OrderCancelled,
    OrderPacked,
    OrderPaid,
    OrderReserved,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class CatalogProjection:
    """Items by id + which one is active for the vision watcher."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._active_id: str | None = None

    @classmethod
    def hydrate(cls, events: Iterable[tuple[int, Event]]) -> CatalogProjection:
        proj = cls()
        for _, ev in events:
            proj.apply(ev)
        return proj

    def apply(self, event: Event) -> None:
        if isinstance(event, ItemTaught):
            self._items[event.item_id] = Item(
                id=event.item_id,
                name=event.name,
                price_cents=event.price_cents,
                physical_count=event.initial_count,
                reorder_threshold=event.reorder_threshold,
            )
        elif isinstance(event, ActiveItemSet):
            # Tolerate ActiveItemSet for an item we haven't seen yet:
            # the catalog might be hydrating out of order during tests.
            # Real flow always taughts → active in that order.
            self._active_id = event.item_id
        elif isinstance(event, CountSet):
            existing = self._items.get(event.item_id)
            if existing is not None:
                self._items[event.item_id] = replace(
                    existing, physical_count=event.count
                )

    # ─── read API ─────────────────────────────────────────────────

    def get(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def list(self) -> list[Item]:
        return list(self._items.values())

    def active(self) -> Item | None:
        if self._active_id is None:
            return None
        return self._items.get(self._active_id)

    def active_id(self) -> str | None:
        return self._active_id


class OrdersProjection:
    """Orders by id."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    @classmethod
    def hydrate(cls, events: Iterable[tuple[int, Event]]) -> OrdersProjection:
        proj = cls()
        for _, ev in events:
            proj.apply(ev)
        return proj

    def apply(self, event: Event) -> None:
        if isinstance(event, OrderReserved):
            self._orders[event.order_id] = Order(
                id=event.order_id,
                item_id=event.item_id,
                qty=event.qty,
                total_cents=event.total_cents,
                status="reserved",
                customer_phone=event.customer_phone,
                stripe_session_id=None,
            )
        elif isinstance(event, OrderPaid):
            existing = self._orders.get(event.order_id)
            if existing is not None:
                self._orders[event.order_id] = replace(
                    existing,
                    status="paid",
                    stripe_session_id=event.stripe_session_id,
                )
        elif isinstance(event, OrderPacked):
            existing = self._orders.get(event.order_id)
            if existing is not None:
                self._orders[event.order_id] = replace(existing, status="packed")
        elif isinstance(event, OrderCancelled):
            existing = self._orders.get(event.order_id)
            if existing is not None:
                self._orders[event.order_id] = replace(existing, status="cancelled")

    # ─── read API ─────────────────────────────────────────────────

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list_all(self) -> list[Order]:
        return list(self._orders.values())

    def list_active(self) -> list[Order]:
        return [
            o for o in self._orders.values() if o.status not in ("packed", "cancelled")
        ]
