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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Schema(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )  # tolerate sponsor schema drift


NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
OrderStatus = Literal["reserved", "paid", "packed", "cancelled"]


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

    type: str = Field(alias="event")
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
    price_cents: NonNegativeInt
    physical_count: NonNegativeInt
    is_active: bool
    is_low: bool


class OrderView(_Schema):
    order_id: str
    item_id: str
    item_name: str
    qty: PositiveInt
    total_cents: NonNegativeInt
    status: OrderStatus
    customer_phone: str


class PendingActions(_Schema):
    """What's pending operator attention right now.

    Drives the Pico keypad's per-key breathe animations: when one
    of these is set, the corresponding action key lights up so the
    operator knows there's something to press.
    """

    teach_proposal: str | None = None  # proposal_id or null
    paid_order: str | None = None      # order_id or null
    reservation: str | None = None     # order_id or null
    supply_buy: bool = False


class RestockView(_Schema):
    proposal_id: str
    item_id: str
    item_name: str
    qty: PositiveInt
    supplier_name: str
    amount_cents: NonNegativeInt
    status: str
    eta_minutes: NonNegativeInt = 0
    basket_url: str = ""
    operator_email: str | None = None
    email_status: str = "not_configured"
    email_message_id: str | None = None
    email_failure_reason: str | None = None
    eta_iso: str | None = None
    failure_reason: str | None = None


class SystemHealth(_Schema):
    """One field per subsystem the Pico keypad surfaces as an LED.

    Values are restricted to the vocabulary documented in
    ``fruit_market/hardware/pico_protocol.py`` (``ok | warmup |
    mock | warn | fail | error | down | unknown``)."""

    camera: str = "unknown"
    model: str = "unknown"
    phone: str = "unknown"


class KioskStateSnapshot(_Schema):
    catalog: list[CatalogItemView]
    active_item_id: str | None
    orders: list[OrderView]
    pending: PendingActions = Field(default_factory=PendingActions)
    restock: RestockView | None = None
    health: SystemHealth = Field(default_factory=SystemHealth)
    # Demo control: streamer captures regardless, but inference
    # (PaliGemma counting) only fires while ``demo_active`` is
    # True. Flipped to True by the Pico START button (or by
    # POST /api/demo/start from the kiosk fallback).
    demo_active: bool = False


class DemoStateResponse(_Schema):
    demo_active: bool


# ─── Kiosk: SSE ─────────────────────────────────────────────────────


SSEEventName = Literal[
    "state.catalog",
    "state.inventory",
    "state.orders",
    "state.active_item",
    "state.stock_low",
    "state.restock",
]


class KioskSSEEvent(_Schema):
    """One server-sent event pushed to ``/api/state/stream``.

    Clients reconnect with ``last_event_id`` to resume; the backend
    replays the projection at that offset.
    """

    event: SSEEventName
    data: dict[str, object]
    id: NonNegativeInt  # monotonic offset for SSE reconnect


# ─── Kiosk: teach ───────────────────────────────────────────────────


class TeachRequest(_Schema):
    transcript: str = Field(min_length=1)


class TeachProposalView(_Schema):
    proposal_id: str
    name: str
    price_cents: NonNegativeInt
    initial_count: NonNegativeInt
    reorder_threshold: NonNegativeInt


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


# ─── Pico sidecar action endpoint ─────────────────────────────────


class PicoActionRequest(_Schema):
    """POSTed by the Pico bridge when an action key is pressed.

    The backend resolves the relevant resource (order_id,
    proposal_id) from its current state — the firmware doesn't
    know IDs, just canonical action names.
    """

    action: str = Field(min_length=1)


class PicoActionResponse(_Schema):
    """One dispatcher result.

    ``status`` carries the textual outcome (``ok``, ``no_pending``,
    ``acknowledged``, ``not_implemented``, ``rejected``…) and is
    what the bridge logs. ``order_id`` / ``proposal_id`` / ``detail``
    surface whatever resource the dispatcher acted on so the bridge
    can correlate with the next state push.
    """

    action: str
    status: str
    ok: bool = True
    detail: str = ""
    order_id: str | None = None
    proposal_id: str | None = None
