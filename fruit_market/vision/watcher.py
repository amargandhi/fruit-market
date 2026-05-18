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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from fruit_market.services.protocols import InventoryService, Item
    from fruit_market.vision.camera import CameraBackend
    from fruit_market.vision.model import PaliGemmaCounter


logger = logging.getLogger(__name__)


# Cross-class IoU threshold. Two boxes from different classes that
# overlap by 50%+ of their union refer to the same physical object
# — PaliGemma claimed it as two classes, only one can be right, so
# we drop the smaller one. 0.5 matches the standard COCO threshold;
# tune via FM_VISION_CROSS_CLASS_IOU if needed.
_CROSS_CLASS_IOU = float(os.environ.get("FM_VISION_CROSS_CLASS_IOU", "0.5"))


def _box_iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    """IoU between two ``(y0, x0, y1, x1)`` boxes."""

    ay0, ax0, ay1, ax1 = a
    by0, bx0, by1, bx1 = b
    iy0, ix0 = max(ay0, by0), max(ax0, bx0)
    iy1, ix1 = min(ay1, by1), min(ax1, bx1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ay1 - ay0) * (ax1 - ax0)
    area_b = (by1 - by0) * (bx1 - bx0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _cross_class_nms(
    per_class_boxes: dict[str, list[tuple[int, int, int, int]]],
    iou_threshold: float,
) -> dict[str, list[tuple[int, int, int, int]]]:
    """Drop boxes that overlap with another class's boxes.

    For each pair of overlapping cross-class boxes (IoU above the
    threshold), the smaller box loses — larger area is a rough
    proxy for "the class the model was more confident about for
    this spatial region." This fixes the failure mode where
    PaliGemma's ``detect banana`` draws a box around an apple:
    when ``detect apple`` also drew a box there, the apple box
    is usually tighter/larger, so the banana box drops.

    Returns a new dict with the surviving boxes per class.
    """

    # Flatten to a list of (class_name, box_index, box) so we can
    # mark losers by (class, index) without mutating mid-iteration.
    flat: list[tuple[str, int, tuple[int, int, int, int]]] = []
    for name, boxes in per_class_boxes.items():
        for idx, box in enumerate(boxes):
            flat.append((name, idx, box))

    losers: set[tuple[str, int]] = set()
    for i, (name_a, idx_a, box_a) in enumerate(flat):
        if (name_a, idx_a) in losers:
            continue
        area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
        for name_b, idx_b, box_b in flat[i + 1 :]:
            if name_a == name_b:
                continue  # intra-class already deduped at the model layer
            if (name_b, idx_b) in losers:
                continue
            if _box_iou(box_a, box_b) < iou_threshold:
                continue
            area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
            # Smaller box loses. Ties broken alphabetically by class
            # for determinism.
            if area_a < area_b or (area_a == area_b and name_a > name_b):
                losers.add((name_a, idx_a))
                break  # we lost — stop checking against others
            losers.add((name_b, idx_b))

    return {
        name: [box for idx, box in enumerate(boxes) if (name, idx) not in losers]
        for name, boxes in per_class_boxes.items()
    }


@dataclass
class WatcherStatus:
    running: bool = False
    last_tick_ok: bool = False
    # Per-noun results from the most recent successful tick.
    # ``last_counts["banana"] = 3`` etc. Multi-item mode populates
    # every taught noun each cycle.
    last_counts: dict[str, int] = field(default_factory=dict)
    last_count: int | None = None  # active item, kept for back-compat
    last_active_item: str | None = None
    last_skipped_motion: bool = False
    consecutive_failures: int = 0
    # Set by the watcher each time it runs the motion gate.
    last_motion_score: float = field(default=0.0)
    # Wall-clock time of the most recent successful count. Used by the
    # heartbeat: if the motion gate has been suppressing counts for
    # longer than ``heartbeat_seconds``, force one through anyway so
    # the dashboard doesn't show a stale value indefinitely.
    last_count_at_monotonic: float = field(default=0.0)
    # Ring buffer of the most recent count CHANGES (not every count).
    # Powers the kiosk's "Activity" panel and the Pico's flash-on-
    # change LED. Each entry: {item_name, prev, new, delta, kind, ts}
    # where kind is one of "added" | "removed" | "out" | "restocked"
    # | "first_seen". Capped at MAX_CHANGE_HISTORY so a long-running
    # watcher doesn't grow unbounded.
    recent_changes: list[dict[str, object]] = field(default_factory=list)


MAX_CHANGE_HISTORY = 50


def _classify_change(prev: int | None, new: int) -> str:
    """Map a (prev, new) pair to one of the demo-legible kinds.

    Judging shorthand:
      first_seen → first time we counted this noun
      restocked  → was 0, now > 0 (replenishment)
      out        → was > 0, now 0 (alarm)
      added      → went up
      removed    → went down
    """

    if prev is None:
        return "first_seen"
    if prev > 0 and new == 0:
        return "out"
    if prev == 0 and new > 0:
        return "restocked"
    if new > prev:
        return "added"
    if new < prev:
        return "removed"
    return "unchanged"


class VisionWatcher:
    """Run the count loop on the asyncio event loop."""

    def __init__(
        self,
        *,
        catalog_active_item: Callable[[], Item | None],
        inventory: InventoryService,
        camera: CameraBackend,
        model: PaliGemmaCounter,
        catalog_items: Callable[[], list[Item]] | None = None,
        max_items_per_tick: int | None = None,
        poll_interval_seconds: float | None = None,
        motion_threshold: float | None = None,
        heartbeat_seconds: float | None = None,
        gate: Callable[[], bool] | None = None,
    ) -> None:
        self._get_active_item: Callable[[], Item | None] = catalog_active_item
        # The gate lets the FastAPI lifespan wire the watcher to the
        # demo-start flag: video keeps streaming, but the model
        # doesn't run until the operator hits START on the Pico. By
        # default the gate is always-open so the watcher works in
        # tests + direct construction without any extra plumbing.
        self._gate: Callable[[], bool] = gate or (lambda: True)
        # Multi-item mode: when ``catalog_items`` is provided, the
        # watcher counts every taught item each tick (up to
        # ``max_items_per_tick``). Set ``FM_VISION_MAX_ITEMS=1`` to
        # fall back to active-only.
        self._get_catalog_items: Callable[[], list[Item]] | None = catalog_items
        self._max_items_per_tick = (
            max_items_per_tick
            if max_items_per_tick is not None
            else int(os.environ.get("FM_VISION_MAX_ITEMS", "4"))
        )
        self._inventory = inventory
        self._camera = camera
        self._model = model
        # 0.5 s poll lets the motion gate sample the freshest streamer
        # frame and fire inference within ~half a second of a scene
        # change. The model itself takes ~1-2 s per tick, but the
        # poll/inference loop overlaps via run_in_executor so the
        # next poll happens while the current tick is finishing.
        # Override with FM_VISION_POLL_SECONDS.
        self._poll_interval = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else float(os.environ.get("FM_VISION_POLL_SECONDS", "0.5"))
        )
        # 3% byte-length change is enough to catch a single fruit
        # being moved — 8% needed visible hand intrusion. Lower
        # threshold = more frequent ticks on subtle changes, slight
        # risk of camera-noise false positives (harmless: model
        # just re-runs and returns the same count).
        self._motion_threshold = (
            motion_threshold
            if motion_threshold is not None
            else float(os.environ.get("FM_VISION_MOTION_THRESHOLD", "3.0"))
        )
        # Heartbeat: even with motion gating active, force one count
        # every ``heartbeat_seconds`` so a perfectly-still scene doesn't
        # show a stale physical_count forever (and so we notice if the
        # camera silently froze).
        # 5 s heartbeat (was 30 s) so a frozen camera daemon shows
        # up as "stale count" within a single demo step instead of
        # hiding for half a minute. With a 0.5 s poll + 3% motion
        # threshold the heartbeat almost never fires for normal
        # use — it's a safety net for static scenes / silent freezes.
        self._heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else float(os.environ.get("FM_VISION_HEARTBEAT_SECONDS", "5"))
        )
        # Stability window: only write to inventory after the model
        # has returned the same count for N CONSECUTIVE ticks. This
        # dampens single-tick flickers from borderline detections
        # (the model occasionally sees 3 fruits as 4 when one is
        # partially occluded). Trade-off: one extra tick of latency
        # before count changes propagate.
        #
        # Default N=1 (write every tick) because the cross-class
        # dedup in the tick handler already kills the dominant
        # noise source (PaliGemma's "apple sometimes called
        # banana"). Smoothing here was masking the symptom and
        # making the kiosk feel laggy on real fruit moves. Bump
        # to 2+ via FM_VISION_STABILITY_TICKS if a particular
        # camera/lighting combo still flickers.
        self._stability_ticks = max(
            1, int(os.environ.get("FM_VISION_STABILITY_TICKS", "1"))
        )
        # Per-item rolling buffer of the last N model reads — the
        # value the watcher commits to inventory is the latest read
        # IFF every entry in the buffer agrees. Distinct from
        # ``status.last_counts`` (which is the most recent COMMITTED
        # count, used for change-classification).
        self._recent_reads: dict[str, list[int]] = {}
        self._previous_frame: bytes | None = None
        # Freshest snapshot for the kiosk's video feed. Distinct
        # from ``_previous_frame`` (motion-gate baseline) — that
        # one only updates when a model call actually fires.
        self._latest_frame: bytes | None = None
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
        # Gate: video streams continuously, but model inference is
        # held back until the operator (or the Pico START button)
        # opens the gate. Lets judges see the live shelf before the
        # AI kicks in, then watch the chips light up the moment
        # the demo starts.
        if not self._gate():
            self.status.last_tick_ok = True
            return

        # Decide which items to count this cycle. Multi-item mode
        # (``catalog_items`` provided) counts everything taught, up
        # to ``max_items_per_tick``. Active-only mode is the
        # fallback when no multi-item callback was given.
        active: Item | None = self._get_active_item()
        items_to_count: list[Item] = self._select_items_to_count(active)
        if not items_to_count:
            # Nothing to count yet. Don't burn cycles on snapshots.
            self.status.last_active_item = active.id if active else None
            self.status.last_tick_ok = True
            return

        self.status.last_active_item = active.id if active else None

        # Run the blocking IO (camera + model) on the default
        # executor so the asyncio loop stays responsive.
        loop = asyncio.get_running_loop()
        frame = await loop.run_in_executor(None, self._camera.snapshot)
        # Cache the freshest frame so the kiosk's ``/api/camera/frame.jpg``
        # endpoint has something to serve. We update this even when
        # the motion gate suppresses the count call below — the UI
        # should always see the most recent capture.
        self._latest_frame = frame

        # Motion gate, with a heartbeat override: if the last
        # successful count is older than ``heartbeat_seconds``, run
        # the model anyway so a static scene can't hide a frozen
        # camera or a stale count.
        now = time.monotonic()
        stale = (
            self.status.last_count_at_monotonic == 0.0
            or now - self.status.last_count_at_monotonic >= self._heartbeat_seconds
        )
        if self._is_unchanged(frame) and not stale:
            self.status.last_skipped_motion = True
            self.status.last_tick_ok = True
            return
        self.status.last_skipped_motion = False
        self._previous_frame = frame

        # One detect call PER ITEM, then CROSS-CLASS dedup before
        # we count boxes. This fixes the "apple sometimes counted
        # as banana" failure mode: PaliGemma's ``detect banana``
        # will occasionally draw a box around an apple, and intra-
        # class NMS can't catch that (the box doesn't overlap with
        # other "banana" boxes). Cross-class dedup catches it by
        # noticing the same physical region is claimed by both
        # ``detect apple`` and ``detect banana`` and keeping only
        # the larger box (a proxy for "the class that saw it
        # more confidently").
        change_ts = time.time()
        per_class_boxes: dict[str, list[tuple[int, int, int, int]]] = {}
        for item in items_to_count:
            try:
                per_class_boxes[item.name] = await loop.run_in_executor(
                    None, self._model.detect_boxes, frame, item.name
                )
            except Exception:
                logger.exception("model detect failed for %r", item.name)
                # Don't put a key in per_class_boxes for failures;
                # the stability buffer for this noun stays unchanged
                # and the count stays at its previous committed value.

        # Cross-class dedup: drop boxes that overlap (IoU >= 0.5)
        # across classes. Keep the larger box in any conflict.
        deduped = _cross_class_nms(per_class_boxes, _CROSS_CLASS_IOU)
        batch: dict[str, int] = {name: len(boxes) for name, boxes in deduped.items()}

        # Now iterate items to update inventory + activity. We
        # preserve the per-item failure tolerance: a missing noun
        # in the result just means the model saw zero of it
        # (detect returns no boxes for absent objects). A wholesale
        # batch failure leaves all items untouched and falls into
        # the "no counts" branch below.
        counts: dict[str, int] = {}
        for item in items_to_count:
            if item.name not in batch:
                continue
            raw_read = batch[item.name]

            # Stability check: push the raw read into the rolling
            # buffer, then only commit if the buffer is full AND
            # every entry agrees. This is the "see it N times
            # before believing it" trick — dampens single-tick
            # flickers from borderline detections without adding
            # much latency (one extra tick = ~1.5 s at the default
            # poll rate).
            buf = self._recent_reads.setdefault(item.name, [])
            buf.append(raw_read)
            if len(buf) > self._stability_ticks:
                buf.pop(0)
            if len(buf) < self._stability_ticks or len(set(buf)) > 1:
                # Not enough history yet, or readings disagree —
                # don't commit. The previous committed count
                # stays visible on the kiosk + inventory.
                continue

            count = raw_read
            counts[item.name] = count

            # Detect change vs the previous COMMITTED tick. Anything
            # other than "unchanged" lands in the ring buffer that
            # powers the judge-facing Activity panel + the Pico's
            # flash LED.
            prev_count = self.status.last_counts.get(item.name)
            kind = _classify_change(prev_count, count)
            if kind == "unchanged":
                # Re-confirming an existing count is the happy path;
                # don't spam the Activity panel with no-op rows.
                continue
            self.status.recent_changes.append({
                "ts": change_ts,
                "item_id": item.id,
                "item_name": item.name,
                "prev": prev_count,
                "new": count,
                "delta": count - (prev_count or 0),
                "kind": kind,
            })
            # Trim the ring buffer.
            if len(self.status.recent_changes) > MAX_CHANGE_HISTORY:
                self.status.recent_changes = self.status.recent_changes[
                    -MAX_CHANGE_HISTORY:
                ]

            self._inventory.reconcile_physical_count(
                item_id=item.id,
                count=count,
                source="model",
                confidence=0.9,
            )

        # ``batch`` is the raw model reads for this tick; ``counts``
        # is only the items that PASSED the stability gate (the
        # model returned the same value N ticks in a row). It's
        # legitimate for ``counts`` to be empty when ``batch`` was
        # populated — that just means nothing stabilized this tick.
        # Only a wholesale model misfire (empty batch) counts as a
        # failure.
        if not batch:
            self.status.consecutive_failures += 1
            self.status.last_tick_ok = False
            return

        # Update the "last successful model read" timestamp on EVERY
        # tick where the model returned something — including ticks
        # where nothing stabilized yet. The motion-gate heartbeat
        # uses this to decide whether to force a re-read, and it
        # should react to model activity, not to commits.
        self.status.last_count_at_monotonic = time.monotonic()

        if counts:
            self.status.last_counts.update(counts)
            # Back-compat: ``last_count`` is the active item's count
            # if we have one, else the first newly-committed noun.
            if active is not None and active.name in counts:
                self.status.last_count = counts[active.name]
            else:
                self.status.last_count = next(iter(counts.values()))
        self.status.consecutive_failures = 0
        self.status.last_tick_ok = True

    def _select_items_to_count(self, active: Item | None) -> list[Item]:
        """Multi-item mode if a catalog callback was provided;
        otherwise just the active item."""

        if self._get_catalog_items is not None:
            items = self._get_catalog_items()
            if items:
                return items[: max(1, self._max_items_per_tick)]
        if active is None:
            return []
        return [active]

    # ─── public reads ─────────────────────────────────────────────

    @property
    def latest_frame(self) -> bytes | None:
        """Freshest camera snapshot bytes, or ``None`` until the
        watcher has ticked at least once. Served by
        ``/api/camera/frame.jpg`` for the kiosk's live feed."""

        return self._latest_frame

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
