"""Live smoke test: snapshot (or load) an image and count apples + bananas.

Run via:

    uv run --extra vision python scripts/smoke_vision.py

If ``/tmp/fruit-market-smoke.jpg`` exists, the script uses that file
directly instead of opening the camera. Set ``FM_SMOKE_IMAGE`` to
read from a different path. This is the workaround for macOS TCC
sandboxes: capture the frame in any app you control (Photo Booth,
imagesnap from Terminal.app, AirDrop from your phone, etc.), save
to the path, and re-run.

If neither file is present, the script tries to open the camera
directly — works when the parent process has camera permission.

First model invocation downloads PaliGemma weights (~6 GB) into
the HF cache. Subsequent runs are warm.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from fruit_market.vision.camera import CameraUnavailableError, open_camera
from fruit_market.vision.model import CountModelError, PaliGemmaCounter

SNAPSHOT_PATH = Path(os.environ.get("FM_SMOKE_IMAGE", "/tmp/fruit-market-smoke.jpg"))
NOUNS = ("apple", "banana")


def main() -> int:
    if SNAPSHOT_PATH.exists():
        frame = SNAPSHOT_PATH.read_bytes()
        print(f"→ using existing image at {SNAPSHOT_PATH} ({len(frame)} bytes)")
    else:
        backend = os.environ.get("FM_CAMERA_BACKEND", "cv2")
        print(f"→ no image at {SNAPSHOT_PATH}, capturing via backend={backend}...")
        camera = open_camera()
        try:
            frame = camera.snapshot()
        except CameraUnavailableError as exc:
            print(f"FAIL: camera unavailable: {exc}", file=sys.stderr)
            print(
                "Hints:",
                f"  • Drop a JPEG of the scene at {SNAPSHOT_PATH} and re-run.",
                "  • Set FM_CAMERA_BACKEND=broker to use the .app helper",
                "    (apps/fm-camera/FruitMarketCamera.app must be built and",
                "    granted camera permission in System Settings).",
                sep="\n",
                file=sys.stderr,
            )
            return 2
        SNAPSHOT_PATH.write_bytes(frame)
        camera.close()
        print(f"→ snapshot saved to {SNAPSHOT_PATH} ({len(frame)} bytes)")
    print()

    print("→ loading PaliGemma 2 (first run downloads ~6 GB)...")
    model = PaliGemmaCounter()
    t0 = time.perf_counter()
    model.warmup()
    print(f"  warmup took {time.perf_counter() - t0:.1f}s")
    print()

    results: dict[str, int | str] = {}
    for noun in NOUNS:
        t0 = time.perf_counter()
        try:
            count = model.count(frame, noun)
        except CountModelError as exc:
            results[noun] = f"PARSE ERROR: {exc}"
            print(f"  {noun}: PARSE ERROR ({exc})")
            continue
        dt = time.perf_counter() - t0
        results[noun] = count
        raw = model.last_raw_response
        print(f"  {noun}: {count}  ({dt:.2f}s, raw={raw!r})")

    print()
    print("─" * 60)
    expected = {"apple": 3, "banana": 3}
    all_ok = True
    for noun, exp in expected.items():
        actual = results.get(noun)
        ok = actual == exp
        all_ok = all_ok and ok
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] expected {exp} {noun}{'s' if exp != 1 else ''}, got {actual}")

    if not all_ok:
        print()
        print("If the count is off by 1-2, that's usually fine — the model")
        print("is sensitive to lighting and occlusion. The watcher's motion")
        print("gate plus the operator override on the kiosk handle drift.")
        print("If the count is wildly off, check the snapshot — the camera")
        print("might be pointed at a different scene than you think.")

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
