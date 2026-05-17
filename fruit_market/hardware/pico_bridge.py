"""Sidecar bridge: USB serial ↔ fruit-market HTTP API.

Runs as a separate process so a Pico crash, USB unplug, or stuck
serial open can't take down the customer-facing brain. The bridge:

1. Auto-detects the Pico serial port (or honors ``--serial``).
2. Opens it in a watchdog thread with a 1 s timeout so a stuck
   firmware can't hang the bridge forever.
3. Reads JSON event lines from the firmware. ``button`` events get
   translated into HTTP POSTs against the fruit-market API.
4. Polls ``GET /api/state`` every ``state_interval_seconds``,
   maps the API response into :class:`PicoStatePayload`, and pushes
   the payload to the firmware so LEDs stay current.

Run via:

    uv run python -m fruit_market.hardware.pico_bridge \\
        --serial /dev/cu.usbmodem* \\
        --api http://localhost:8000

Or with everything default (auto-detect port, localhost:8000):

    uv run python -m fruit_market.hardware.pico_bridge

The bridge is intentionally chatty on stdout (one line per push,
one per button event, one per error) so a tail in another terminal
is enough to see what's happening during a demo.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from fruit_market.hardware.pico_protocol import (
    PicoButtonEvent,
    PicoHello,
    PicoStatePayload,
    parse_event_line,
    serialize_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


logger = logging.getLogger("fm.pico_bridge")


# ─── Serial detection + open ────────────────────────────────────────


# Glob patterns we expect the Pico to appear under on macOS.
# Linux equivalents (`/dev/ttyACM*`) are also matched.
_PORT_GLOBS = (
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
)
_BAUD = 115200
_OPEN_TIMEOUT_S = 1.0
_BAD_PORT_TTL_S = 30.0


def candidate_ports() -> list[str]:
    """All currently-visible candidate serial ports, sorted."""

    seen: set[str] = set()
    out: list[str] = []
    for pattern in _PORT_GLOBS:
        for path in sorted(glob.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def _open_serial(port: str, timeout_s: float = _OPEN_TIMEOUT_S) -> Any:
    """Open ``port`` for read/write in a watchdog thread.

    Returns the open ``serial.Serial`` instance (typed ``Any``
    because pyserial doesn't ship type stubs we can rely on across
    versions) or raises ``OSError``. The watchdog ensures a stuck
    ``Serial(...)`` constructor (which happens with some Pico
    firmwares in odd states) doesn't hang us forever — we bail
    after ``timeout_s``.
    """

    import serial

    box: dict[str, Any] = {}

    def _do_open() -> None:
        try:
            ser = serial.Serial(
                port,
                _BAUD,
                timeout=0.05,
                write_timeout=0.25,
            )
            box["ser"] = ser
        except Exception as exc:  # noqa: BLE001
            box["error"] = str(exc)

    opener = threading.Thread(target=_do_open, name="fm-pico-open", daemon=True)
    opener.start()
    opener.join(timeout_s)
    if opener.is_alive():
        # Leave the daemon thread to die with the process; it'll
        # eventually return when the kernel unsticks the port.
        raise OSError(f"open(): timed out after {timeout_s:.1f}s on {port}")
    if "ser" not in box:
        raise OSError(box.get("error", "open(): unknown failure"))

    ser = box["ser"]
    # Drop DTR/RTS *after* open so MicroPython on the Pico doesn't
    # interpret the open as a soft-reboot signal.
    with contextlib.suppress(Exception):
        ser.dtr = False
        ser.rts = False
    return ser


# ─── HTTP client wrapper ────────────────────────────────────────────


# A real browser User-Agent — the AgentPhone webhook callbacks travel
# through the same backend and the rest of the codebase already pins
# this header for consistency.
HTTP_USER_AGENT = (
    "fm-pico-bridge/0.1 "
    "(Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36)"
)


@dataclass(frozen=True)
class ApiClient:
    base_url: str
    timeout_s: float = 5.0

    def fetch_state(self) -> dict[str, object]:
        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.get(
                f"{self.base_url}/api/state",
                headers={"User-Agent": HTTP_USER_AGENT, "Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}

    def post_action(self, action: str) -> dict[str, object]:
        """POST the button action to the API.

        Sends to ``/api/pico/action`` — a thin endpoint the HTTP
        layer is expected to expose that dispatches to the right
        service. Body is just ``{"action": "..."}``; the backend
        resolves the relevant order_id / proposal_id from state.
        """

        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.post(
                f"{self.base_url}/api/pico/action",
                headers={"User-Agent": HTTP_USER_AGENT, "Content-Type": "application/json"},
                json={"action": action},
            )
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}


# ─── State mapping ──────────────────────────────────────────────────


def api_state_to_payload(state: dict[str, object]) -> PicoStatePayload:
    """Translate the API's snapshot into the firmware's wire shape.

    The API surface is owned by another track and may evolve; this
    function is intentionally forgiving — missing fields collapse
    to sensible defaults rather than raising.
    """

    catalog = state.get("catalog") or []
    active_item_id = state.get("active_item_id") or ""
    active_name = ""
    active_count = 0
    active_low = False
    if isinstance(catalog, list):
        for item in catalog:
            if not isinstance(item, dict):
                continue
            if item.get("item_id") == active_item_id:
                active_name = str(item.get("name", ""))
                active_count = int(item.get("physical_count", 0) or 0)
                active_low = bool(item.get("is_low", False))
                break

    pending = state.get("pending") or {}
    pending_dict = pending if isinstance(pending, dict) else {}
    restock = state.get("restock") or {}
    restock_dict = restock if isinstance(restock, dict) else {}
    supply_buy_pending = bool(pending_dict.get("supply_buy"))
    attention = {
        "confirm": bool(pending_dict.get("teach_proposal")),
        "packed": bool(pending_dict.get("paid_order")),
        "cancel": bool(pending_dict.get("reservation")) or supply_buy_pending,
        "supply_buy": supply_buy_pending,
    }

    health = state.get("health") or {}
    health_dict = health if isinstance(health, dict) else {}

    error_message = ""
    err = state.get("error") or ""
    if isinstance(err, str):
        error_message = err
    if not error_message and restock_dict.get("status") == "failed":
        error_message = str(restock_dict.get("failure_reason") or "restock failed")

    return PicoStatePayload(
        active_item=active_name,
        active_count=active_count,
        active_low=active_low,
        restock_status=str(restock_dict.get("status") or ""),
        attention=attention,
        health={
            "camera": str(health_dict.get("camera", "unknown")),
            "model": str(health_dict.get("model", "unknown")),
            "phone": str(health_dict.get("phone", "unknown")),
        },
        error_message=error_message,
    )


# ─── Bridge loop ────────────────────────────────────────────────────


class PicoBridge:
    """Owns the serial connection + the API client.

    Two background threads: a reader pulling event lines from the
    serial port and a state pusher polling the API. Stop with
    :meth:`stop`; the threads exit at the next loop iteration.
    """

    def __init__(
        self,
        *,
        api: ApiClient,
        port: str | None = None,
        state_interval_seconds: float = 1.0,
    ) -> None:
        self._api = api
        self._configured_port = port
        self._state_interval = state_interval_seconds
        self._stop = threading.Event()
        self._serial: Any = None
        self._serial_port: str | None = None
        self._bad_port_until: dict[str, float] = {}
        self._last_state_pushed: bytes | None = None
        self._reader_thread: threading.Thread | None = None
        self._pusher_thread: threading.Thread | None = None

    # ─── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        if self._reader_thread is not None:
            return
        self._stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, name="fm-pico-reader", daemon=True
        )
        self._pusher_thread = threading.Thread(
            target=self._push_loop, name="fm-pico-pusher", daemon=True
        )
        self._reader_thread.start()
        self._pusher_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        if self._pusher_thread is not None:
            self._pusher_thread.join(timeout=2.0)
            self._pusher_thread = None
        self._close_serial()

    # ─── port lifecycle ───────────────────────────────────────────

    def _detect_port(self) -> str | None:
        if self._configured_port:
            return self._configured_port
        for candidate in candidate_ports():
            if self._bad_port_until.get(candidate, 0.0) > time.monotonic():
                continue
            return candidate
        return None

    def _ensure_serial(self) -> Any:
        if self._serial is not None and self._serial_port is not None:
            return self._serial
        port = self._detect_port()
        if port is None:
            return None
        try:
            ser = _open_serial(port)
        except OSError as exc:
            self._bad_port_until[port] = time.monotonic() + _BAD_PORT_TTL_S
            logger.warning("pico open failed on %s: %s", port, exc)
            return None
        self._serial = ser
        self._serial_port = port
        self._last_state_pushed = None  # force a re-push to the freshly-open port
        logger.info("pico connected on %s", port)
        return ser

    def _close_serial(self) -> None:
        ser = self._serial
        self._serial = None
        self._serial_port = None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()

    # ─── reader loop ──────────────────────────────────────────────

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            ser = self._ensure_serial()
            if ser is None:
                self._stop.wait(2.0)
                continue
            try:
                line = ser.readline()
            except Exception as exc:  # noqa: BLE001
                logger.warning("pico read failed: %s", exc)
                self._close_serial()
                self._stop.wait(1.0)
                continue
            if not line:
                # Empty read just means no data this tick; loop on.
                continue
            self._handle_event_line(line)

    def _handle_event_line(self, raw: bytes) -> None:
        event = parse_event_line(raw)
        if event is None:
            logger.debug("ignoring unparseable line: %r", raw[:80])
            return
        if isinstance(event, PicoHello):
            logger.info(
                "pico hello: device=%s version=%d actions=%s",
                event.device, event.version, ",".join(event.actions),
            )
            # Force an immediate state push so the firmware lights
            # up to current values without waiting for the poll cycle.
            self._last_state_pushed = None
            return
        if isinstance(event, PicoButtonEvent):
            logger.info("pico button: %s (key %d)", event.action, event.button)
            try:
                self._api.post_action(event.action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("api post_action(%s) failed: %s", event.action, exc)
            return

    # ─── pusher loop ──────────────────────────────────────────────

    def _push_loop(self) -> None:
        while not self._stop.is_set():
            try:
                state = self._api.fetch_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug("api fetch_state failed: %s", exc)
                self._stop.wait(self._state_interval)
                continue
            payload = api_state_to_payload(state)
            wire = serialize_state(payload)
            if wire != self._last_state_pushed:
                self._push_to_pico(wire)
                self._last_state_pushed = wire
            self._stop.wait(self._state_interval)

    def _push_to_pico(self, wire: bytes) -> None:
        ser = self._ensure_serial()
        if ser is None:
            return
        try:
            ser.write(wire)
            ser.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pico write failed: %s", exc)
            self._close_serial()


# ─── CLI entry ──────────────────────────────────────────────────────


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fm-pico-bridge",
        description="USB serial ↔ fruit-market HTTP API bridge.",
    )
    parser.add_argument(
        "--serial",
        help="Path to the Pico serial device (default: auto-detect cu.usbmodem*/ttyACM*).",
    )
    parser.add_argument(
        "--api",
        default=os.environ.get("FM_API_URL", "http://localhost:8000"),
        help="Base URL of the fruit-market API (default: $FM_API_URL or http://localhost:8000).",
    )
    parser.add_argument(
        "--state-interval-seconds",
        type=float,
        default=1.0,
        help="How often to poll /api/state and push to the Pico (default: 1.0).",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("FM_PICO_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    if os.environ.get("FM_PICO_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        print("FM_PICO_DISABLED is set; not starting bridge.", file=sys.stderr)
        return 0
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    bridge = PicoBridge(
        api=ApiClient(base_url=args.api.rstrip("/")),
        port=args.serial,
        state_interval_seconds=args.state_interval_seconds,
    )
    bridge.start()
    logger.info(
        "fm-pico-bridge running. api=%s port=%s interval=%.1fs",
        args.api, args.serial or "<auto>", args.state_interval_seconds,
    )
    try:
        while True:
            time.sleep(60.0)
    except KeyboardInterrupt:
        logger.info("stopping bridge…")
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
