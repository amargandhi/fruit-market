"""Camera snapshot — two backends behind one interface.

Single responsibility: open the camera, return a JPEG ``bytes``
snapshot. Recovering from "camera went to sleep" / "another process
grabbed it" is the caller's concern — we raise
:class:`CameraUnavailableError` and let the watcher decide whether
to retry or escalate.

Backends:

* :class:`Cv2Camera` — opencv-python + AVFoundation. Works when the
  Python process's parent app has been granted camera permission in
  macOS Privacy & Security (Terminal, iTerm, etc.). Indexes via
  ``FM_CAMERA_INDEX`` (default 0).
* :class:`BrokerCamera` — subprocess-calls ``apps/fm-camera/
  FruitMarketCamera.app/Contents/MacOS/fm-camera``. The helper .app
  has its own TCC entry, so this works even when Python's parent
  process is sandboxed. Selects the device by name substring via
  ``FM_CAMERA_DEVICE`` (default "C920").
* :class:`DaemonCamera` — reads frames from the long-running
  ``FruitMarketCamera.app --daemon`` HTTP endpoint. This is the
  default path when the daemon is already running.

The :func:`open_camera` factory picks one based on
``FM_CAMERA_BACKEND``: ``auto`` (default), ``daemon``, ``cv2``,
``broker``, or ``file``. Auto probes daemon first, then falls back to
cv2 and broker.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    import cv2


class CameraUnavailableError(RuntimeError):
    """Raised when no frame can be read from the configured device."""


class CameraBackend(Protocol):
    """Duck-typed contract: anything with snapshot() + close() works
    as the watcher's camera. _FakeCamera in tests conforms to this
    too."""

    def snapshot(self) -> bytes: ...

    def close(self) -> None: ...


class Cv2Camera:
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
        self._warmed: bool = False

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
            if not self._warmed:
                for _ in range(2):
                    cap.read()
                self._warmed = True

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
                self._warmed = False


# Keep the public name ``Camera`` pointing at the cv2 backend for
# back-compat with tests and the watcher fixture. New code should
# prefer the explicit class name or the ``open_camera`` factory.
Camera = Cv2Camera


# ─── BrokerCamera ──────────────────────────────────────────────────


# Path to the helper .app's main executable, relative to repo root.
# The .app is built by ``apps/fm-camera/build.sh``.
_DEFAULT_BROKER_BIN = "apps/fm-camera/FruitMarketCamera.app/Contents/MacOS/fm-camera"


class BrokerCamera:
    """Snapshot via the ``fm-camera`` helper binary.

    The helper is a code-signed .app bundle with its own TCC entry,
    so it can capture even when Python's parent process can't. We
    call it as a one-shot subprocess per snapshot.

    Settings (env):
      * ``FM_CAMERA_DEVICE`` — substring match against the device's
        ``localizedName`` (default ``"C920"``).
      * ``FM_CAMERA_BROKER_BIN`` — absolute path to the helper
        binary, overrides the default.

    Latency: ~1.5 s per snapshot (1 s auto-exposure settle + capture
    + write). That's fine for the 3 s watcher poll; if you need
    faster, drop the settle in main.swift.
    """

    def __init__(
        self,
        device_query: str | None = None,
        binary_path: str | None = None,
    ) -> None:
        self._device = device_query or os.environ.get("FM_CAMERA_DEVICE", "C920")
        # Resolve the binary path: env override, else default relative
        # to cwd (typically the repo root). We don't search PATH —
        # this binary lives inside the project tree by design.
        explicit = binary_path or os.environ.get("FM_CAMERA_BROKER_BIN")
        if explicit is not None:
            self._binary = Path(explicit)
        else:
            self._binary = Path.cwd() / _DEFAULT_BROKER_BIN
        self._lock = threading.Lock()

    def snapshot(self) -> bytes:
        if not self._binary.exists():
            raise CameraUnavailableError(
                f"fm-camera binary not found at {self._binary}. "
                f"Build it with: cd apps/fm-camera && ./build.sh"
            )

        with self._lock, tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False
        ) as tmp:
            output_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [str(self._binary), self._device, str(output_path)],
                capture_output=True,
                timeout=20,
            )
            if result.returncode != 0:
                msg = result.stderr.decode(errors="replace").strip()
                raise CameraUnavailableError(
                    f"fm-camera exit {result.returncode}: {msg}"
                )
            data = output_path.read_bytes()
            if not data:
                raise CameraUnavailableError("fm-camera produced empty file")
            return data
        finally:
            output_path.unlink(missing_ok=True)

    def close(self) -> None:
        # No persistent resources — each snapshot is a one-shot.
        return


# ─── FileCamera ────────────────────────────────────────────────────


class FileCamera:
    """Read a pinned JPEG from disk every tick.

    Used when:

    * The host process can't get macOS camera permission (e.g.
      the bridge is launched from a sandboxed parent like Claude
      Code), but you still want the full vision pipeline running
      against a real image of the scene.
    * Reproducible bench runs — pin the JPEG, vary the model or
      prompt, compare counts.

    Settings (env):
      * ``FM_CAMERA_FILE`` — path to the JPEG (default
        ``/tmp/fruit-market-smoke.jpg``).

    The file is re-read on every ``snapshot()`` so you can hot-swap
    the image during a demo by overwriting the file (e.g. with
    ``open`` to FruitMarketCamera.app or ``imagesnap``). Raises
    :class:`CameraUnavailableError` if the file is missing or empty.
    """

    def __init__(self, file_path: str | None = None) -> None:
        explicit = file_path or os.environ.get(
            "FM_CAMERA_FILE", "/tmp/fruit-market-smoke.jpg"
        )
        self._path = Path(explicit)
        self._lock = threading.Lock()

    def snapshot(self) -> bytes:
        with self._lock:
            if not self._path.exists():
                raise CameraUnavailableError(
                    f"FileCamera: {self._path} does not exist. "
                    "Capture an image (FruitMarketCamera.app, imagesnap, "
                    "AirDrop, etc.) and try again."
                )
            data = self._path.read_bytes()
            if not data:
                raise CameraUnavailableError(
                    f"FileCamera: {self._path} is empty."
                )
            return data

    def close(self) -> None:
        return


# ─── DaemonCamera ──────────────────────────────────────────────────


class DaemonCamera:
    """Read frames over HTTP from the long-running FruitMarketCamera.app daemon.

    The .app holds an AVCaptureSession open continuously and serves
    the most recent JPEG via a tiny local HTTP server (default
    ``http://127.0.0.1:8765/frame.jpg``). This is the cleanest
    backend for "truly live" video on macOS — the daemon has its
    own TCC entry (granted once via the .app bundle), so the
    Python process can run from ANY parent (including sandboxed
    ones like Claude Code) without camera-permission battles.

    Settings (env):
      * ``FM_CAMERA_DAEMON_URL`` — base URL of the daemon (default
        ``http://127.0.0.1:8765``).
      * ``FM_CAMERA_DAEMON_TIMEOUT`` — per-request timeout in
        seconds (default 2.0).

    Start the daemon first:

        open apps/fm-camera/FruitMarketCamera.app --args --daemon

    or directly:

        apps/fm-camera/FruitMarketCamera.app/Contents/MacOS/fm-camera \\
            --daemon --device "C920" --port 8765
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        explicit = base_url or os.environ.get(
            "FM_CAMERA_DAEMON_URL", "http://127.0.0.1:8765"
        )
        self._frame_url = explicit.rstrip("/") + "/frame.jpg"
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.environ.get("FM_CAMERA_DAEMON_TIMEOUT", "2.0"))
        )
        self._lock = threading.Lock()
        # Reuse the httpx Client across snapshots so we get
        # connection pooling for free — daemon endpoint is on
        # localhost but the TCP handshake still adds up at 5 FPS.
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise CameraUnavailableError(
                "DaemonCamera requires httpx (it's already a runtime "
                "dep of the FastAPI app)."
            ) from exc
        self._client = httpx.Client(timeout=self._timeout)

    def snapshot(self) -> bytes:
        with self._lock:
            try:
                response = self._client.get(self._frame_url)
            except Exception as exc:  # noqa: BLE001
                raise CameraUnavailableError(
                    f"DaemonCamera: cannot reach {self._frame_url}: {exc}. "
                    "Is FruitMarketCamera.app running in --daemon mode?"
                ) from exc
            if response.status_code != 200:
                # 503 with X-Camera-Status header means the daemon
                # is up but hasn't captured a first frame yet —
                # surface a clear message so the watcher's log
                # explains what's happening.
                status = response.headers.get("X-Camera-Status", "unknown")
                raise CameraUnavailableError(
                    f"DaemonCamera: HTTP {response.status_code} "
                    f"(X-Camera-Status: {status})"
                )
            data = response.content
            if not data:
                raise CameraUnavailableError(
                    "DaemonCamera: empty response body"
                )
            return data

    def close(self) -> None:
        import contextlib  # noqa: PLC0415

        with contextlib.suppress(Exception):
            self._client.close()


# ─── Factory ───────────────────────────────────────────────────────


def open_camera() -> CameraBackend:
    """Pick a camera backend based on ``FM_CAMERA_BACKEND``.

    Values:
      * ``auto``   — prefer daemon, then cv2, then broker.
      * ``daemon`` — use :class:`DaemonCamera` (continuous FruitMarketCamera.app).
      * ``cv2``    — use :class:`Cv2Camera` only; raise if it fails.
      * ``broker`` — one-shot subprocess to :class:`BrokerCamera`.
      * ``file``   — use :class:`FileCamera` (pinned JPEG, TCC-free).

    Default is ``auto`` so the kiosk automatically reuses a running
    ``FruitMarketCamera.app --daemon`` process. Explicit env settings
    still select exactly the requested backend.
    """

    backend = os.environ.get("FM_CAMERA_BACKEND", "auto").lower()
    if backend == "broker":
        return BrokerCamera()
    if backend == "file":
        return FileCamera()
    if backend == "daemon":
        return DaemonCamera()
    if backend == "auto":
        return _open_auto_camera()
    return Cv2Camera()


def _daemon_camera_for_auto() -> CameraBackend:
    timeout = float(os.environ.get("FM_CAMERA_AUTO_DAEMON_TIMEOUT", "0.25"))
    return DaemonCamera(timeout_seconds=timeout)


def _open_auto_camera() -> CameraBackend:
    candidates: tuple[tuple[str, Callable[[], CameraBackend]], ...] = (
        ("daemon", _daemon_camera_for_auto),
        ("cv2", Cv2Camera),
        ("broker", BrokerCamera),
    )
    errors: list[str] = []
    for name, factory in candidates:
        camera: CameraBackend | None = None
        try:
            camera = factory()
            # Probe one frame so the FastAPI app does not boot with a
            # backend that cannot feed the web cache.
            _ = camera.snapshot()
            return camera
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            if camera is not None:
                camera.close()
    raise CameraUnavailableError(
        "no camera backend available (" + "; ".join(errors) + ")"
    )
