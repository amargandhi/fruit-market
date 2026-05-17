"""Protocol classes for every domain service + their domain types.

These are the contracts that decouple the two parallel tracks:

* **Track A** writes the real implementations of the Protocols,
  backed by the event log + projections.
* **Track B** writes routers and the LLM brain that *consume* the
  Protocols. It imports the Protocols (and the ``_stubs`` module
  while Track A is still in progress).

If either side wants to change a method signature, it has to land
as its own commit on ``main`` so the other side picks it up before
diverging further.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# ─── Domain types (frozen dataclasses) ──────────────────────────────


OrderStatus = Literal["reserved", "paid", "packed", "cancelled"]
CountSource = Literal["model", "manual", "pico"]


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    price_cents: int
    physical_count: int
    reorder_threshold: int


@dataclass(frozen=True)
class Order:
    id: str
    item_id: str
    qty: int
    total_cents: int
    status: OrderStatus
    customer_phone: str
    stripe_session_id: str | None = None


@dataclass(frozen=True)
class TeachProposal:
    id: str
    name: str
    price_cents: int
    initial_count: int
    reorder_threshold: int


@dataclass(frozen=True)
class VenueInfo:
    name: str
    location: str
    hours_today: str
    pickup: str


# ─── Service Protocols ──────────────────────────────────────────────


@runtime_checkable
class CatalogService(Protocol):
    """Catalog: what items exist + which one is active for vision."""

    def get_item(self, item_id: str) -> Item | None: ...

    def resolve_by_name(self, query: str) -> Item | None:
        """Best-effort lexical resolution. ``query`` is what the
        caller said (e.g. "nanas" → bananas). Returns None if no
        confident match — the brain then falls back to Moss."""

    def list_items(self) -> list[Item]: ...

    def set_active_item(self, item_id: str) -> None: ...

    def get_active_item(self) -> Item | None:
        """The item the vision watcher is currently counting. None
        until the operator teaches and selects one."""


@runtime_checkable
class InventoryService(Protocol):
    """Inventory: the canonical physical_count per item.

    This is the *only* place either the vision watcher or the
    operator should write counts. ``reconcile_physical_count`` is
    what closes the loop between MLX and the phone agent."""

    def get_physical_count(self, item_id: str) -> int: ...

    def reconcile_physical_count(
        self,
        item_id: str,
        count: int,
        source: CountSource,
        confidence: float = 1.0,
    ) -> None:
        """Appends a CountSet event, updates the projection, and
        emits StockLow if the new count is at or below the item's
        reorder_threshold."""


@runtime_checkable
class PricingService(Protocol):
    def quote(self, item_id: str, qty: int) -> int:
        """Total price in integer cents for ``qty`` units of
        ``item_id``. Raises ``ValueError`` if the item doesn't
        exist or qty <= 0."""


@runtime_checkable
class OrdersService(Protocol):
    """Order lifecycle: reserve → paid → packed (or cancelled).

    ``reserve`` is the only method that decrements available
    inventory. It must be locked inside a single transaction so two
    concurrent reservations of the last banana can't both win."""

    def reserve(self, item_id: str, qty: int, customer_phone: str) -> Order: ...

    def mark_paid(self, order_id: str, stripe_session_id: str) -> None:
        """Idempotent on ``stripe_session_id``. Stripe will retry
        the webhook on transient failures."""

    def mark_packed(self, order_id: str) -> None: ...

    def cancel(self, order_id: str, reason: str) -> None: ...

    def get(self, order_id: str) -> Order | None: ...

    def list_active(self) -> list[Order]:
        """Everything not yet packed or cancelled. Drives the
        kiosk's three-lane order board."""


@runtime_checkable
class TeachService(Protocol):
    """Teaches new items into the catalog from a free-text
    transcript. The brain produces a proposal, the operator
    confirms or rejects on the kiosk."""

    def propose(self, transcript: str) -> TeachProposal:
        """Parse ``transcript`` into a TeachProposal. e.g.
        \"These are apples, $1.50, 6 of them\" →
        TeachProposal(name='apples', price_cents=150,
        initial_count=6)."""

    def confirm(self, proposal_id: str) -> Item:
        """Append ItemTaught, set as active, return the new Item."""

    def reject(self, proposal_id: str) -> None: ...


# ─── Service bundle ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Services:
    """All services in one bundle.

    The FastAPI app keeps a single instance of this in its lifespan
    state. Tools and routers receive the bundle (or a single member
    of it) via dependency injection.
    """

    catalog: CatalogService
    inventory: InventoryService
    pricing: PricingService
    orders: OrdersService
    teach: TeachService
    venue: VenueInfo
