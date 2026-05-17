"""Real PricingService.

Pure read over the catalog projection. Money is integer cents.
Floats never enter; the public method takes ``qty: int`` and the
catalog stores ``price_cents: int``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fruit_market.state.projections import CatalogProjection


class RealPricingService:
    def __init__(self, catalog: CatalogProjection) -> None:
        self._catalog = catalog

    def quote(self, item_id: str, qty: int) -> int:
        if qty <= 0:
            raise ValueError(f"qty must be > 0; got {qty}")
        item = self._catalog.get(item_id)
        if item is None:
            raise ValueError(f"unknown item: {item_id}")
        return item.price_cents * qty
