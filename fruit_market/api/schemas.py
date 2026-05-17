"""HTTP request/response envelopes.

Two flavors:

* **Inbound** — what AgentPhone and Stripe POST to our webhooks.
  We mirror the bare-minimum fields we read; we don't reject extra
  keys (sponsors evolve their payload shapes).
* **Outbound** — what the kiosk consumes from ``/api/state`` and
  ``/api/state/stream``, plus the kiosk → backend POSTs.

If a service Protocol's output already has the right shape, the
router should serialize it directly; don't redefine it here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fruit_market.services.protocols import OrderStatus


class _Schema(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate sponsor schema drift


# ─── AgentPhone webhook envelope ────────────────────────────────────


class AgentPhoneCallContext(_Schema):
    """The subset of fields we read from an AgentPhone call payload."""

    call_id: str
    from_phone: str = Field(alias="from")
    to_phone: str = Field(alias="to")


class AgentPhoneWebhookEnvelope(_Schema):
    """Inbound webhook from AgentPhone.

    The envelope shape isn't fully nailed down by sponsor docs yet;
    ``extra="ignore"`` lets us evolve without crashing on new fields.
    """

    type: str  # "call.started" | "call.transcript" | "message.received" | ...
    call: AgentPhoneCallContext | None = None
    transcript: str | None = None
    message: str | None = None


# ─── Stripe webhook envelope (we use Stripe's library; this is for tests) ─


class StripeWebhookEnvelope(_Schema):
    """Minimal Stripe webhook shape for our handler.

    Production code goes through ``stripe.Webhook.construct_event``
    so the real Stripe types apply. This mirror exists so contract
    tests can build envelopes without importing stripe."""

    id: str
    type: str
    data: dict[str, object]


# ─── Kiosk: snapshot ────────────────────────────────────────────────


class CatalogItemView(_Schema):
    item_id: str
    name: str
    price_cents: int
    physical_count: int
    is_active: bool
    is_low: bool


class OrderView(_Schema):
    order_id: str
    item_id: str
    item_name: str
    qty: int
    total_cents: int
    status: OrderStatus
    customer_phone: str


class KioskStateSnapshot(_Schema):
    catalog: list[CatalogItemView]
    active_item_id: str | None
    orders: list[OrderView]


# ─── Kiosk: SSE ─────────────────────────────────────────────────────


SSEEventName = Literal[
    "state.catalog",
    "state.inventory",
    "state.orders",
    "state.active_item",
    "state.stock_low",
]


class KioskSSEEvent(_Schema):
    """One server-sent event pushed to ``/api/state/stream``.

    Clients reconnect with ``last_event_id`` to resume; the backend
    replays the projection at that offset.
    """

    event: SSEEventName
    data: dict[str, object]
    id: int  # monotonic offset for SSE reconnect


# ─── Kiosk: teach ───────────────────────────────────────────────────


class TeachRequest(_Schema):
    transcript: str = Field(min_length=1)


class TeachProposalView(_Schema):
    proposal_id: str
    name: str
    price_cents: int
    initial_count: int
    reorder_threshold: int


class TeachResponse(_Schema):
    proposal: TeachProposalView


class TeachConfirmRequest(_Schema):
    proposal_id: str


# ─── Kiosk: orders ──────────────────────────────────────────────────


class PackOrderResponse(_Schema):
    order_id: str
    status: OrderStatus


class SwitchActiveItemRequest(_Schema):
    item_id: str


class SwitchActiveItemResponse(_Schema):
    item_id: str
