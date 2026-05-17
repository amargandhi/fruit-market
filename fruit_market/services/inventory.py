"""Real InventoryService.

The vision watcher calls ``reconcile_physical_count`` every poll
cycle. This is the *only* path inventory should be written by the
model — it appends a ``CountSet`` event, the catalog projection
picks it up, and ``StockLow`` is emitted if the new count is at or
below the item's reorder threshold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fruit_market.state.events import CountSet, StockLow

if TYPE_CHECKING:
    from fruit_market.services.protocols import CountSource
    from fruit_market.state.projections import CatalogProjection
    from fruit_market.state.store import EventStore


class RealInventoryService:
    def __init__(self, store: EventStore, catalog: CatalogProjection) -> None:
        self._store = store
        self._catalog = catalog

    def get_physical_count(self, item_id: str) -> int:
        item = self._catalog.get(item_id)
        return 0 if item is None else item.physical_count

    def reconcile_physical_count(
        self,
        item_id: str,
        count: int,
        source: CountSource,
        confidence: float = 1.0,
    ) -> None:
        item = self._catalog.get(item_id)
        if item is None:
            raise KeyError(f"unknown item: {item_id}")
        if count < 0:
            raise ValueError(f"count must be >= 0; got {count}")

        # Skip the write if nothing actually changed. The watcher
        # polls every 3s; without this, a static counter would
        # flood the log with redundant CountSets.
        if item.physical_count == count and source == "model":
            return

        # The CountSet event commits first; the catalog projection
        # picks it up via the live-tail subscription, which means
        # the StockLow check below sees the updated count.
        self._store.append(
            CountSet(
                item_id=item_id,
                count=count,
                source=source,
                confidence=confidence,
            )
        )

        # Reorder gate. Fires once per descent below threshold;
        # consumers (e.g. the restock agent) are responsible for
        # their own idempotency.
        was_above = item.physical_count > item.reorder_threshold
        is_at_or_below = count <= item.reorder_threshold
        if was_above and is_at_or_below:
            self._store.append(
                StockLow(
                    item_id=item_id,
                    threshold=item.reorder_threshold,
                    current_count=count,
                )
            )
