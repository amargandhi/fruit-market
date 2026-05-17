"""CameraStreamer + StreamerCamera tests.

We don't load real cv2 / mlx-vlm here — a fake camera with a
controllable frame queue exercises every code path: start/stop,
cache freshness, motion-score updates, error backoff, the
StreamerCamera adapter and its stale-cache rejection.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import pytest

from fruit_market.vision.camera import CameraUnavailableError
from fruit_market.vision.streamer import CameraStreamer, StreamerCamera


@dataclass
class _FakeCamera:
    """Returns frames from a list; cycles when exhausted. Raises if
    ``raise_after`` is set and ``snapshots`` has been called that
    many times — useful for testing failure backoff."""

    frames: list[bytes] = field(
        default_factory=lambda: [b"\xff\xd8\xff" + b"x" * 200]
    )
    raise_after: int | None = None
    snapshots: int = 0

    def snapshot(self) -> bytes:
        self.snapshots += 1
        if self.raise_after is not None and self.snapshots > self.raise_after:
            raise CameraUnavailableError("simulated failure")
        return self.frames[(self.snapshots - 1) % len(self.frames)]

    def close(self) -> None:
        pass


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ─── basic capture loop ─────────────────────────────────────────────


def test_streamer_caches_first_frame_after_start() -> None:
    camera = _FakeCamera(frames=[b"\xff\xd8\xff" + b"A" * 200])
    streamer = CameraStreamer(camera, idle_fps=20, burst_fps=20)
    streamer.start()
    try:
        assert _wait_until(lambda: streamer.latest_frame is not None), (
            "streamer never produced a frame"
        )
        assert streamer.latest_frame is not None
        assert streamer.latest_frame.startswith(b"\xff\xd8\xff")
        assert camera.snapshots >= 1
    finally:
        streamer.stop()


def test_streamer_keeps_capturing_until_stopped() -> None:
    camera = _FakeCamera()
    streamer = CameraStreamer(camera, idle_fps=50, burst_fps=50)
    streamer.start()
    try:
        # Wait long enough for multiple captures.
        time.sleep(0.2)
        captures_at_t = camera.snapshots
        assert captures_at_t >= 3, f"too few captures: {captures_at_t}"
    finally:
        streamer.stop()
    # After stop, no more captures.
    snapshots_at_stop = camera.snapshots
    time.sleep(0.1)
    assert camera.snapshots == snapshots_at_stop, (
        "streamer kept capturing after stop()"
    )


def test_streamer_stop_is_idempotent() -> None:
    streamer = CameraStreamer(_FakeCamera(), idle_fps=20, burst_fps=20)
    streamer.start()
    streamer.stop()
    streamer.stop()  # second call must not raise
    streamer.start()  # restartable
    time.sleep(0.05)
    streamer.stop()


def test_streamer_start_is_idempotent_when_running() -> None:
    camera = _FakeCamera()
    streamer = CameraStreamer(camera, idle_fps=50, burst_fps=50)
    streamer.start()
    streamer.start()  # second call must not spawn a second thread
    time.sleep(0.1)
    # Count of live threads named fm-camera-streamer — should be exactly 1.
    live = [t for t in threading.enumerate() if t.name == "fm-camera-streamer"]
    streamer.stop()
    assert len(live) == 1, f"second start spawned an extra thread: {len(live)}"


# ─── motion score ───────────────────────────────────────────────────


def test_motion_score_zero_for_identical_frames() -> None:
    streamer = CameraStreamer(
        _FakeCamera(frames=[b"\xff\xd8\xff" + b"A" * 1000]),
        idle_fps=50,
        burst_fps=50,
    )
    streamer.start()
    try:
        assert _wait_until(lambda: streamer.latest_frame is not None)
        time.sleep(0.1)
        # Same frame returned repeatedly → motion score stays at 0.
        assert streamer.motion_score == 0.0
        assert streamer.is_motion is False
    finally:
        streamer.stop()


def test_motion_score_jumps_on_size_change() -> None:
    frames = [
        b"\xff\xd8\xff" + b"A" * 1000,  # 1003 bytes
        b"\xff\xd8\xff" + b"A" * 1500,  # 1503 bytes — +50% delta
    ]
    streamer = CameraStreamer(
        _FakeCamera(frames=frames),
        idle_fps=50,
        burst_fps=50,
        motion_threshold_pct=10.0,
    )
    streamer.start()
    try:
        # Need at least 2 captures for the size delta to register.
        assert _wait_until(lambda: streamer.motion_score > 10.0), (
            f"motion score never crossed threshold: {streamer.motion_score}"
        )
        assert streamer.is_motion is True
    finally:
        streamer.stop()


# ─── failure handling ──────────────────────────────────────────────


def test_streamer_logs_failure_and_keeps_running() -> None:
    camera = _FakeCamera(raise_after=0)  # every snapshot raises
    streamer = CameraStreamer(camera, idle_fps=50, burst_fps=50)
    streamer.start()
    try:
        time.sleep(0.5)
        status = streamer.status()
        assert status["failures"] > 0
        assert status["consecutive_failures"] > 0
        assert status["last_error"] == "simulated failure"
        assert status["frame_bytes"] == 0
    finally:
        streamer.stop()


def test_streamer_recovers_after_failure() -> None:
    """If snapshot starts succeeding again, the streamer resumes."""

    class FlakyCamera:
        def __init__(self) -> None:
            self.snapshots = 0
            self.allowed_after = 3

        def snapshot(self) -> bytes:
            self.snapshots += 1
            if self.snapshots <= self.allowed_after:
                raise CameraUnavailableError("warming up")
            return b"\xff\xd8\xff" + b"x" * 100

        def close(self) -> None:
            pass

    camera = FlakyCamera()
    streamer = CameraStreamer(camera, idle_fps=50, burst_fps=50)
    streamer.start()
    try:
        assert _wait_until(
            lambda: streamer.latest_frame is not None,
            # Failure backoff is ~2s; give it a generous window.
            timeout=10.0,
        ), f"recovery never happened: {streamer.status()}"
        assert streamer.status()["consecutive_failures"] == 0
    finally:
        streamer.stop()


# ─── StreamerCamera adapter ────────────────────────────────────────


def test_streamer_camera_returns_latest() -> None:
    streamer = CameraStreamer(_FakeCamera(), idle_fps=50, burst_fps=50)
    streamer.start()
    try:
        assert _wait_until(lambda: streamer.latest_frame is not None)
        adapter = StreamerCamera(streamer)
        frame = adapter.snapshot()
        assert frame == streamer.latest_frame
    finally:
        streamer.stop()


def test_streamer_camera_raises_before_first_capture() -> None:
    streamer = CameraStreamer(_FakeCamera(), idle_fps=1, burst_fps=1)
    # Don't start it — no captures will happen.
    adapter = StreamerCamera(streamer)
    with pytest.raises(CameraUnavailableError, match="has not captured"):
        adapter.snapshot()


def test_streamer_camera_rejects_stale_cache(monkeypatch) -> None:
    streamer = CameraStreamer(_FakeCamera(), idle_fps=50, burst_fps=50)
    streamer.start()
    try:
        assert _wait_until(lambda: streamer.latest_frame is not None)
    finally:
        streamer.stop()
    # After stop, the cache won't refresh. With a tiny stale_after,
    # the adapter should raise after the threshold passes.
    adapter = StreamerCamera(streamer, stale_after_seconds=0.05)
    time.sleep(0.1)
    with pytest.raises(CameraUnavailableError, match="stale"):
        adapter.snapshot()


# ─── status surface ────────────────────────────────────────────────


def test_status_reports_running_and_cadence() -> None:
    streamer = CameraStreamer(_FakeCamera(), idle_fps=2, burst_fps=4)
    streamer.start()
    try:
        assert _wait_until(lambda: streamer.latest_frame is not None)
        status = streamer.status()
        assert status["running"] is True
        assert status["idle_fps"] == 2
        assert status["burst_fps"] == 4
        assert status["frame_bytes"] > 0
        assert status["captures"] >= 1
    finally:
        streamer.stop()
