"""Continuous background camera capture into a shared JPEG cache.

Why this exists: the model only needs a fresh frame every few
seconds, but the kiosk's ``<img>`` tag wants to see one every few
hundred milliseconds for it to feel "live". Tying the HTTP-served
frame to the model's poll rate makes the camera feel frozen.

The streamer runs its own thread:

* Continuously grabs frames from the wrapped backend.
* Stashes the latest JPEG bytes in a lock-guarded slot.
* Computes a cheap byte-length motion score between consecutive
  frames so the watcher's motion gate has something better than
  the model's own 3-second cadence.
* Adapts capture rate: ``burst_fps`` while motion is above
  ``motion_threshold_pct``, ``idle_fps`` otherwise.

Two consumers read the same slot:

* :class:`StreamerCamera` — duck-typed ``snapshot()`` adapter for
  :class:`fruit_market.vision.watcher.VisionWatcher`. The watcher
  doesn't even know it's reading from a cache.
* The ``/api/camera/frame.jpg`` HTTP endpoint — fetches
  ``streamer.latest_frame`` and returns it immediately, no V4L2
  race with the watcher.

Backend defaults (overrideable via env / kwargs):

* ``cv2``    → 5 FPS idle / 8 FPS burst (cheap local capture)
* ``broker`` → 0.5 FPS idle / 1 FPS burst (Swift subprocess takes
  ~1.5 s per capture; faster than that would waste CPU)
* ``file``   → 2 FPS idle / 4 FPS burst (cheap file read; lets the
  user swap the pinned JPEG and see the new image within ~500 ms)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from fruit_market.vision.camera import (
    BrokerCamera,
    CameraUnavailableError,
    Cv2Camera,
    FileCamera,
)

if TYPE_CHECKING:
    from fruit_market.vision.camera import CameraBackend


logger = logging.getLogger("fm.vision.streamer")


# Default cadences per backend type. The streamer asks the backend
# class what it should run at; explicit env overrides win.
_BACKEND_DEFAULTS: dict[type, tuple[float, float]] = {
    Cv2Camera: (5.0, 8.0),
    BrokerCamera: (0.5, 1.0),
    FileCamera: (2.0, 4.0),
}


def _resolve_fps(camera: CameraBackend) -> tuple[float, float]:
    """Pick (idle_fps, burst_fps) based on env first, backend second."""

    env_idle = os.environ.get("FM_CAMERA_STREAM_IDLE_FPS")
    env_burst = os.environ.get("FM_CAMERA_STREAM_BURST_FPS")
    idle_default, burst_default = 1.0, 2.0
    for backend_cls, (idle, burst) in _BACKEND_DEFAULTS.items():
        if isinstance(camera, backend_cls):
            idle_default, burst_default = idle, burst
            break
    idle = float(env_idle) if env_idle else idle_default
    burst = float(env_burst) if env_burst else burst_default
    return idle, burst


class CameraStreamer:
    """Background thread that keeps a freshest-wins JPEG cache.

    Thread-safe: ``latest_frame`` can be read from any number of
    consumers concurrently. The capture thread itself is the only
    writer.
    """

    def __init__(
        self,
        camera: CameraBackend,
        *,
        idle_fps: float | None = None,
        burst_fps: float | None = None,
        motion_threshold_pct: float | None = None,
    ) -> None:
        self._camera = camera

        # Resolve cadence: explicit args win over env which wins
        # over per-backend defaults.
        default_idle, default_burst = _resolve_fps(camera)
        self._idle_fps = idle_fps if idle_fps is not None else default_idle
        self._burst_fps = burst_fps if burst_fps is not None else default_burst
        self._idle_period = 1.0 / max(self._idle_fps, 0.1)
        self._burst_period = 1.0 / max(self._burst_fps, 0.1)
        self._motion_threshold = (
            motion_threshold_pct
            if motion_threshold_pct is not None
            else float(os.environ.get("FM_CAMERA_STREAM_MOTION_PCT", "8.0"))
        )

        self._lock = threading.Lock()
        self._latest_frame: bytes | None = None
        self._latest_monotonic: float = 0.0
        self._previous_for_motion: bytes | None = None
        self._motion_score: float = 0.0

        self._captures: int = 0
        self._failures: int = 0
        self._consecutive_failures: int = 0
        self._last_error: str | None = None

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ─── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Begin capturing in the background. Idempotent."""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(
            target=self._run,
            name="fm-camera-streamer",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        logger.info(
            "streamer started: idle=%.1f fps burst=%.1f fps motion>%.1f%%",
            self._idle_fps, self._burst_fps, self._motion_threshold,
        )

    def stop(self, timeout_seconds: float = 2.0) -> None:
        """Signal the thread to stop and wait briefly for it to die."""

        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
            self._thread = None

    # ─── public reads ────────────────────────────────────────────

    @property
    def latest_frame(self) -> bytes | None:
        """Most recent JPEG bytes, or None until the first capture."""

        with self._lock:
            return self._latest_frame

    @property
    def latest_frame_age_seconds(self) -> float | None:
        """Wall-clock age of ``latest_frame``. None until first capture."""

        with self._lock:
            if self._latest_monotonic == 0.0:
                return None
            return time.monotonic() - self._latest_monotonic

    @property
    def motion_score(self) -> float:
        """Most recent inter-frame change percentage (0-100+)."""

        with self._lock:
            return self._motion_score

    @property
    def is_motion(self) -> bool:
        return self.motion_score >= self._motion_threshold

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "idle_fps": self._idle_fps,
                "burst_fps": self._burst_fps,
                "motion_score": round(self._motion_score, 2),
                "motion_threshold": self._motion_threshold,
                "frame_age_seconds": (
                    round(time.monotonic() - self._latest_monotonic, 3)
                    if self._latest_monotonic > 0
                    else None
                ),
                "frame_bytes": len(self._latest_frame) if self._latest_frame else 0,
                "captures": self._captures,
                "failures": self._failures,
                "consecutive_failures": self._consecutive_failures,
                "last_error": self._last_error,
            }

    # ─── main loop ───────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                frame = self._camera.snapshot()
                self._record_frame(frame)
                self._consecutive_failures = 0
                self._last_error = None
                self._captures += 1
            except CameraUnavailableError as exc:
                self._failures += 1
                self._consecutive_failures += 1
                self._last_error = str(exc)
                if self._consecutive_failures in (1, 5, 25):
                    # Log on first failure + occasionally after,
                    # but don't spam every tick when the camera is
                    # genuinely down for minutes.
                    logger.warning(
                        "streamer capture failed (%d consecutive): %s",
                        self._consecutive_failures, exc,
                    )
            except Exception as exc:  # noqa: BLE001
                # Unexpected — log with traceback once so we can
                # diagnose, then back off.
                self._failures += 1
                self._consecutive_failures += 1
                self._last_error = str(exc)
                if self._consecutive_failures == 1:
                    logger.exception("streamer hit unexpected error")

            elapsed = time.monotonic() - t0
            target_period = (
                self._burst_period if self.is_motion else self._idle_period
            )
            # On repeated failure, back off so we don't spin a busy loop.
            if self._consecutive_failures > 0:
                target_period = max(target_period, 2.0)

            sleep_for = max(0.0, target_period - elapsed)
            # Use the stop event's wait() so a stop() call interrupts
            # the sleep immediately.
            self._stop.wait(sleep_for)

    def _record_frame(self, frame: bytes) -> None:
        """Update the cache + motion score under the lock."""

        with self._lock:
            previous = self._previous_for_motion
            self._latest_frame = frame
            self._latest_monotonic = time.monotonic()
            self._previous_for_motion = frame
            if previous is None or len(previous) == 0 or len(frame) == 0:
                self._motion_score = 0.0
                return
            # Cheap JPEG-byte-length diff. Real cameras encoded by
            # the same camera + same scene produce near-identical
            # byte counts; any movement disrupts the entropy and
            # shifts the count noticeably.
            delta = abs(len(frame) - len(previous))
            base = max(len(previous), 1)
            self._motion_score = (delta / base) * 100.0


# ─── Adapter so VisionWatcher reads from the cache, not the camera ─


class StreamerCamera:
    """``snapshot()`` adapter that reads from a :class:`CameraStreamer`.

    Plug this into :class:`fruit_market.vision.watcher.VisionWatcher`
    instead of a raw camera backend. The watcher gets the most
    recently captured frame without re-hitting the device — which
    means the model's poll cadence stays decoupled from the
    streamer's capture cadence.

    Raises :class:`CameraUnavailableError` only when the streamer
    has captured nothing yet **or** the cached frame is older than
    ``stale_after_seconds``. Otherwise returns the cached bytes.
    """

    def __init__(
        self,
        streamer: CameraStreamer,
        *,
        stale_after_seconds: float = 10.0,
    ) -> None:
        self._streamer = streamer
        self._stale_after = stale_after_seconds

    def snapshot(self) -> bytes:
        frame = self._streamer.latest_frame
        if frame is None:
            raise CameraUnavailableError(
                "streamer has not captured a frame yet"
            )
        age = self._streamer.latest_frame_age_seconds
        if age is not None and age > self._stale_after:
            raise CameraUnavailableError(
                f"streamer cache is stale ({age:.1f}s old, > {self._stale_after}s)"
            )
        return frame

    def close(self) -> None:
        # Streamer lifecycle is owned by the VisionBundle, not us.
        return


__all__ = ["CameraStreamer", "StreamerCamera"]
