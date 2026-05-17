"""Domain services — the business logic layer.

``make_services()`` is the single factory the FastAPI app calls.
By default it returns the real (event-store-backed) bundle. Set
``FRUITMARKET_USE_STUBS=1`` in the environment to fall back to the
in-memory ``_stubs`` bundle — useful for contract tests that don't
want SQLite touching disk.

The Protocol classes are the contract both implementations satisfy;
nothing else in the codebase should care which one is wired up.
"""

import os

from fruit_market.services import _stubs
from fruit_market.services.factory import make_real_services
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

    Returns the real, event-store-backed bundle by default. Set
    ``FRUITMARKET_USE_STUBS=1`` to fall back to in-memory stubs —
    handy for tests that don't want SQLite I/O.
    """

    if os.environ.get("FRUITMARKET_USE_STUBS") == "1":
        return _stubs.make_stub_services()
    return make_real_services()


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
