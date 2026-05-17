"""Fruit Market — Pico 2 W + Pimoroni RGB Keypad firmware.

Copy this file to the Pico as ``main.py`` after installing the
Pimoroni MicroPython build. The bridge process on the host sends
newline-delimited JSON over USB serial:

    {"event":"state","active_item":"banana","active_count":3,
     "supply_buy_pending":true,"health":{"camera":"ok","model":"warmup",
     "phone":"ok"}}

The firmware emits button events the same way:

    {"event":"button","button":1,"action":"confirm"}

Button layout (4x4, indices 0-15 left-to-right, top-to-bottom):

    ┌───────────┬───────────┬───────────┬───────────┐
    │ 0 CONFIRM │ 1 PACKED  │ 2 CANCEL  │ 3 COUNT   │
    ├───────────┼───────────┼───────────┼───────────┤
    │ 4 SUPPLY  │ 5 ─       │ 6 ─       │ 7 READY   │
    ├───────────┼───────────┼───────────┼───────────┤
    │ 8 stock   │ 9 stock   │10 stock   │11 stock   │
    ├───────────┼───────────┼───────────┼───────────┤
    │12 camera  │13 model   │14 phone   │15 error   │
    └───────────┴───────────┴───────────┴───────────┘

Action keys (0-4, 7) emit JSON button events. The other keys are
visual only — the firmware ignores presses on them so an accidental
finger on the demo table never sends a bogus command.

The supply-buy key (4) breathes blue-green when
``supply_buy_pending`` is set, drawing the operator's attention.
Press it to acknowledge; the host clears the flag.
"""

from __future__ import annotations

import json
import select
import sys
import time

try:
    import picokeypad as keypad  # Pimoroni MicroPython library
except ImportError:
    keypad = None


# ─── Layout + colors ────────────────────────────────────────────────


KEY_CONFIRM = 0
KEY_PACKED = 1
KEY_CANCEL = 2
KEY_COUNT_NOW = 3
KEY_SUPPLY_BUY = 4
KEY_READY = 7
STOCK_BAR = (8, 9, 10, 11)
HEALTH_CAMERA = 12
HEALTH_MODEL = 13
HEALTH_PHONE = 14
HEALTH_ERROR = 15

# Indices that emit button events; presses on other keys are ignored.
ACTION_KEY_TO_NAME = {
    KEY_CONFIRM: "confirm",
    KEY_PACKED: "packed",
    KEY_CANCEL: "cancel",
    KEY_COUNT_NOW: "count_now",
    KEY_SUPPLY_BUY: "supply_buy",
    KEY_READY: "ready",
}

# Indices that always emit even when their attention-flag is off.
# Useful for "I want to repeat the last status" type buttons.
ALWAYS_EMIT = {KEY_READY}

# Color palette. Brightness is kept modest so the keypad is readable
# in daylight without being a stage spotlight.
COLOR_OFF = (0, 0, 0)
COLOR_DIM_GREY = (12, 12, 12)
COLOR_GREEN = (0, 160, 30)
COLOR_AMBER = (200, 110, 0)
COLOR_RED = (180, 0, 0)
COLOR_BLUE = (0, 80, 210)
COLOR_TEAL = (0, 170, 160)
COLOR_WHITE = (160, 160, 160)
COLOR_VIOLET = (110, 0, 190)

LABEL_COLORS = {
    "confirm": COLOR_GREEN,
    "packed": COLOR_AMBER,
    "cancel": COLOR_RED,
    "count_now": COLOR_VIOLET,
    "supply_buy": COLOR_TEAL,
    "ready": COLOR_GREEN,
}

HEALTH_COLORS = {
    "ok": COLOR_GREEN,
    "warmup": COLOR_AMBER,
    "mock": COLOR_BLUE,
    "warn": COLOR_AMBER,
    "fail": COLOR_RED,
    "error": COLOR_RED,
    "down": COLOR_RED,
    "unknown": COLOR_DIM_GREY,
}


# ─── State ──────────────────────────────────────────────────────────


# Last full state payload from the host. Defaults are conservative
# (everything off) so a Pico that boots before the bridge connects
# shows nothing rather than stale data.
state = {
    "event": "state",
    "active_item": "",
    "active_count": 0,
    "active_low": False,
    "attention": {
        "confirm": False,
        "packed": False,
        "cancel": False,
        "supply_buy": False,
    },
    "health": {
        "camera": "unknown",
        "model": "unknown",
        "phone": "unknown",
    },
    "error_message": "",
}


# Brief white flash on the most recently pressed key — gives haptic
# feedback even though the LED is on top of the silicone.
flash_index = -1
flash_until_ms = 0
FLASH_DURATION_MS = 140


# ─── Helpers ────────────────────────────────────────────────────────


def now_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def write_line(obj):
    sys.stdout.write(json.dumps(obj) + "\n")


def read_host_line():
    """Non-blocking read of one JSON line from the host."""

    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return None
    raw = sys.stdin.readline()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {"event": "error", "message": "bad json from host"}


def scale(color, percent):
    """Return ``color`` darkened to ``percent`` of full brightness."""

    return tuple(max(0, min(255, int(c * percent / 100))) for c in color)


def breathe(base, dim, ts, period_ms):
    """Sinusoidal-ish breathe between ``dim`` and ``base``.

    No math.sin in MicroPython firmware constraints; triangle wave
    is visually close enough and cheaper to compute.
    """

    phase = (ts % period_ms) / period_ms
    if phase < 0.5:
        t = phase * 2.0
    else:
        t = (1.0 - phase) * 2.0
    return tuple(int(dim[i] + (base[i] - dim[i]) * t) for i in range(3))


def blink(a, b, ts, period_ms):
    return a if (ts // period_ms) % 2 == 0 else b


def set_pad(index, color):
    if keypad is not None:
        keypad.illuminate(index, color[0], color[1], color[2])


# ─── Render ─────────────────────────────────────────────────────────


def paint_actions(ts):
    """Draw the top two rows of action buttons."""

    attention = state.get("attention", {})

    # Each action key gets a low ambient glow in its color so the
    # operator can still see the layout in the dark. When the
    # corresponding action wants attention, the key breathes.
    for index, action in ACTION_KEY_TO_NAME.items():
        base = LABEL_COLORS.get(action, COLOR_DIM_GREY)
        if attention.get(action):
            set_pad(index, breathe(base, scale(base, 12), ts, 700))
        else:
            set_pad(index, scale(base, 18))

    # The supply-buy key uses a faster breathe + teal so it's
    # impossible to miss when a restock decision is pending.
    if attention.get("supply_buy"):
        set_pad(KEY_SUPPLY_BUY, breathe(COLOR_TEAL, scale(COLOR_TEAL, 8), ts, 380))


def paint_stock(ts):
    """Render the active item's count as a 4-bar gauge on row 3."""

    count = int(state.get("active_count", 0) or 0)
    low = bool(state.get("active_low"))
    color = COLOR_AMBER if low else COLOR_GREEN

    for i, index in enumerate(STOCK_BAR):
        if i < count:
            set_pad(index, scale(color, 60))
        else:
            set_pad(index, COLOR_DIM_GREY)

    # If count == 0, blink the leftmost stock cell in red so an
    # empty shelf is obvious from across the room.
    if count == 0:
        set_pad(STOCK_BAR[0], blink(COLOR_RED, COLOR_OFF, ts, 420))


def paint_health(ts):
    """Bottom row: camera / model / phone / error."""

    health = state.get("health", {})

    for index, key in (
        (HEALTH_CAMERA, "camera"),
        (HEALTH_MODEL, "model"),
        (HEALTH_PHONE, "phone"),
    ):
        status = (health.get(key) or "unknown").lower()
        color = HEALTH_COLORS.get(status, COLOR_DIM_GREY)
        if status == "warmup":
            set_pad(index, breathe(color, scale(color, 10), ts, 900))
        elif status in ("warn", "fail", "error", "down"):
            set_pad(index, blink(color, scale(color, 10), ts, 320))
        else:
            set_pad(index, scale(color, 55))

    if state.get("error_message"):
        set_pad(HEALTH_ERROR, blink(COLOR_RED, COLOR_WHITE, ts, 220))
    else:
        set_pad(HEALTH_ERROR, COLOR_DIM_GREY)


def paint():
    if keypad is None:
        return
    ts = now_ms()
    # Reset the grid to a neutral floor each frame so previous
    # animation state doesn't bleed through.
    if hasattr(keypad, "clear"):
        keypad.clear()
    paint_actions(ts)
    paint_stock(ts)
    paint_health(ts)
    if flash_index >= 0 and ts < flash_until_ms:
        set_pad(flash_index, COLOR_WHITE)
    keypad.update()


# ─── Input ──────────────────────────────────────────────────────────


def emit_hello():
    write_line({
        "event": "hello",
        "device": "fm-pico-keypad",
        "version": 1,
        "actions": sorted(ACTION_KEY_TO_NAME.values()),
    })


def poll_buttons(previous):
    """Detect new presses, emit JSON events, return new pressed mask."""

    global flash_index, flash_until_ms

    if keypad is None:
        return previous

    current = keypad.get_button_states()
    pressed = current & ~previous

    if pressed:
        for index in range(keypad.get_num_pads()):
            if not (pressed & (1 << index)):
                continue
            action = ACTION_KEY_TO_NAME.get(index)
            if action is None:
                continue  # press on a visual-only key is ignored
            # Only emit when there's attention on this action, or
            # when it's an always-emit key (e.g. ready).
            attention = state.get("attention", {})
            if not attention.get(action) and index not in ALWAYS_EMIT:
                continue
            flash_index = index
            flash_until_ms = now_ms() + FLASH_DURATION_MS
            write_line({
                "event": "button",
                "button": index + 1,  # 1-based for human readability
                "action": action,
            })

    return current


# ─── Main ───────────────────────────────────────────────────────────


def apply_host_payload(payload):
    """Merge an incoming host payload into ``state``.

    The host sends a full state snapshot every push, so we replace
    rather than patch. ``error`` events are surfaced via
    ``error_message`` so the bottom-right key blinks.
    """

    global state

    if not isinstance(payload, dict):
        return
    event = payload.get("event")
    if event == "state":
        state = {
            "event": "state",
            "active_item": payload.get("active_item", ""),
            "active_count": payload.get("active_count", 0),
            "active_low": payload.get("active_low", False),
            "attention": payload.get("attention", state.get("attention", {})),
            "health": payload.get("health", state.get("health", {})),
            "error_message": payload.get("error_message", ""),
        }
    elif event == "error":
        state["error_message"] = payload.get("message", "error")


def setup():
    if keypad is not None:
        keypad.init()
        keypad.set_brightness(0.9)


def main():
    setup()
    previous = 0
    emit_hello()
    while True:
        payload = read_host_line()
        if payload is not None:
            apply_host_payload(payload)
        previous = poll_buttons(previous)
        paint()
        time.sleep(0.03)


if __name__ == "__main__" or keypad is not None:
    main()
