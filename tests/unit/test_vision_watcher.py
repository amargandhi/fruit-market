"""Vision watcher with mocked camera + model.

We don't load MLX in unit tests. Instead we hand the watcher fake
camera/model objects that produce deterministic frames + counts so
the asyncio + reconcile wiring can be tested without GPU.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from fruit_market.services.factory import make_real_services
from fruit_market.state.store import EventStore
from fruit_market.vision.watcher import VisionWatcher


@dataclass
class _FakeCamera:
    """Returns the same JPEG-shaped blob on every snapshot unless
    you call ``stir()`` to perturb the bytes (simulating motion)."""

    payload: bytes = b"jpegjpegjpeg"
    snapshots: list[bytes] = field(default_factory=list)

    def snapshot(self) -> bytes:
        self.snapshots.append(self.payload)
        return self.payload

    def stir(self, extra: bytes = b"x" * 32) -> None:
        self.payload = self.payload + extra


@dataclass
class _FakeModel:
    """Returns counts from a queue, falling back to ``default`` when
    the queue is empty.

    Implements every shape the watcher consumes:
      * ``count`` (single-noun integer)
      * ``count_batch`` (multi-noun integers in one call)
      * ``detect_boxes`` (single-noun → list of synthetic boxes,
        one per integer in the count, spaced so they don't overlap
        intra-class but DO overlap cross-class for whatever you
        want to test).

    Each ``detect_boxes`` call generates non-overlapping unit boxes
    in the top-left grid; tests that need cross-class overlap
    should set ``box_overrides[noun]`` to a hand-crafted list.
    """

    counts: list[int] = field(default_factory=list)
    default: int = 0
    calls: list[tuple[bytes, str]] = field(default_factory=list)
    box_overrides: dict[str, list[tuple[int, int, int, int]]] = field(
        default_factory=dict,
    )

    def count(self, image: bytes, noun: str) -> int:
        self.calls.append((image, noun))
        return self.counts.pop(0) if self.counts else self.default

    def count_batch(self, image: bytes, nouns: list[str]) -> dict[str, int]:
        return {noun: self.count(image, noun) for noun in nouns}

    def detect_boxes(self, image: bytes, noun: str) -> list[tuple[int, int, int, int]]:
        self.calls.append((image, noun))
        if noun in self.box_overrides:
            return list(self.box_overrides[noun])
        n = self.counts.pop(0) if self.counts else self.default
        # Non-overlapping 10x10 boxes laid out left-to-right at y=0.
        return [(0, i * 20, 10, i * 20 + 10) for i in range(n)]


@pytest.mark.asyncio
async def test_watcher_writes_count_to_inventory(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    item = services.teach.confirm(
        services.teach.propose("These are bananas, $1.00, 6 of them").id
    )

    camera = _FakeCamera()
    # default=3 so every tick returns the same count — the test
    # doesn't care how many ticks fired, only that the count
    # reached inventory.
    model = _FakeModel(default=3)
    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        inventory=services.inventory,
        camera=camera,
        model=model,
        poll_interval_seconds=0.05,
        motion_threshold=0.0,  # always treat as motion → always count
    )
    await watcher.start()
    await asyncio.sleep(0.1)
    await watcher.stop()

    assert services.inventory.get_physical_count(item.id) == 3
    assert watcher.status.last_count == 3
    assert watcher.status.last_active_item == item.id


@pytest.mark.asyncio
async def test_watcher_skips_model_when_image_unchanged(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    services.teach.confirm(
        services.teach.propose("These are bananas, $1.00, 6 of them").id
    )

    camera = _FakeCamera()
    model = _FakeModel(counts=[5, 4, 3])
    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        inventory=services.inventory,
        camera=camera,
        model=model,
        poll_interval_seconds=0.05,
        motion_threshold=99.0,  # everything below 99% delta is "no motion"
    )
    await watcher.start()
    await asyncio.sleep(0.2)
    await watcher.stop()

    # First tick: previous_frame is None → counts once.
    # Subsequent ticks: identical frame → skipped, model not called again.
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_watcher_idle_when_no_active_item(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)

    camera = _FakeCamera()
    model = _FakeModel()
    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        inventory=services.inventory,
        camera=camera,
        model=model,
        poll_interval_seconds=0.05,
    )
    await watcher.start()
    await asyncio.sleep(0.15)
    await watcher.stop()

    # No active item → watcher must not snapshot or call the model.
    assert camera.snapshots == []
    assert model.calls == []
    assert watcher.status.last_active_item is None


@pytest.mark.asyncio
async def test_watcher_heartbeat_forces_count_when_scene_is_static(tmp_path) -> None:
    """A perfectly-still scene must still get refreshed counts after
    the heartbeat interval — the motion gate alone would freeze the
    physical_count forever."""

    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    item = services.teach.confirm(
        services.teach.propose("These are bananas, $1.00, 6 of them").id
    )

    camera = _FakeCamera()
    model = _FakeModel(default=2)
    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        inventory=services.inventory,
        camera=camera,
        model=model,
        poll_interval_seconds=0.02,
        motion_threshold=99.0,  # everything is "no motion"
        heartbeat_seconds=0.05,  # short heartbeat for the test
    )
    await watcher.start()
    # Wait long enough for: initial tick + at least one heartbeat override.
    await asyncio.sleep(0.2)
    await watcher.stop()

    # First tick always runs (previous_frame is None → not motion-gated),
    # then the heartbeat should force at least one additional count
    # despite the motion gate. Without the heartbeat, model.calls would
    # stay at 1 forever.
    assert len(model.calls) >= 2, f"heartbeat did not fire: only {len(model.calls)} calls"
    assert services.inventory.get_physical_count(item.id) == 2


@pytest.mark.asyncio
async def test_watcher_survives_model_exception(tmp_path) -> None:
    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    item = services.teach.confirm(
        services.teach.propose("These are bananas, $1.00, 6 of them").id
    )

    class _BoomModel:
        def count(self, image: bytes, noun: str) -> int:
            raise RuntimeError("model misfire")

        def count_batch(self, image: bytes, nouns: list[str]) -> dict[str, int]:
            raise RuntimeError("model misfire")

        def detect_boxes(
            self, image: bytes, noun: str,
        ) -> list[tuple[int, int, int, int]]:
            raise RuntimeError("model misfire")

    camera = _FakeCamera()
    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        inventory=services.inventory,
        camera=camera,
        model=_BoomModel(),  # type: ignore[arg-type]
        poll_interval_seconds=0.05,
        motion_threshold=0.0,
    )
    await watcher.start()
    await asyncio.sleep(0.15)
    await watcher.stop()

    # Watcher swallowed the exception, kept ticking, counted failures.
    assert watcher.status.consecutive_failures >= 1
    # Inventory remains at the initial count — no bogus write.
    assert services.inventory.get_physical_count(item.id) == 6


# ─── Cross-class dedup ─────────────────────────────────────────────


def test_cross_class_nms_drops_smaller_overlapping_box() -> None:
    """Same physical fruit claimed by two classes — the smaller
    box loses so we don't double-count.

    Real-world failure mode: PaliGemma's ``detect banana`` draws
    a box around an apple while ``detect apple`` also draws one
    on the same fruit. Without cross-class dedup, banana count
    gets a phantom +1.
    """

    from fruit_market.vision.watcher import _cross_class_nms

    per_class = {
        "apple": [(0, 0, 100, 100)],   # large, correct apple box
        "banana": [(5, 5, 95, 95)],    # smaller box ~80% inside the apple
    }
    deduped = _cross_class_nms(per_class, iou_threshold=0.5)
    # Apple's larger box survives; banana's phantom box is dropped.
    assert deduped["apple"] == [(0, 0, 100, 100)]
    assert deduped["banana"] == []


def test_cross_class_nms_keeps_distinct_fruits_in_different_regions() -> None:
    """Apple in one corner, banana in the other — both must survive."""

    from fruit_market.vision.watcher import _cross_class_nms

    per_class = {
        "apple": [(0, 0, 100, 100)],
        "banana": [(200, 200, 300, 300)],
    }
    deduped = _cross_class_nms(per_class, iou_threshold=0.5)
    assert len(deduped["apple"]) == 1
    assert len(deduped["banana"]) == 1


def test_cross_class_nms_handles_multiple_overlaps() -> None:
    """3 apples + 3 bananas with 1 cross-class overlap should leave
    3 apples + 2 bananas (the overlapping banana box, being smaller,
    drops)."""

    from fruit_market.vision.watcher import _cross_class_nms

    per_class = {
        "apple": [
            (0, 0, 100, 100),       # apple A
            (0, 200, 100, 300),     # apple B
            (0, 400, 100, 500),     # apple C
        ],
        "banana": [
            (5, 5, 95, 95),         # claimed banana, but on apple A — DROP
            (200, 0, 280, 80),      # genuine banana D
            (200, 200, 280, 280),   # genuine banana E
        ],
    }
    deduped = _cross_class_nms(per_class, iou_threshold=0.5)
    assert len(deduped["apple"]) == 3
    assert len(deduped["banana"]) == 2
