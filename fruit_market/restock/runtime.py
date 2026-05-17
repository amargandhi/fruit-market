"""Runtime wiring for the optional restock worker."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fruit_market.restock.coordinator import ApprovalResult, RestockCoordinator
from fruit_market.restock.paysponge import (
    PaySpongeClient,
    PaySpongeWalletClient,
    UnavailablePaySpongeClient,
)
from fruit_market.restock.projection import RestockProjection
from fruit_market.restock.settings import RestockSettings
from fruit_market.restock.supplier import HttpDemoSupplierClient, StaticSupplierClient
from fruit_market.state.events import Event, StockLow

if TYPE_CHECKING:
    from collections.abc import Callable

    from fruit_market.services.protocols import Services
    from fruit_market.state.store import EventStore


logger = logging.getLogger("fm.restock")


class RestockWorker:
    """Small side worker so ``StockLow`` subscribers do not block writers."""

    def __init__(self, coordinator: RestockCoordinator) -> None:
        self._coordinator = coordinator
        self._queue: queue.Queue[tuple[int, StockLow] | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="fm-restock-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=2.0)
        self._thread = None

    def enqueue(self, offset: int, event: StockLow) -> None:
        self._queue.put((offset, event))

    def drain_once_for_tests(self) -> bool:
        item = self._queue.get_nowait()
        if item is None:
            return False
        offset, event = item
        self._coordinator.handle_stock_low(offset, event)
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            offset, event = item
            try:
                self._coordinator.handle_stock_low(offset, event)
            except Exception:
                logger.exception("restock worker failed for stock_low offset=%s", offset)


@dataclass
class RestockRuntime:
    settings: RestockSettings
    projection: RestockProjection
    coordinator: RestockCoordinator
    worker: RestockWorker
    _store: EventStore
    _unsubscribers: list[Callable[[], None]]

    def start(self) -> None:
        if not self.settings.enabled:
            return
        if self._unsubscribers:
            return

        self._unsubscribers.append(self._store.subscribe(self._projection_subscriber))
        self._unsubscribers.append(self._store.subscribe(self._worker_subscriber))
        self.worker.start()
        for offset, event in self._store.replay():
            if isinstance(event, StockLow) and not self.projection.has_stock_low_offset(offset):
                self.worker.enqueue(offset, event)

    def stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self.worker.stop()

    def approve_pending(self, *, approved_by: str = "pico") -> ApprovalResult:
        return self.coordinator.approve_pending(approved_by=approved_by)

    def reject_pending(
        self, *, rejected_by: str = "pico", reason: str = ""
    ) -> ApprovalResult:
        return self.coordinator.reject_pending(
            rejected_by=rejected_by,
            reason=reason,
        )

    def _projection_subscriber(self, _offset: int, event: Event) -> None:
        self.projection.apply(event)

    def _worker_subscriber(self, offset: int, event: Event) -> None:
        if isinstance(event, StockLow):
            self.worker.enqueue(offset, event)


def build_restock_runtime(
    *,
    settings: RestockSettings,
    store: EventStore,
    services: Services,
    sponge: PaySpongeClient | None = None,
) -> RestockRuntime | None:
    if not settings.enabled:
        return None

    projection = RestockProjection.hydrate(store.replay())
    supplier = (
        HttpDemoSupplierClient(
            api_base=settings.supplier_api_base,
            gateway_url=settings.supplier_gateway_url,
        )
        if settings.supplier_api_base
        else StaticSupplierClient(settings.supplier_gateway_url)
    )

    live_sponge = sponge
    if live_sponge is None and settings.payment_mode == "staging_live":
        try:
            live_sponge = PaySpongeWalletClient(
                api_key=settings.sponge_api_key,
                preferred_chain=settings.sponge_preferred_chain,
            )
        except Exception as exc:  # noqa: BLE001
            live_sponge = UnavailablePaySpongeClient(str(exc))

    coordinator = RestockCoordinator(
        settings=settings,
        store=store,
        projection=projection,
        catalog=services.catalog,
        supplier=supplier,
        sponge=live_sponge,
    )
    return RestockRuntime(
        settings=settings,
        projection=projection,
        coordinator=coordinator,
        worker=RestockWorker(coordinator),
        _store=store,
        _unsubscribers=[],
    )
