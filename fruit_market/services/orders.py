"""Real OrdersService.

Reserves stock atomically: a SQLite ``BEGIN IMMEDIATE`` transaction
holds the write lock while we (a) re-read the projection's
physical_count, (b) check it's >= qty, (c) append
``OrderReserved`` + ``CountSet`` together. Two concurrent reservers
of the last banana serialize through this lock; one wins, the other
sees the lowered count and raises.

``mark_paid`` is idempotent on ``(order_id, stripe_session_id)`` —
Stripe will retry the webhook on transient failures, and we never
want to double-count revenue.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fruit_market.state.events import (
    CancelReason,
    CountSet,
    OrderCancelled,
    OrderPacked,
    OrderPaid,
    OrderReserved,
)

if TYPE_CHECKING:
    from fruit_market.services.protocols import Order
    from fruit_market.state.projections import CatalogProjection, OrdersProjection
    from fruit_market.state.store import EventStore


def _new_order_id() -> str:
    return f"ord_{uuid.uuid4().hex[:10]}"


class RealOrdersService:
    def __init__(
        self,
        store: EventStore,
        catalog: CatalogProjection,
        orders: OrdersProjection,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._orders = orders

    # ─── reservation (the hottest path) ─────────────────────────

    def reserve(self, item_id: str, qty: int, customer_phone: str) -> Order:
        if qty <= 0:
            raise ValueError(f"qty must be > 0; got {qty}")

        order_id = _new_order_id()
        # The transaction takes the writer lock; concurrent calls
        # serialize. Inside the lock we re-read the projection
        # (which was updated by the previous committer's subscriber
        # callback) so the count check is current.
        with self._store.transaction() as tx:
            item = self._catalog.get(item_id)
            if item is None:
                raise ValueError(f"unknown item: {item_id}")
            if item.physical_count < qty:
                raise ValueError(
                    f"not enough stock: have {item.physical_count}, need {qty}"
                )
            total_cents = item.price_cents * qty
            tx.append(
                OrderReserved(
                    order_id=order_id,
                    item_id=item_id,
                    qty=qty,
                    total_cents=total_cents,
                    customer_phone=customer_phone,
                )
            )
            tx.append(
                CountSet(
                    item_id=item_id,
                    count=item.physical_count - qty,
                    source="manual",
                )
            )

        # Projection has been updated by the subscriber callback by
        # the time we get here; return the freshly-projected order.
        order = self._orders.get(order_id)
        assert order is not None, "OrdersProjection didn't apply OrderReserved"
        return order

    # ─── lifecycle ──────────────────────────────────────────────

    def mark_paid(self, order_id: str, stripe_session_id: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        # Idempotent: if we've already recorded a paid event for this
        # stripe_session_id on this order, do nothing.
        if (
            order.status == "paid"
            and order.stripe_session_id == stripe_session_id
        ):
            return
        if order.status not in ("reserved", "paid"):
            raise ValueError(
                f"cannot mark {order.status} order as paid: {order_id}"
            )
        self._store.append(
            OrderPaid(order_id=order_id, stripe_session_id=stripe_session_id)
        )

    def mark_packed(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        if order.status != "paid":
            raise ValueError(
                f"cannot pack {order.status} order: {order_id} (only paid orders pack)"
            )
        self._store.append(OrderPacked(order_id=order_id))

    def cancel(self, order_id: str, reason: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        if order.status not in ("reserved", "paid"):
            # Already terminal — no-op.
            return
        # Return stock if the order had been holding any.
        with self._store.transaction() as tx:
            if order.status == "reserved":
                item = self._catalog.get(order.item_id)
                if item is not None:
                    tx.append(
                        CountSet(
                            item_id=order.item_id,
                            count=item.physical_count + order.qty,
                            source="manual",
                        )
                    )
            tx.append(
                OrderCancelled(
                    order_id=order_id,
                    reason=_coerce_reason(reason),
                )
            )

    # ─── reads ──────────────────────────────────────────────────

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list_active(self) -> list[Order]:
        return self._orders.list_active()


def _coerce_reason(reason: str) -> CancelReason:
    if reason == "timeout":
        return "timeout"
    if reason == "customer_request":
        return "customer_request"
    if reason == "out_of_stock":
        return "out_of_stock"
    return "operator"
