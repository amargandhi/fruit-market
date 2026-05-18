"""Fruit Market — Pico 2 W + Pimoroni RGB Keypad firmware.

Copy this file to the Pico as ``main.py`` after installing the
Pimoroni MicroPython build. The bridge process on the host sends
newline-delimited JSON over USB serial; the firmware re-paints
the 4x4 keypad on every frame from the most recent payload.

    {"event":"state","active_item":"banana","active_count":3,
     "order_status":"paid","attention":{"packed":true,...},
     "health":{"camera":"ok","model":"ok","phone":"ok"}}

The firmware emits button events the same way:

    {"event":"button","button":1,"action":"ready"}

Keypad layout (4x4, indices 0-15 left-to-right, top-to-bottom):

    +-------------+-------------+-------------+-----------------+
    | 0 READY     | 1 PACKED    | 2 CANCEL    | 3 SUPPLY_BUY    |
    |   (green)   |   (amber)   |   (red)     |   (cyan)        |
    +-------------+-------------+-------------+-----------------+
    | 4 call act. | 5 payment   | 6 COUNT_NOW | 7 CONFIRM       |
    |   (blue)    |   (gold)    |   (violet)  |   (light green) |
    +-------------+-------------+-------------+-----------------+
    | 8 apples    | 9 bananas   |10 reserved  |11 paid/packed   |
    |   (red)     |   (yellow)  |   (purple)  |   (green)       |
    +-------------+-------------+-------------+-----------------+
    |12 camera    |13 model     |14 phone     |15 error         |
    |   (blue)    |   (violet)  |   (magenta) |   (red)         |
    +-------------+-------------+-------------+-----------------+

Row 0 -- primary demo actions. These are the four keys the
operator touches during a live run:
    READY      -> open the store / mark shelf confirmed
    PACKED     -> confirm pack of next paid order
    CANCEL     -> cancel reservation or pending restock
    SUPPLY_BUY -> approve PaySponge supplier payment

Row 1 -- secondary actions + status indicators:
    keys 4 + 5 are visual-only (call active, payment pending)
    COUNT_NOW (6)  -> force a vision recount past the motion gate
    CONFIRM   (7)  -> confirm a pending teach proposal

Rows 2 + 3 -- purely visual. Never emit events on press.

Action keys (0, 1, 2, 3, 6, 7) always emit on press regardless of
whether the backend has anything pending -- the backend decides
what to do (returns ``status="no_pending"`` if there is nothing to
act on). This keeps the keypad feeling alive: every press gets a
white flash + a serial event, even on first boot.
"""

# NOTE: this file runs on MicroPython on the Pico, NOT on CPython.
# That means: no ``from __future__ import annotations``, no PEP 604
# union syntax (``str | None`` is fine on MicroPython >= 1.21 but
# we keep things conservative), and the only stdlib modules we get
# are the small set MicroPython ships natively.

import json
import select
import sys
import time

try:
    # The Pimoroni MicroPython build for RP2350 (Pico 2 W) ships
    # picokeypad as a class-based module; older RP2040 builds had
    # module-level functions. We use the class API here -- works on
    # both. If neither is available (running on a stock MicroPython
    # or a desktop interpreter for syntax checking), fall back to
    # None and the firmware becomes a no-op shell.
    from picokeypad import PicoKeypad  # type: ignore[import-not-found]
    keypad = PicoKeypad()
except ImportError:
    keypad = None


# --- Layout ---------------------------------------------------------


# Row 0 -- primary demo actions
KEY_READY = 0
KEY_PACKED = 1
KEY_CANCEL = 2
KEY_SUPPLY_BUY = 3

# Row 1 -- secondary actions + status
KEY_CALL_ACTIVE = 4
KEY_PAYMENT = 5
KEY_COUNT_NOW = 6
KEY_CONFIRM = 7

# Row 2 -- item + order status
KEY_APPLE = 8
KEY_BANANA = 9
KEY_RESERVATION = 10
KEY_PAID = 11

# Row 3 -- system health
KEY_CAMERA = 12
KEY_MODEL = 13
KEY_PHONE = 14
KEY_ERROR = 15

# Indices that emit button events; presses on other keys are ignored.
# Order matches the demo flow: READY first (opens the store),
# PACKED + SUPPLY_BUY are the during-demo actions, CANCEL is the
# escape hatch. COUNT_NOW + CONFIRM are secondary.
ACTION_KEY_TO_NAME = {
    KEY_READY: "ready",
    KEY_PACKED: "packed",
    KEY_CANCEL: "cancel",
    KEY_SUPPLY_BUY: "supply_buy",
    KEY_COUNT_NOW: "count_now",
    KEY_CONFIRM: "confirm",
}

# Color palette. Brightness is kept modest so the keypad is readable
# in daylight without being a stage spotlight.
COLOR_OFF = (0, 0, 0)
COLOR_DIM_GREY = (12, 12, 12)
COLOR_GREEN = (0, 170, 30)
COLOR_GREEN_SOFT = (0, 130, 60)
COLOR_AMBER = (220, 130, 0)
COLOR_RED = (180, 0, 0)
COLOR_BLUE = (0, 80, 220)
COLOR_CYAN = (0, 170, 170)
COLOR_GOLD = (220, 170, 0)
COLOR_WHITE = (160, 160, 160)
COLOR_VIOLET = (110, 0, 190)
COLOR_PURPLE = (140, 0, 200)
COLOR_MAGENTA = (190, 0, 130)
COLOR_APPLE = (200, 30, 10)
COLOR_BANANA = (230, 190, 0)

# Each row-0 action's resting color for its key.
ACTION_BASE_COLOR = {
    KEY_READY:      COLOR_GREEN,
    KEY_PACKED:     COLOR_AMBER,
    KEY_CANCEL:     COLOR_RED,
    KEY_SUPPLY_BUY: COLOR_CYAN,
    KEY_COUNT_NOW:  COLOR_VIOLET,
    KEY_CONFIRM:    COLOR_GREEN_SOFT,
}

HEALTH_COLORS = {
    "ok":      COLOR_GREEN,
    "warmup":  COLOR_AMBER,
    "mock":    COLOR_BLUE,
    "warn":    COLOR_AMBER,
    "fail":    COLOR_RED,
    "error":   COLOR_RED,
    "down":    COLOR_RED,
    "unknown": COLOR_DIM_GREY,
}


# --- State ----------------------------------------------------------


# Last full state payload from the host. Defaults are conservative
# (everything off) so a Pico that boots before the bridge connects
# shows nothing rather than stale data.
state = {
    "event": "state",
    "active_item": "",
    "active_count": 0,
    "active_low": False,
    "order_status": "",          # "" | "reserved" | "paid" | "packed" | "cancelled"
    "call_active": False,
    "payment_pending": False,
    "restock_status": "",        # "" | "pending_approval" | "approved" | "ordered" | ...
    "attention": {
        "ready":      False,
        "packed":     False,
        "cancel":     False,
        "supply_buy": False,
        "count_now":  False,
        "confirm":    False,
    },
    "health": {
        "camera": "unknown",
        "model": "unknown",
        "phone": "unknown",
    },
    "error_message": "",
}


# Brief white flash on the most recently pressed key -- gives haptic
# feedback even though the LED is on top of the silicone.
flash_index = -1
flash_until_ms = 0
FLASH_DURATION_MS = 180


# Bridge-pushed flashes: short LED pulses painted on top of the
# idle render to punctuate count changes (green=added, amber=removed,
# red=out-of-stock). Each entry:
#   {"index": 0-15, "color": (r,g,b), "until_ms": deadline}
active_flashes = []


# --- Helpers --------------------------------------------------------


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
    """Triangle-wave breathing between ``dim`` and ``base``.

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


# --- Render -- one paint function per layer ------------------------


def paint_legend(ts):
    """Layer 1: every key gets its resting glow.

    This is the "what is this key for?" layer. Operator can read
    the layout in the dark even when nothing is pending.
    """

    # Row 0 action keys -- modest glow in their action color.
    for index, base in ACTION_BASE_COLOR.items():
        set_pad(index, scale(base, 18))

    # Row 1 status indicators (call / payment) -- very dim.
    set_pad(KEY_CALL_ACTIVE, scale(COLOR_BLUE, 12))
    set_pad(KEY_PAYMENT,     scale(COLOR_GOLD, 12))

    # Row 2 item + order indicators -- dim resting colors.
    set_pad(KEY_APPLE,       scale(COLOR_APPLE, 12))
    set_pad(KEY_BANANA,      scale(COLOR_BANANA, 12))
    set_pad(KEY_RESERVATION, scale(COLOR_PURPLE, 10))
    set_pad(KEY_PAID,        scale(COLOR_GREEN, 12))

    # Row 3 health -- baseline dim; paint_health overrides on status.
    set_pad(KEY_CAMERA, scale(COLOR_BLUE, 10))
    set_pad(KEY_MODEL,  scale(COLOR_VIOLET, 10))
    set_pad(KEY_PHONE,  scale(COLOR_MAGENTA, 10))
    set_pad(KEY_ERROR,  COLOR_DIM_GREY)


def paint_attention(ts):
    """Layer 2: breathe row-0 action keys that need operator focus.

    The attention dict is the bridge's signal that "this action
    has something pending." A breathing key in the operator's
    peripheral vision pulls the eye in.
    """

    attention = state.get("attention", {})
    for index, action in ACTION_KEY_TO_NAME.items():
        if not attention.get(action):
            continue
        base = ACTION_BASE_COLOR.get(index, COLOR_DIM_GREY)
        # Faster breathe = more urgent. SUPPLY_BUY (money-moving)
        # gets the fastest breath because it's the highest-stakes
        # decision; the rest share a calmer cadence.
        period = 380 if action == "supply_buy" else 600
        set_pad(index, breathe(base, scale(base, 12), ts, period))


def paint_item(ts):
    """Layer 3: glow whichever fruit is the active item.

    Picks the row-2 cell that matches ``active_item`` (apple or
    banana) and breathes it. Other fruits stay at legend brightness.
    """

    active = (state.get("active_item") or "").lower()
    if active in ("apple", "apples"):
        set_pad(KEY_APPLE, breathe(COLOR_APPLE, scale(COLOR_APPLE, 25), ts, 900))
    elif active in ("banana", "bananas"):
        set_pad(KEY_BANANA, breathe(COLOR_BANANA, scale(COLOR_BANANA, 25), ts, 900))


def paint_order(ts):
    """Layer 4: row-2 cells 10 + 11 reflect the most recent order."""

    status = (state.get("order_status") or "").lower()
    if status == "reserved":
        set_pad(KEY_RESERVATION, breathe(COLOR_PURPLE, scale(COLOR_PURPLE, 15), ts, 700))
    elif status == "paid":
        set_pad(KEY_PAID, breathe(COLOR_GREEN, scale(COLOR_GREEN, 15), ts, 620))
    elif status == "packed":
        set_pad(KEY_PAID, scale(COLOR_GREEN, 75))


def paint_workflow(ts):
    """Layer 5: row-1 status indicators (call active + payment)."""

    if state.get("call_active"):
        set_pad(KEY_CALL_ACTIVE, breathe(COLOR_BLUE, scale(COLOR_BLUE, 18), ts, 650))
    if state.get("payment_pending"):
        set_pad(KEY_PAYMENT, breathe(COLOR_GOLD, scale(COLOR_GOLD, 18), ts, 520))


def paint_supply_buy_state(ts):
    """Layer 6: SUPPLY_BUY (key 3) shows the restock pipeline state.

    Overrides the attention layer's generic breathing with a
    state-specific animation so the operator can see whether the
    payment is in flight, completed, or failed.
    """

    status = str(state.get("restock_status") or "")
    if status == "pending_approval":
        # Handled by paint_attention via supply_buy=True; we only
        # override here for non-pending phases.
        return
    if status in ("approved", "payment_started"):
        set_pad(KEY_SUPPLY_BUY, breathe(COLOR_CYAN, scale(COLOR_CYAN, 15), ts, 420))
    elif status in ("ordered", "received"):
        set_pad(KEY_SUPPLY_BUY, scale(COLOR_GREEN, 70))
    elif status in ("failed", "rejected"):
        set_pad(KEY_SUPPLY_BUY, blink(COLOR_RED, scale(COLOR_RED, 12), ts, 320))


def paint_health(ts):
    """Layer 7: row-3 subsystem health (camera / model / phone / error)."""

    health = state.get("health", {})
    for index, key in (
        (KEY_CAMERA, "camera"),
        (KEY_MODEL,  "model"),
        (KEY_PHONE,  "phone"),
    ):
        status = (health.get(key) or "unknown").lower()
        color = HEALTH_COLORS.get(status, COLOR_DIM_GREY)
        if status == "warmup":
            set_pad(index, breathe(color, scale(color, 10), ts, 900))
        elif status in ("warn", "fail", "error", "down"):
            set_pad(index, blink(color, scale(color, 10), ts, 320))
        elif status == "ok":
            set_pad(index, scale(color, 70))
        # "mock" / "unknown" stay at legend brightness (already set).

    if state.get("error_message"):
        set_pad(KEY_ERROR, blink(COLOR_RED, COLOR_WHITE, ts, 220))


def paint_flashes(ts):
    """Layer 8: bridge-pushed transient flashes (count changes)."""

    if not active_flashes:
        return
    still_active = []
    for flash in active_flashes:
        if ts >= flash["until_ms"]:
            continue
        set_pad(flash["index"], flash["color"])
        still_active.append(flash)
    active_flashes[:] = still_active


def paint():
    if keypad is None:
        return
    ts = now_ms()
    if hasattr(keypad, "clear"):
        keypad.clear()
    paint_legend(ts)
    paint_attention(ts)
    paint_item(ts)
    paint_order(ts)
    paint_workflow(ts)
    paint_supply_buy_state(ts)
    paint_health(ts)
    paint_flashes(ts)
    # Press flash is the final overlay so the operator sees their
    # press regardless of what every other layer painted there.
    if flash_index >= 0 and ts < flash_until_ms:
        set_pad(flash_index, COLOR_WHITE)
    keypad.update()


# --- Input ----------------------------------------------------------


def emit_hello():
    write_line({
        "event": "hello",
        "device": "fm-pico-keypad",
        "version": 2,
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
            flash_index = index
            flash_until_ms = now_ms() + FLASH_DURATION_MS
            write_line({
                "event": "button",
                "button": index + 1,  # 1-based for human readability
                "action": action,
            })

    return current


# --- Main -----------------------------------------------------------


def apply_host_payload(payload):
    """Merge an incoming host payload into ``state``.

    The host sends a full state snapshot every push, so we replace
    rather than patch.
    """

    global state

    if not isinstance(payload, dict):
        return
    event = payload.get("event")
    if event == "state":
        state = {
            "event":           "state",
            "active_item":     payload.get("active_item", ""),
            "active_count":    payload.get("active_count", 0),
            "active_low":      payload.get("active_low", False),
            "order_status":    payload.get("order_status", ""),
            "call_active":     payload.get("call_active", False),
            "payment_pending": payload.get("payment_pending", False),
            "restock_status":  payload.get("restock_status", ""),
            "attention":       payload.get("attention", state.get("attention", {})),
            "health":          payload.get("health", state.get("health", {})),
            "error_message":   payload.get("error_message", ""),
        }
        # Schedule any flashes the bridge pushed in this payload.
        flashes = payload.get("flashes") or []
        if isinstance(flashes, list):
            ts = now_ms()
            for f in flashes:
                if not isinstance(f, dict):
                    continue
                try:
                    idx = int(f.get("index", -1))
                    color = f.get("color") or [0, 0, 0]
                    dur = int(f.get("duration_ms", 600))
                except (TypeError, ValueError):
                    continue
                if idx < 0 or len(color) < 3:
                    continue
                active_flashes.append({
                    "index": idx,
                    "color": (int(color[0]), int(color[1]), int(color[2])),
                    "until_ms": ts + dur,
                })
    elif event == "error":
        state["error_message"] = payload.get("message", "error")


def setup():
    if keypad is not None:
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
