from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from fruit_market.restock.coordinator import RestockCoordinator
from fruit_market.restock.paysponge import PaySpongeWalletClient
from fruit_market.restock.projection import RestockProjection
from fruit_market.restock.settings import RestockSettings
from fruit_market.restock.supplier import StaticSupplierClient
from fruit_market.services.factory import make_real_services
from fruit_market.state.events import StockLow
from fruit_market.state.store import EventStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.real_sponsors
def test_real_restock_paysponge_gateway_smoke(tmp_path: Path) -> None:
    if os.environ.get("FRUITMARKET_RUN_REAL_SPONGE") != "1":
        pytest.skip("set FRUITMARKET_RUN_REAL_SPONGE=1 to make a real paid Gateway call")
    gateway_url = os.environ.get("RESTOCK_SUPPLIER_GATEWAY_URL", "")
    if not gateway_url:
        pytest.skip("RESTOCK_SUPPLIER_GATEWAY_URL is required")

    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    projection = RestockProjection.hydrate(store.replay())
    store.subscribe(lambda _offset, event: projection.apply(event))
    item = services.teach.confirm(
        services.teach.propose("These are apples, $1.50, 6 of them").id
    )
    sponge = PaySpongeWalletClient(
        api_key=os.environ["SPONGE_API_KEY"],
        preferred_chain=os.environ.get("RESTOCK_SPONGE_PREFERRED_CHAIN", "base"),
    )
    coordinator = RestockCoordinator(
        settings=RestockSettings(
            enabled=True,
            payment_mode="staging_live",
            supplier_gateway_url=gateway_url,
            default_quantity=int(os.environ.get("RESTOCK_DEFAULT_QTY", "24")),
        ),
        store=store,
        projection=projection,
        catalog=services.catalog,
        supplier=StaticSupplierClient(gateway_url),
        sponge=sponge,
    )

    low = StockLow(item_id=item.id, threshold=2, current_count=0)
    offset = store.append(low)
    coordinator.handle_stock_low(offset, low)
    result = coordinator.approve_pending(approved_by="test")

    assert result.ok is True
    record = projection.current_active()
    assert record is not None
    assert record.status == "ordered"
    assert record.supplier_order_id
