"""Event types for the Fruit Market event log.

Every state change in the system is one of the types below. Each
event is a Pydantic model with:

* a string ``type`` discriminator used by the JSON-tagged-union
  serializer in the SQLite store,
* a UTC ``ts`` timestamp that the store sets at append time,
* whatever payload fields make sense for the event.

Money is **integer cents** everywhere. Never floats.

Adding a new event:

1. Add a ``Literal["..."]`` ``type`` field and the payload fields.
2. Add the class to the ``Event`` discriminated union below.
3. Add a reducer branch in ``state/projections.py`` (Track A).

The order of fields matters for serialization stability. Don't
reorder existing fields; append new ones at the end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class _EventBase(BaseModel):
    """Shared config + ``ts`` field for every event."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ts: datetime = Field(default_factory=_utcnow)


# ─── Catalog + active item ──────────────────────────────────────────


class ItemTaught(_EventBase):
    """The operator confirmed a teach proposal.

    The item becomes addressable by ``item_id`` and queryable by name.
    ``initial_count`` seeds inventory. ``reorder_threshold`` controls
    when ``StockLow`` fires.
    """

    type: Literal["item_taught"] = "item_taught"
    item_id: str
    name: str
    price_cents: int = Field(ge=0)
    initial_count: int = Field(ge=0)
    reorder_threshold: int = Field(ge=0, default=2)


class ActiveItemSet(_EventBase):
    """Operator switched which item the vision watcher is counting.

    Vision counts only the *active* item each cycle. Switching is one
    tap on the kiosk.
    """

    type: Literal["active_item_set"] = "active_item_set"
    item_id: str


# ─── Inventory ──────────────────────────────────────────────────────


CountSource = Literal["model", "manual", "pico"]


class CountSet(_EventBase):
    """A new physical_count was observed.

    Three sources:

    * ``model`` — the vision watcher (PaliGemma).
    * ``manual`` — operator typed it into the kiosk.
    * ``pico``  — the embedded keypad.

    ``confidence`` is 0.0–1.0 from the model; manual/pico are 1.0.
    """

    type: Literal["count_set"] = "count_set"
    item_id: str
    count: int = Field(ge=0)
    source: CountSource
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)


class StockLow(_EventBase):
    """Inventory dropped to or below the item's reorder_threshold.

    Emitted by InventoryService when a CountSet brings count down.
    Triggers the (stretch) restock agent.
    """

    type: Literal["stock_low"] = "stock_low"
    item_id: str
    threshold: int = Field(ge=0)
    current_count: int = Field(ge=0)


# ─── Orders ─────────────────────────────────────────────────────────


CancelReason = Literal["timeout", "customer_request", "out_of_stock", "operator"]


class OrderReserved(_EventBase):
    """A customer order was created and inventory was reserved.

    Reservation lifecycle: reserved → (paid | cancelled). Paid orders
    are then packed. The reservation holds stock for the customer
    until paid or cancelled.
    """

    type: Literal["order_reserved"] = "order_reserved"
    order_id: str
    item_id: str
    qty: int = Field(gt=0)
    total_cents: int = Field(gt=0)
    customer_phone: str  # E.164


class OrderPaid(_EventBase):
    """Stripe webhook said the customer paid.

    ``stripe_session_id`` is the Checkout session that was paid.
    Idempotency key for the OrdersService.
    """

    type: Literal["order_paid"] = "order_paid"
    order_id: str
    stripe_session_id: str


class OrderPacked(_EventBase):
    """Operator marked the order as ready for pickup."""

    type: Literal["order_packed"] = "order_packed"
    order_id: str


class OrderCancelled(_EventBase):
    type: Literal["order_cancelled"] = "order_cancelled"
    order_id: str
    reason: CancelReason


# ─── Restock (Sponge stretch) ──────────────────────────────────────


RestockActor = Literal["pico", "api", "worker", "test"]


class RestockProposed(_EventBase):
    """The restock worker proposed a locked supplier payment.

    This is the only event emitted directly from ``StockLow``. It
    does not move money. The payload is intentionally complete so a
    later Pico approval can approve this exact supplier, quantity,
    amount, destination URL, and request body hash.
    """

    type: Literal["restock_proposed"] = "restock_proposed"
    proposal_id: str
    stock_low_offset: int = Field(gt=0)
    item_id: str
    item_name: str
    qty: int = Field(gt=0)
    supplier_id: str
    supplier_name: str
    unit_price_cents: int = Field(ge=0)
    amount_cents: int = Field(gt=0)
    gateway_url: str
    payload_hash: str = Field(min_length=16)
    idempotency_key: str = Field(min_length=8)
    expires_at_iso: str


class RestockSpongePlanSubmitted(_EventBase):
    """PaySponge accepted an approval plan for the locked proposal."""

    type: Literal["restock_sponge_plan_submitted"] = (
        "restock_sponge_plan_submitted"
    )
    proposal_id: str
    sponge_plan_id: str


class RestockApproved(_EventBase):
    """A human approved the exact proposal on the local approval surface."""

    type: Literal["restock_approved"] = "restock_approved"
    proposal_id: str
    approved_by: RestockActor = "pico"
    payload_hash: str = Field(min_length=16)
    amount_cents: int = Field(gt=0)


class RestockRejected(_EventBase):
    """A pending proposal was explicitly rejected."""

    type: Literal["restock_rejected"] = "restock_rejected"
    proposal_id: str
    rejected_by: RestockActor = "pico"
    reason: str = ""


class RestockPaymentStarted(_EventBase):
    """The backend is about to execute the approved paid request."""

    type: Literal["restock_payment_started"] = "restock_payment_started"
    proposal_id: str
    amount_cents: int = Field(gt=0)
    sponge_plan_id: str | None = None


class RestockPaymentFailed(_EventBase):
    """Restock failed before supplier confirmation.

    ``stage`` is intentionally broad for operator-facing logs:
    proposal construction, PaySponge plan submission/approval, the
    paid request itself, cap checks, expiry, and supplier response
    parsing all collapse into this one terminal failure event.
    """

    type: Literal["restock_payment_failed"] = "restock_payment_failed"
    proposal_id: str
    stage: Literal[
        "proposal",
        "plan",
        "approval",
        "payment",
        "supplier",
        "caps",
        "expired",
    ]
    reason: str


class RestockOrdered(_EventBase):
    """The restock agent placed and paid for a supplier order.

    Fires after StockLow → restock_agent → supplier MCP +
    Sponge MCP succeed. ``eta_iso`` is the supplier's quoted ETA.
    """

    type: Literal["restock_ordered"] = "restock_ordered"
    item_id: str
    qty: int = Field(gt=0)
    supplier_id: str
    sponge_payment_id: str
    eta_iso: str  # ISO-8601 datetime as a string for simple JSON serialization
    proposal_id: str = ""
    supplier_order_id: str = ""
    amount_cents: int = Field(ge=0, default=0)
    payment_receipt: str | None = None


class RestockReceived(_EventBase):
    """Operator confirmed the paid restock was physically received."""

    type: Literal["restock_received"] = "restock_received"
    proposal_id: str
    item_id: str
    qty: int = Field(gt=0)
    source: Literal["manual", "pico", "api"] = "manual"


# ─── The discriminated union ────────────────────────────────────────


Event = Annotated[
    (
        ItemTaught
        | ActiveItemSet
        | CountSet
        | StockLow
        | OrderReserved
        | OrderPaid
        | OrderPacked
        | OrderCancelled
        | RestockProposed
        | RestockSpongePlanSubmitted
        | RestockApproved
        | RestockRejected
        | RestockPaymentStarted
        | RestockPaymentFailed
        | RestockOrdered
        | RestockReceived
    ),
    Field(discriminator="type"),
]
"""Tagged union over every event type.

Use this as the parameter type for any function that accepts an
event of unknown concrete type. Pydantic routes on ``type`` at
deserialization time.
"""
