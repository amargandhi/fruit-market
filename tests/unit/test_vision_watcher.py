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
    the queue is empty."""

    counts: list[int] = field(default_factory=list)
    default: int = 0
    calls: list[tuple[bytes, str]] = field(default_factory=list)

    def count(self, image: bytes, noun: str) -> int:
        self.calls.append((image, noun))
        return self.counts.pop(0) if self.counts else self.default


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
