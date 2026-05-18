"""Camera backend selection tests."""

from __future__ import annotations

from typing import Any

import fruit_market.vision.camera as camera_module
from fruit_market.vision.camera import CameraUnavailableError, open_camera


class _WorkingCamera:
    snapshots = 0
    closed = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def snapshot(self) -> bytes:
        type(self).snapshots += 1
        return b"\xff\xd8\xff" + self.__class__.__name__.encode()

    def close(self) -> None:
        type(self).closed += 1


class _FailingCamera(_WorkingCamera):
    def snapshot(self) -> bytes:
        raise CameraUnavailableError(f"{self.__class__.__name__} unavailable")


def test_open_camera_auto_prefers_running_daemon(monkeypatch) -> None:
    monkeypatch.delenv("FM_CAMERA_BACKEND", raising=False)
    monkeypatch.setattr(camera_module, "DaemonCamera", _WorkingCamera)
    monkeypatch.setattr(camera_module, "Cv2Camera", _FailingCamera)
    monkeypatch.setattr(camera_module, "BrokerCamera", _FailingCamera)

    camera = open_camera()

    assert isinstance(camera, _WorkingCamera)
    assert _WorkingCamera.snapshots == 1


def test_open_camera_auto_falls_back_when_daemon_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("FM_CAMERA_BACKEND", raising=False)

    class FailingDaemon(_FailingCamera):
        closed = 0

    class WorkingCv2(_WorkingCamera):
        snapshots = 0

    monkeypatch.setattr(camera_module, "DaemonCamera", FailingDaemon)
    monkeypatch.setattr(camera_module, "Cv2Camera", WorkingCv2)
    monkeypatch.setattr(camera_module, "BrokerCamera", _FailingCamera)

    camera = open_camera()

    assert isinstance(camera, WorkingCv2)
    assert WorkingCv2.snapshots == 1
    assert FailingDaemon.closed == 1


def test_open_camera_explicit_backend_skips_auto_probe(monkeypatch) -> None:
    monkeypatch.setenv("FM_CAMERA_BACKEND", "cv2")

    class ExplicitCv2(_WorkingCamera):
        snapshots = 0

    monkeypatch.setattr(camera_module, "Cv2Camera", ExplicitCv2)

    camera = open_camera()

    assert isinstance(camera, ExplicitCv2)
    assert ExplicitCv2.snapshots == 0
