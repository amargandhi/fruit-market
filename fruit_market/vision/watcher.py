"""Asyncio task that keeps inventory honest.

Every ``poll_interval_seconds`` (default 3 s) while an item is
active in the catalog:

1. Snapshot the camera.
2. Run the motion gate — if the image is virtually identical to
   the previous frame, skip the model call. Saves ~0.5 s + GPU
   energy on a static counter.
3. Ask PaliGemma to ``count {active_item.name}``.
4. Reconcile through :class:`InventoryService` so the catalog
   projection picks up the new count and any StockLow fires.

The watcher is exception-tolerant by design. A failed camera read
or a model misfire logs and falls through to the next tick. The
loop only exits when ``stop()`` is called.

Construct via :func:`build_default_watcher` once your services and
camera + model are wired up. The FastAPI lifespan owns the lifetime.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from fruit_market.services.protocols import InventoryService, Item
    from fruit_market.vision.camera import Camera
    from fruit_market.vision.model import PaliGemmaCounter


logger = logging.getLogger(__name__)


@dataclass
class WatcherStatus:
    running: bool = False
    last_tick_ok: bool = False
    last_count: int | None = None
    last_active_item: str | None = None
    last_skipped_motion: bool = False
    consecutive_failures: int = 0
    # Set by the watcher each time it runs the motion gate.
    last_motion_score: float = field(default=0.0)


class VisionWatcher:
    """Run the count loop on the asyncio event loop."""

    def __init__(
        self,
        *,
        catalog_active_item: Callable[[], Item | None],
        inventory: InventoryService,
        camera: Camera,
        model: PaliGemmaCounter,
        poll_interval_seconds: float | None = None,
        motion_threshold: float | None = None,
    ) -> None:
        self._get_active_item: Callable[[], Item | None] = catalog_active_item
        self._inventory = inventory
        self._camera = camera
        self._model = model
        self._poll_interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else float(os.environ.get("FM_VISION_POLL_SECONDS", "3"))
        )
        self._motion_threshold = (
            motion_threshold
            if motion_threshold is not None
            else float(os.environ.get("FM_VISION_MOTION_THRESHOLD", "8.0"))
        )
        self._previous_frame: bytes | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.status = WatcherStatus()

    # ─── lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="vision-watcher")
        self.status.running = True

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await self._task
        finally:
            self._task = None
            self.status.running = False

    # ─── main loop ────────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("vision watcher tick failed")
                self.status.consecutive_failures += 1
                self.status.last_tick_ok = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                break  # stop was signalled
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        active: Item | None = self._get_active_item()
        if active is None:
            # Nothing to count yet. Don't burn cycles on snapshots.
            self.status.last_active_item = None
            self.status.last_tick_ok = True
            return

        self.status.last_active_item = active.id

        # Run the blocking IO (camera + model) on the default
        # executor so the asyncio loop stays responsive.
        loop = asyncio.get_running_loop()
        frame = await loop.run_in_executor(None, self._camera.snapshot)

        if self._is_unchanged(frame):
            self.status.last_skipped_motion = True
            self.status.last_tick_ok = True
            return
        self.status.last_skipped_motion = False
        self._previous_frame = frame

        count = await loop.run_in_executor(None, self._model.count, frame, active.name)
        self.status.last_count = count
        self.status.consecutive_failures = 0
        self.status.last_tick_ok = True

        # Reconcile is cheap (in-process SQLite append + projection
        # update); we don't need to push it onto the executor.
        self._inventory.reconcile_physical_count(
            item_id=active.id,
            count=count,
            source="model",
            confidence=0.9,
        )

    # ─── motion gate ──────────────────────────────────────────────

    def _is_unchanged(self, frame: bytes) -> bool:
        """Cheap perceptual diff against the previous frame.

        We compare the JPEG byte length; for the same camera and
        same scene the encoder produces near-identical output. A
        large change (someone reached in, added an item) shifts
        the byte length significantly. This is intentionally less
        precise than a pixel diff — we want the model to re-count
        on *any* plausible change, not just statistically
        significant ones.
        """

        if self._previous_frame is None:
            return False
        prev = len(self._previous_frame)
        curr = len(frame)
        delta = abs(prev - curr)
        pct = (delta / max(prev, 1)) * 100.0
        self.status.last_motion_score = pct
        return pct < self._motion_threshold
