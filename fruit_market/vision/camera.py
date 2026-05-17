"""USB camera snapshot via OpenCV / AVFoundation on macOS.

Single responsibility: open the camera, return a JPEG ``bytes``
snapshot. Recovering from "camera went to sleep" / "another process
grabbed it" is the caller's concern — we raise
:class:`CameraUnavailableError` and let the watcher decide whether
to retry or escalate.

The default device index is read from ``FM_CAMERA_INDEX`` (env) or
falls back to ``0``. Resolution is configurable for the demo
(higher → slower model inference; lower → cheaper but counts can
get fuzzy).
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import cv2


class CameraUnavailableError(RuntimeError):
    """Raised when no frame can be read from the configured device."""


class Camera:
    """Thin wrapper around ``cv2.VideoCapture`` with a lock.

    OpenCV's VideoCapture is not thread-safe; the lock here lets the
    asyncio watcher snapshot from one thread while a debug endpoint
    snapshots from another without crashing the underlying device.
    """

    def __init__(
        self,
        device_index: int | None = None,
        width: int = 640,
        height: int = 480,
        jpeg_quality: int = 85,
    ) -> None:
        self._device_index = (
            device_index
            if device_index is not None
            else int(os.environ.get("FM_CAMERA_INDEX", "0"))
        )
        self._width = width
        self._height = height
        self._jpeg_quality = jpeg_quality
        self._lock = threading.Lock()
        self._cap: cv2.VideoCapture | None = None

    def _ensure_open(self) -> cv2.VideoCapture:
        if self._cap is not None and self._cap.isOpened():
            return self._cap
        # Defer the import: opencv-python is in the optional `vision`
        # extra, and we want unit tests on a Linux CI machine to be
        # able to import this module without cv2 installed.
        import cv2  # noqa: PLC0415

        cap = cv2.VideoCapture(self._device_index, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            # Fall back to the platform default backend.
            cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            raise CameraUnavailableError(
                f"could not open camera at index {self._device_index}"
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap = cap
        return cap

    def snapshot(self) -> bytes:
        """Grab one frame and return it as JPEG bytes.

        Drops the first two frames after opening the device — webcam
        auto-exposure typically takes 1-2 frames to settle and the
        first frame is often black.
        """

        import cv2  # noqa: PLC0415

        with self._lock:
            cap = self._ensure_open()
            # Warm-up frames are cheap; only do them right after open.
            warmup_needed = getattr(cap, "_fm_warmed", False) is False
            if warmup_needed:
                for _ in range(2):
                    cap.read()
                cap._fm_warmed = True

            ok, frame = cap.read()
            if not ok or frame is None:
                # Drop the handle so the next call re-opens.
                cap.release()
                self._cap = None
                raise CameraUnavailableError("camera read failed")

            ok, buf = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality],
            )
            if not ok:
                raise CameraUnavailableError("jpeg encode failed")
            return bytes(buf)

    def close(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
