"""Real CatalogService backed by the event store + CatalogProjection.

Owns the projection. Subscribes to the store so projection state
stays in sync with appends from other services (e.g. when
InventoryService writes a CountSet).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fruit_market.services.protocols import Item
    from fruit_market.state.events import Event
    from fruit_market.state.projections import CatalogProjection
    from fruit_market.state.store import EventStore


class RealCatalogService:
    def __init__(self, store: EventStore, projection: CatalogProjection) -> None:
        self._store = store
        self._projection = projection
        # Live tail: every future append updates the projection.
        # Hydration of past events is the caller's job (done in
        # ``services.build_real_services``).
        self._unsubscribe = store.subscribe(self._on_event)

    def _on_event(self, _offset: int, event: Event) -> None:
        self._projection.apply(event)

    # ─── reads ────────────────────────────────────────────────────

    def get_item(self, item_id: str) -> Item | None:
        return self._projection.get(item_id)

    def resolve_by_name(self, query: str) -> Item | None:
        q = query.strip().lower()
        if not q:
            return None
        items = self._projection.list()
        # Exact match wins.
        for item in items:
            if item.name.lower() == q:
                return item
        # Then substring either way (handles "nana" → "banana"
        # and "bananas" → "banana").
        for item in items:
            if q in item.name.lower() or item.name.lower() in q:
                return item
        return None

    def list_items(self) -> list[Item]:
        return self._projection.list()

    def get_active_item(self) -> Item | None:
        return self._projection.active()

    # ─── writes ───────────────────────────────────────────────────

    def set_active_item(self, item_id: str) -> None:
        from fruit_market.state.events import ActiveItemSet

        if self._projection.get(item_id) is None:
            raise KeyError(f"unknown item: {item_id}")
        self._store.append(ActiveItemSet(item_id=item_id))

    # ─── lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        self._unsubscribe()
