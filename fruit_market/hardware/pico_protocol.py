"""Wire protocol for the Pico keypad bridge.

Two directions:

* **Bridge → firmware** — push state via :func:`serialize_state`.
  The firmware re-renders LEDs on every push; it never accumulates
  state, so dropped frames are recovered on the next push.
* **Firmware → bridge** — newline JSON parsed by
  :func:`parse_event_line`. Returns ``None`` for unknown event
  types so the bridge can log + skip rather than crash.

Keeping the protocol in its own pure-Python module (no pyserial,
no httpx) means tests can exercise the schema without touching
hardware or installing optional deps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

# Canonical action names emitted by the firmware. Mirrored as
# ``ACTION_KEY_TO_NAME.values()`` in apps/pico/main.py — both sides
# must agree. ONLY the top row of the keypad emits buttons, so this
# list is intentionally short: four actions, one per top-row key.
#
# The HTTP layer (``/api/pico/action``) still accepts additional
# action names (``count_now``, ``confirm``) for direct curl-based
# diagnostics, but the firmware will never emit them — they're
# no longer wired to physical keys.
ACTION_NAMES: tuple[str, ...] = (
    "ready",        # row-0 key 0: open the store / mark shelf confirmed
    "packed",       # row-0 key 1: mark most-recent paid order packed
    "cancel",       # row-0 key 2: cancel reservation or pending restock
    "supply_buy",   # row-0 key 3: approve PaySponge supplier payment
)

# Allowed values for each health field. ``unknown`` is the default
# until the bridge has actually heard from the corresponding service.
HEALTH_VALUES: tuple[str, ...] = (
    "ok",       # green
    "warmup",   # breathing amber
    "mock",     # solid blue — sponsor unconfigured but app works
    "warn",     # blinking amber
    "fail",     # blinking red
    "error",    # blinking red
    "down",     # blinking red
    "unknown",  # dim grey
)


# ─── Bridge → firmware ──────────────────────────────────────────────


@dataclass(frozen=True)
class FlashInstruction:
    """One transient LED pulse for the firmware to overlay.

    Use case: punctuate a count change. The watcher noticed an
    apple was removed → bridge schedules a flash on the apple
    row-2 cell (key 8) with amber color, 700 ms duration. The
    firmware paints it on top of every other layer until the
    duration expires, then drops it.
    """

    index: int                       # 0..15
    color: tuple[int, int, int]      # RGB 0..255
    duration_ms: int = 700

    def to_wire(self) -> dict[str, object]:
        return {
            "index": int(self.index),
            "color": [int(self.color[0]), int(self.color[1]), int(self.color[2])],
            "duration_ms": int(self.duration_ms),
        }


@dataclass(frozen=True)
class PicoStatePayload:
    """One full state snapshot pushed to the firmware.

    The firmware replaces its in-memory state every push. The
    bridge SHOULD push on every state change AND on a heartbeat
    (~1 Hz) so a freshly-booted Pico catches up without needing
    a special handshake.

    Fields map directly onto the firmware's per-key paint
    pipeline (see ``apps/pico/main.py`` for the per-layer paint
    functions):
      * ``active_item`` → glows the matching row-2 fruit cell.
      * ``fruit_status`` → per-fruit active/low/out status for the
        lower-grid dashboard cells.
      * ``order_status`` → row-2 cells 10 (reservation) + 11 (paid).
      * ``call_active`` / ``payment_pending`` → row-1 indicators.
      * ``restock_status`` → row-0 SUPPLY_BUY animation phase.
      * ``attention`` → which row-0 keys breathe to demand
        operator focus.
      * ``health`` → row-3 subsystem indicators.
      * ``error_message`` → row-3 ERROR strobe.
      * ``flashes`` → transient count-change pulses (green=added,
        amber=removed, red=out-of-stock). Each schedules with a
        deadline; the firmware drops expired flashes on its own.
    """

    active_item: str = ""
    active_count: int = 0
    active_low: bool = False
    fruit_status: dict[str, dict[str, object]] = field(default_factory=dict)
    order_status: str = ""           # "" | reserved | paid | packed | cancelled
    call_active: bool = False
    payment_pending: bool = False
    restock_status: str = ""
    attention: dict[str, bool] = field(default_factory=dict)
    health: dict[str, str] = field(default_factory=dict)
    error_message: str = ""
    flashes: tuple[FlashInstruction, ...] = ()

    def to_wire(self) -> dict[str, object]:
        """Plain-dict shape that ``serialize_state`` will encode."""

        attention = {
            action: bool(self.attention.get(action, False))
            for action in ACTION_NAMES
        }
        health = {
            field_name: _coerce_health(self.health.get(field_name, "unknown"))
            for field_name in ("camera", "model", "phone")
        }
        fruit_status = {
            fruit: _coerce_fruit_status(self.fruit_status.get(fruit, {}))
            for fruit in ("apple", "banana")
        }
        return {
            "event": "state",
            "active_item": self.active_item or "",
            "active_count": max(0, int(self.active_count)),
            "active_low": bool(self.active_low),
            "fruit_status": fruit_status,
            "order_status": str(self.order_status or ""),
            "call_active": bool(self.call_active),
            "payment_pending": bool(self.payment_pending),
            "restock_status": str(self.restock_status or ""),
            "attention": attention,
            "health": health,
            "error_message": str(self.error_message or ""),
            "flashes": [f.to_wire() for f in self.flashes],
        }


def serialize_state(payload: PicoStatePayload) -> bytes:
    """Encode ``payload`` as one newline-terminated JSON line.

    Use the returned bytes verbatim with ``serial.write`` — the
    trailing newline is what the firmware splits on.
    """

    return (json.dumps(payload.to_wire(), separators=(",", ":")) + "\n").encode("utf-8")


def _coerce_health(value: str) -> str:
    v = (value or "unknown").strip().lower()
    return v if v in HEALTH_VALUES else "unknown"


def _coerce_fruit_status(value: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        value = {}
    count_raw = value.get("count", 0)
    try:
        # int() will refuse ``object``; narrow to (str, int, float, bool)
        # before coercion, fall back to 0 on anything weirder.
        if isinstance(count_raw, str | int | float | bool):
            count = max(0, int(count_raw or 0))
        else:
            count = 0
    except (TypeError, ValueError):
        count = 0
    return {
        "count": count,
        "active": bool(value.get("active", False)),
        "low": bool(value.get("low", False)),
    }


# ─── Firmware → bridge ──────────────────────────────────────────────


EventName = Literal["hello", "button", "error"]


@dataclass(frozen=True)
class PicoHello:
    """Emitted on firmware boot. The bridge uses this as a
    handshake — once it lands, the bridge pushes the current state
    so the freshly-booted Pico catches up."""

    event: Literal["hello"] = "hello"
    device: str = ""
    version: int = 0
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PicoButtonEvent:
    """A debounced, attention-gated press of one of the action keys."""

    event: Literal["button"] = "button"
    button: int = 0
    action: str = ""

    def is_known_action(self) -> bool:
        return self.action in ACTION_NAMES


def parse_event_line(raw: bytes | str) -> PicoButtonEvent | PicoHello | None:
    """Parse one JSON line from the firmware.

    Returns ``None`` for any line that:

    * isn't valid JSON,
    * isn't an object,
    * has an unrecognised ``event`` field, or
    * (for buttons) has an unknown ``action``.

    The bridge logs the raw line and moves on — we never let the
    firmware crash the host process.
    """

    if isinstance(raw, bytes | bytearray):
        try:
            text = bytes(raw).decode("utf-8", errors="replace")
        except Exception:
            return None
    else:
        text = raw
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    event = data.get("event")
    if event == "hello":
        actions_raw = data.get("actions") or []
        actions = tuple(str(a) for a in actions_raw if isinstance(a, str))
        return PicoHello(
            device=str(data.get("device", "")),
            version=int(data.get("version", 0) or 0),
            actions=actions,
        )
    if event == "button":
        action = str(data.get("action", ""))
        if action not in ACTION_NAMES:
            return None
        try:
            button = int(data.get("button", 0) or 0)
        except (TypeError, ValueError):
            button = 0
        return PicoButtonEvent(button=button, action=action)

    return None
