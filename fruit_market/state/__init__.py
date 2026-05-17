"""Append-only event log + projection layer.

The event store is the single source of truth. Every state change in
the system (teach, count, reserve, paid, packed, restock_ordered) is
appended as one event. Catalog / inventory / orders state are pure
projections over the log — they can always be replayed from scratch.

Phase 1 only exposes the event types. The SQLite store and the
projections themselves land in Track A (Phase 2).
"""

from fruit_market.state.events import (
    ActiveItemSet,
    CountSet,
    Event,
    ItemTaught,
    OrderCancelled,
    OrderPacked,
    OrderPaid,
    OrderReserved,
    RestockApproved,
    RestockOperatorEmailed,
    RestockOperatorEmailFailed,
    RestockOrdered,
    RestockPaymentFailed,
    RestockPaymentStarted,
    RestockProposed,
    RestockReceived,
    RestockRejected,
    RestockSpongePlanSubmitted,
    StockLow,
)

__all__ = [
    "ActiveItemSet",
    "CountSet",
    "Event",
    "ItemTaught",
    "OrderCancelled",
    "OrderPacked",
    "OrderPaid",
    "OrderReserved",
    "RestockApproved",
    "RestockOrdered",
    "RestockOperatorEmailFailed",
    "RestockOperatorEmailed",
    "RestockPaymentFailed",
    "RestockPaymentStarted",
    "RestockProposed",
    "RestockReceived",
    "RestockRejected",
    "RestockSpongePlanSubmitted",
    "StockLow",
]
