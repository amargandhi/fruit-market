"""Construct a real Services bundle backed by the event store.

This is the "production" alternative to ``_stubs.make_stub_services``.
``make_services()`` (in ``services/__init__.py``) delegates here on
the live path.

Wiring order matters and is opinionated:

1. Open the event store (SQLite WAL).
2. Hydrate the catalog and orders projections by replaying the log.
3. Construct each service, handing it the store + the projections
   it needs. The CatalogService subscribes to the store on
   construction; that's how live appends flow into the projection.
4. Subscribe the OrdersProjection to the store too — it's used
   read-only by OrdersService and the kiosk SSE stream.

Pass an existing ``EventStore`` (e.g. from tests) instead of letting
this open the default. The watcher and the FastAPI app share one
store per process.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fruit_market.services.catalog import RealCatalogService
from fruit_market.services.inventory import RealInventoryService
from fruit_market.services.orders import RealOrdersService
from fruit_market.services.pricing import RealPricingService
from fruit_market.services.protocols import Services, VenueInfo
from fruit_market.services.teach import RealTeachService
from fruit_market.state.projections import CatalogProjection, OrdersProjection
from fruit_market.state.store import EventStore, open_default_store

if TYPE_CHECKING:
    from fruit_market.state.events import Event


def make_real_services(store: EventStore | None = None) -> Services:
    """Build a real Services bundle.

    The caller is responsible for the store's lifetime (close it on
    shutdown). For the default in-process store, just call this
    with no arguments — the FastAPI lifespan handles teardown.
    """

    event_store = store if store is not None else open_default_store()

    # Hydrate projections from the entire log. For an empty DB this
    # is instant; for a long-running deployment it's still fast
    # because the log only grows with real-world traffic.
    catalog_proj = CatalogProjection.hydrate(event_store.replay())
    orders_proj = OrdersProjection.hydrate(event_store.replay())

    # The CatalogService subscribes to live appends in its
    # constructor; do the same for orders so the kiosk's SSE stream
    # sees order transitions without a manual wire-up.
    def _orders_subscriber(_offset: int, event: Event) -> None:
        orders_proj.apply(event)

    event_store.subscribe(_orders_subscriber)

    catalog = RealCatalogService(event_store, catalog_proj)
    inventory = RealInventoryService(event_store, catalog_proj)
    pricing = RealPricingService(catalog_proj)
    orders = RealOrdersService(event_store, catalog_proj, orders_proj)
    teach = RealTeachService(event_store, catalog_proj)
    venue = _venue_from_env()

    return Services(
        catalog=catalog,
        inventory=inventory,
        pricing=pricing,
        orders=orders,
        teach=teach,
        venue=venue,
    )


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
