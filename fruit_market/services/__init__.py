"""Domain services — the business logic layer.

Phase 1 ships only the Protocol classes (the shapes that other
layers depend on) and an in-memory ``_stubs`` module that
implements every Protocol with dict-backed fakes. The parallel
"Track A" PR replaces those fakes with real implementations
backed by the event store and projections.

``make_services()`` is the single factory the FastAPI app should
call. After Track A merges, this function returns the real
implementations; in the meantime it returns the stubs.

This means Codex's Track B can build against ``make_services()``
today without waiting for Track A.
"""

from fruit_market.services import _stubs
from fruit_market.services.protocols import (
    CatalogService,
    InventoryService,
    Item,
    Order,
    OrdersService,
    OrderStatus,
    PricingService,
    Services,
    TeachProposal,
    TeachService,
    VenueInfo,
)


def make_services() -> Services:
    """Factory for the service bundle.

    Phase 1: returns the in-memory stubs. After Track A merges, the
    real implementations replace this body. Callers don't change.
    """

    return _stubs.make_stub_services()


__all__ = [
    "CatalogService",
    "InventoryService",
    "Item",
    "Order",
    "OrderStatus",
    "OrdersService",
    "PricingService",
    "Services",
    "TeachProposal",
    "TeachService",
    "VenueInfo",
    "make_services",
]
