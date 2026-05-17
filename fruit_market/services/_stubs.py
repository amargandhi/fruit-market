"""In-memory fake implementations of every service Protocol.

Track B (api/brain/integrations/ui) builds against these stubs so
it doesn't have to wait for Track A's real implementations. They
expose the same shape; the only difference is persistence (none)
and validation depth (just enough to keep the routers honest).

Don't add business logic here. If a behavior matters for the demo
it belongs in the real Track A implementation. The stub should be
deliberately uninteresting so swapping it for the real thing is a
single import change.
"""

from __future__ import annotations

import os
import re
import uuid

from fruit_market.services.protocols import (
    CatalogService,
    CountSource,
    InventoryService,
    Item,
    Order,
    OrdersService,
    PricingService,
    Services,
    TeachProposal,
    TeachService,
    VenueInfo,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ─── Catalog ────────────────────────────────────────────────────────


class StubCatalogService:
    """Dict-backed catalog with simple substring-match resolution."""

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._active_id: str | None = None

    # Internal helper used by other stubs in this module.
    def _upsert(self, item: Item) -> None:
        self._items[item.id] = item

    def get_item(self, item_id: str) -> Item | None:
        return self._items.get(item_id)

    def resolve_by_name(self, query: str) -> Item | None:
        q = query.strip().lower()
        if not q:
            return None
        # Exact name match first, then substring, then any item whose
        # name contains the query as a prefix.
        for item in self._items.values():
            if item.name.lower() == q:
                return item
        for item in self._items.values():
            if q in item.name.lower() or item.name.lower() in q:
                return item
        return None

    def list_items(self) -> list[Item]:
        return list(self._items.values())

    def set_active_item(self, item_id: str) -> None:
        if item_id not in self._items:
            raise KeyError(f"unknown item: {item_id}")
        self._active_id = item_id

    def get_active_item(self) -> Item | None:
        if self._active_id is None:
            return None
        return self._items.get(self._active_id)


# ─── Inventory ──────────────────────────────────────────────────────


class StubInventoryService:
    """Mirrors physical_count on the catalog's Item record."""

    def __init__(self, catalog: StubCatalogService) -> None:
        self._catalog = catalog

    def get_physical_count(self, item_id: str) -> int:
        item = self._catalog.get_item(item_id)
        return 0 if item is None else item.physical_count

    def reconcile_physical_count(
        self,
        item_id: str,
        count: int,
        source: CountSource,
        confidence: float = 1.0,
    ) -> None:
        item = self._catalog.get_item(item_id)
        if item is None:
            raise KeyError(f"unknown item: {item_id}")
        if count < 0:
            raise ValueError(f"count must be >= 0; got {count}")
        # Stub doesn't emit events; the real impl in Track A will.
        new_item = Item(
            id=item.id,
            name=item.name,
            price_cents=item.price_cents,
            physical_count=count,
            reorder_threshold=item.reorder_threshold,
        )
        self._catalog._upsert(new_item)


# ─── Pricing ────────────────────────────────────────────────────────


class StubPricingService:
    def __init__(self, catalog: StubCatalogService) -> None:
        self._catalog = catalog

    def quote(self, item_id: str, qty: int) -> int:
        if qty <= 0:
            raise ValueError(f"qty must be > 0; got {qty}")
        item = self._catalog.get_item(item_id)
        if item is None:
            raise ValueError(f"unknown item: {item_id}")
        return item.price_cents * qty


# ─── Orders ─────────────────────────────────────────────────────────


class StubOrdersService:
    def __init__(
        self,
        catalog: StubCatalogService,
        inventory: StubInventoryService,
        pricing: StubPricingService,
    ) -> None:
        self._catalog = catalog
        self._inventory = inventory
        self._pricing = pricing
        self._orders: dict[str, Order] = {}

    def reserve(self, item_id: str, qty: int, customer_phone: str) -> Order:
        item = self._catalog.get_item(item_id)
        if item is None:
            raise ValueError(f"unknown item: {item_id}")
        if item.physical_count < qty:
            raise ValueError(
                f"not enough stock: have {item.physical_count}, need {qty}"
            )
        order = Order(
            id=_new_id("ord"),
            item_id=item_id,
            qty=qty,
            total_cents=self._pricing.quote(item_id, qty),
            status="reserved",
            customer_phone=customer_phone,
        )
        self._orders[order.id] = order
        # Stub: decrement inventory immediately so concurrent reserves
        # in tests behave deterministically.
        self._inventory.reconcile_physical_count(
            item_id=item_id,
            count=item.physical_count - qty,
            source="manual",
        )
        return order

    def mark_paid(self, order_id: str, stripe_session_id: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        if order.status == "paid":
            # Idempotent: Stripe retries succeed silently.
            return
        self._orders[order_id] = Order(
            id=order.id,
            item_id=order.item_id,
            qty=order.qty,
            total_cents=order.total_cents,
            status="paid",
            customer_phone=order.customer_phone,
            stripe_session_id=stripe_session_id,
        )

    def mark_packed(self, order_id: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        self._orders[order_id] = Order(
            id=order.id,
            item_id=order.item_id,
            qty=order.qty,
            total_cents=order.total_cents,
            status="packed",
            customer_phone=order.customer_phone,
            stripe_session_id=order.stripe_session_id,
        )

    def cancel(self, order_id: str, reason: str) -> None:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order: {order_id}")
        # Stub returns stock on cancel. Real impl will guard against
        # double-return via the event log.
        if order.status == "reserved":
            current = self._inventory.get_physical_count(order.item_id)
            self._inventory.reconcile_physical_count(
                item_id=order.item_id,
                count=current + order.qty,
                source="manual",
            )
        self._orders[order_id] = Order(
            id=order.id,
            item_id=order.item_id,
            qty=order.qty,
            total_cents=order.total_cents,
            status="cancelled",
            customer_phone=order.customer_phone,
            stripe_session_id=order.stripe_session_id,
        )

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list_active(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status not in ("packed", "cancelled")]


# ─── Teach ──────────────────────────────────────────────────────────


# Match an explicit dollar amount: "$1.50" or "1.50 dollars". We
# require the $ or a "dollars" suffix so the integer-count regex
# below doesn't see this value.
_PRICE_RE = re.compile(
    r"\$\s*(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*dollars?",
    re.IGNORECASE,
)
# Count must be followed by an explicit "of them / left / in stock /
# pieces / total" marker so it doesn't collide with the dollar amount
# above. (Dollar amounts get stripped before this regex runs too.)
_COUNT_RE = re.compile(
    r"\b(\d+)\b\s*(?:of\s+them|left|in\s+stock|pieces?|total)",
    re.IGNORECASE,
)
_NAME_RE = re.compile(
    r"(?:these\s+are\s+|this\s+is\s+a\s+|this\s+is\s+|got\s+|have\s+)([a-z][a-z\s]*?)"
    r"(?:[,.]|\s+\$|\s+for|\s+at\s+\$|$)",
    re.IGNORECASE,
)


class StubTeachService:
    """Toy parser for teach transcripts. The real impl is in Track A
    and will be stricter."""

    def __init__(self, catalog: StubCatalogService) -> None:
        self._catalog = catalog
        self._proposals: dict[str, TeachProposal] = {}

    def propose(self, transcript: str) -> TeachProposal:
        text = transcript.strip()
        name_match = _NAME_RE.search(text)
        name = (name_match.group(1).strip().lower() if name_match else "item")
        name = _singularize_name(name)

        price_match = _PRICE_RE.search(text)
        price_cents = 0
        if price_match:
            dollars = float(price_match.group(1) or price_match.group(2))
            price_cents = round(dollars * 100)

        # Strip the matched price so the count regex doesn't see "1.50"
        # as a candidate integer.
        text_for_count = (
            text.replace(price_match.group(0), "") if price_match else text
        )
        count_match = _COUNT_RE.search(text_for_count)
        initial_count = int(count_match.group(1)) if count_match else 0

        proposal = TeachProposal(
            id=_new_id("prop"),
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
        item = Item(
            id=_new_id("item"),
            name=proposal.name,
            price_cents=proposal.price_cents,
            physical_count=proposal.initial_count,
            reorder_threshold=proposal.reorder_threshold,
        )
        self._catalog._upsert(item)
        self._catalog.set_active_item(item.id)
        return item

    def reject(self, proposal_id: str) -> None:
        self._proposals.pop(proposal_id, None)


# ─── Factory ────────────────────────────────────────────────────────


def _venue_from_env() -> VenueInfo:
    return VenueInfo(
        name=os.environ.get("VENUE_NAME", "Fruit Market"),
        location=os.environ.get("VENUE_LOCATION", ""),
        hours_today=os.environ.get("VENUE_HOURS_TODAY", ""),
        pickup=os.environ.get(
            "VENUE_PICKUP",
            "Come to the front counter, show your order email or SMS.",
        ),
    )


def make_stub_services() -> Services:
    """Construct an in-memory Services bundle.

    All five service stubs share the same in-memory state by
    composition: orders mutates inventory, teach mutates catalog,
    etc. Holding a single ``StubCatalogService`` is the trick that
    makes the bundle behave like one process.
    """

    catalog = StubCatalogService()
    inventory = StubInventoryService(catalog)
    pricing = StubPricingService(catalog)
    orders = StubOrdersService(catalog, inventory, pricing)
    teach = StubTeachService(catalog)
    venue = _venue_from_env()

    # The Protocol checker is satisfied by structural typing — we
    # cast through the Services dataclass below.
    services: Services = Services(
        catalog=_ensure_catalog(catalog),
        inventory=_ensure_inventory(inventory),
        pricing=_ensure_pricing(pricing),
        orders=_ensure_orders(orders),
        teach=_ensure_teach(teach),
        venue=venue,
    )
    return services


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


# These helpers exist purely to give mypy a clear assertion point
# that each concrete stub conforms to its Protocol.


def _ensure_catalog(c: StubCatalogService) -> CatalogService:
    return c


def _ensure_inventory(i: StubInventoryService) -> InventoryService:
    return i


def _ensure_pricing(p: StubPricingService) -> PricingService:
    return p


def _ensure_orders(o: StubOrdersService) -> OrdersService:
    return o


def _ensure_teach(t: StubTeachService) -> TeachService:
    return t
