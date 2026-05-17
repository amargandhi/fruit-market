"""Edge vision pipeline — camera + PaliGemma counter + asyncio watcher.

The single entry point for the HTTP layer is
:func:`fruit_market.vision.factory.build_default_vision`, which
wires up a camera, lazy-loads PaliGemma in the background, and
starts the watcher loop. See ``factory.py`` for the lifespan
integration pattern.
"""

from fruit_market.vision.camera import (
    BrokerCamera,
    Camera,
    CameraBackend,
    CameraUnavailableError,
    Cv2Camera,
    DaemonCamera,
    FileCamera,
    open_camera,
)
from fruit_market.vision.factory import VisionBundle, build_default_vision
from fruit_market.vision.model import (
    DEFAULT_MODEL,
    CountModelError,
    PaliGemmaCounter,
)
from fruit_market.vision.streamer import CameraStreamer, StreamerCamera
from fruit_market.vision.watcher import VisionWatcher, WatcherStatus

__all__ = [
    "DEFAULT_MODEL",
    "BrokerCamera",
    "Camera",
    "CameraBackend",
    "CameraStreamer",
    "CameraUnavailableError",
    "CountModelError",
    "Cv2Camera",
    "DaemonCamera",
    "FileCamera",
    "PaliGemmaCounter",
    "StreamerCamera",
    "VisionBundle",
    "VisionWatcher",
    "WatcherStatus",
    "build_default_vision",
    "open_camera",
]
