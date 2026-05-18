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
    | 4 apple act.| 5 apple +   | 6 apple -   | 7 apple out     |
    |   (red)     |   (green)   |   (amber)   |   (red)         |
    +-------------+-------------+-------------+-----------------+
    | 8 bana. act.| 9 bana. +   |10 bana. -   |11 bana. out     |
    |   (yellow)  |   (green)   |   (amber)   |   (red)         |
    +-------------+-------------+-------------+-----------------+
    |12 sold      |13 payment   |14 restock   |15 error         |
    |   (green)   |   (gold)    |   (cyan)    |   (red)         |
    +-------------+-------------+-------------+-----------------+

ONLY the top row emits button events. Every other key is visual-
only -- a stray finger on rows 1-3 never triggers anything.

Row 0 actions:
    READY      -> open the store / mark shelf confirmed
    PACKED     -> confirm pack of next paid order
    CANCEL     -> cancel reservation or pending restock
    SUPPLY_BUY -> approve PaySponge supplier payment

Rows 1-3: purely visual indicators. Driven by host state pushes;
they never fire events. Row 1 is apple state, row 2 is banana
state, and row 3 is sales/payment/restock/error state.

Action keys (0, 1, 2, 3) always emit on press regardless of
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

# Row 1 -- apple status indicators only (no buttons)
KEY_APPLE_ACTIVE = 4
KEY_APPLE_ADDED = 5
KEY_APPLE_REMOVED = 6
KEY_APPLE_OUT = 7

# Row 2 -- banana status indicators only (no buttons)
KEY_BANANA_ACTIVE = 8
KEY_BANANA_ADDED = 9
KEY_BANANA_REMOVED = 10
KEY_BANANA_OUT = 11

# Row 3 -- sales + backend status indicators only (no buttons)
KEY_SOLD = 12
KEY_PAYMENT = 13
KEY_RESTOCK = 14
KEY_ERROR = 15

STATUS_GRID_KEYS = (
    KEY_APPLE_ACTIVE,
    KEY_APPLE_ADDED,
    KEY_APPLE_REMOVED,
    KEY_APPLE_OUT,
    KEY_BANANA_ACTIVE,
    KEY_BANANA_ADDED,
    KEY_BANANA_REMOVED,
    KEY_BANANA_OUT,
    KEY_SOLD,
    KEY_PAYMENT,
    KEY_RESTOCK,
    KEY_ERROR,
)

# Indices that emit button events. ONLY the top row -- presses on
# rows 1-3 are silently ignored so a stray finger on a status
# indicator can never fire a real action. Order matches the demo
# flow: READY opens the store, PACKED + SUPPLY_BUY are the
# during-demo actions, CANCEL is the escape hatch.
ACTION_KEY_TO_NAME = {
    KEY_READY: "ready",
    KEY_PACKED: "packed",
    KEY_CANCEL: "cancel",
    KEY_SUPPLY_BUY: "supply_buy",
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
    "fruit_status": {
        "apple": {"count": 0, "active": False, "low": False},
        "banana": {"count": 0, "active": False, "low": False},
    },
    "order_status": "",          # "" | "reserved" | "paid" | "packed" | "cancelled"
    "call_active": False,
    "payment_pending": False,
    "restock_status": "",        # "" | "pending_approval" | "approved" | "ordered" | ...
    "attention": {
        "ready":      False,
        "packed":     False,
        "cancel":     False,
        "supply_buy": False,
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


# --- Render -- video-friendly paint pipeline -----------------------
#
# Design philosophy: a dark keypad is a calm keypad. Most of the
# time only one or two cells should be lit; events produce bright,
# brief animations. The full layout is shown by silkscreen labels
# on the keypad cover -- not by always-on LED glow.
#
# Idle steady state (nothing pending, no errors):
#   * Active fruit cell: soft solid glow so the operator can see
#     what the model is tracking right now.
#   * READY (key 0): slow pulse green IF the operator hasn't pressed
#     it yet (invites the first press). OFF after pressed.
#   * Everything else: OFF.
#
# Event-driven highlights (when something is actually happening):
#   * Count went up    -> green flash on the fruit's "+" cell
#   * Count went down  -> amber flash on the fruit's "-" cell
#   * Out of stock     -> red strobe on the fruit's "out" cell
#   * Paid order ready -> PACKED breathes amber until packed
#   * Restock pending  -> SUPPLY_BUY breathes fast amber until approved
#   * System error     -> red blink on ERROR
#
# Count-change flashes are pushed by the bridge as transient
# overlays (see paint_flashes); the firmware doesn't need to know
# about deltas -- it just paints whatever short-lived flashes the
# host scheduled.


def paint_legend(ts):
    """Layer 1: clear every key to OFF.

    A clean slate each frame -- subsequent paint layers only light
    up the cells that genuinely need attention. The keypad's
    silkscreen labels (or operator memory) tell the operator what
    each key does; LEDs are reserved for events.
    """

    for i in range(16):
        set_pad(i, COLOR_OFF)


def paint_ready_invite(ts):
    """Pulse the READY key gently until the operator presses it.

    Once demo_active is True (operator pressed READY), the attention
    bit clears and this layer stops painting. Keeps the cold-boot
    moment from looking like a dead device -- there's exactly one
    breathing key inviting the first interaction.
    """

    attention = state.get("attention", {})
    if attention.get("ready"):
        set_pad(KEY_READY, breathe(COLOR_GREEN, scale(COLOR_GREEN, 8), ts, 1400))


def paint_attention(ts):
    """Breathe row-0 action keys that have something pending.

    Only paints keys with a pending state. Keys without pending
    work stay OFF -- the operator's eye is drawn to the one
    breathing key instead of scanning a wall of lights.

    READY is handled by paint_ready_invite (slower cadence, calmer
    invitation). The other actions get a more urgent breathe when
    they fire.
    """

    attention = state.get("attention", {})
    if attention.get("packed"):
        set_pad(KEY_PACKED, breathe(COLOR_AMBER, scale(COLOR_AMBER, 10), ts, 600))
    if attention.get("cancel"):
        set_pad(KEY_CANCEL, breathe(COLOR_RED, scale(COLOR_RED, 10), ts, 600))
    if attention.get("supply_buy"):
        # Fastest breathe -- money-moving action, highest urgency.
        set_pad(KEY_SUPPLY_BUY, breathe(COLOR_AMBER, scale(COLOR_AMBER, 8), ts, 380))


def paint_status_grid(ts):
    """Paint rows 1-3 as one same-color video status block.

    For recording, the bottom 3x4 grid should read from across the
    room. Instead of tiny per-cell semantics, all twelve visual-only
    keys share one color and one animation for the highest-priority
    current event.
    """

    mode = "solid"
    color = COLOR_DIM_GREY

    health = state.get("health", {})
    health_problem = False
    if isinstance(health, dict):
        for key in ("camera", "model", "phone"):
            status = (health.get(key) or "unknown").lower()
            if status in ("warn", "fail", "error", "down"):
                health_problem = True

    # Highest priority: backend/device error.
    if state.get("error_message") or health_problem:
        mode = "blink"
        color = COLOR_RED
    else:
        # Transient fruit movement: bridge pushes green/amber/red
        # pulses. Any one of those makes the whole bottom grid pulse
        # the same color for clean video.
        still_active = []
        active_color = None
        for flash in active_flashes:
            if ts >= flash["until_ms"]:
                continue
            active_color = flash["color"]
            still_active.append(flash)
        active_flashes[:] = still_active
        if active_color is not None:
            mode = "pulse"
            color = active_color
        else:
            apple_count, _apple_active, apple_low = _fruit_info("apple")
            banana_count, _banana_active, banana_low = _fruit_info("banana")
            restock_status = str(state.get("restock_status") or "")
            order_status = (state.get("order_status") or "").lower()

            if _store_is_open() and (apple_count == 0 or banana_count == 0):
                mode = "blink"
                color = COLOR_RED
            elif restock_status == "pending_approval":
                mode = "pulse"
                color = COLOR_AMBER
            elif restock_status in ("approved", "payment_started"):
                mode = "pulse"
                color = COLOR_CYAN
            elif restock_status in ("ordered", "received"):
                mode = "solid"
                color = COLOR_GREEN
            elif state.get("payment_pending") or order_status == "reserved":
                mode = "pulse"
                color = COLOR_GOLD
            elif order_status in ("paid", "packed"):
                mode = "pulse" if order_status == "paid" else "solid"
                color = COLOR_GREEN
            elif apple_low or banana_low:
                mode = "pulse"
                color = COLOR_AMBER
            elif _store_is_open():
                mode = "solid"
                color = COLOR_BLUE

    if mode == "blink":
        painted = blink(color, COLOR_OFF, ts, 320)
    elif mode == "pulse":
        painted = breathe(color, scale(color, 10), ts, 520)
    else:
        painted = scale(color, 55 if color != COLOR_DIM_GREY else 100)

    for index in STATUS_GRID_KEYS:
        set_pad(index, painted)


def _fruit_info(name):
    """Return ``(count, active, low)`` for apple/banana.

    New bridge payloads include ``fruit_status`` for both fruits.
    The active_item fields remain as a fallback so older bridges
    still light the active cell.
    """

    fruit_status = state.get("fruit_status", {})
    info = {}
    if isinstance(fruit_status, dict):
        maybe = fruit_status.get(name)
        if isinstance(maybe, dict):
            info = maybe

    active_name = (state.get("active_item") or "").lower().rstrip("s")
    fallback_active = active_name == name
    try:
        count = int(info.get("count", state.get("active_count", 0) if fallback_active else 0) or 0)
    except (TypeError, ValueError):
        count = 0
    active = bool(info.get("active", fallback_active))
    low = bool(info.get("low", state.get("active_low", False) if fallback_active else False))
    return count, active, low


def _store_is_open():
    attention = state.get("attention", {})
    if isinstance(attention, dict) and attention.get("ready"):
        return False
    return True


def paint_fruit_status(ts):
    """Rows 1-2: apple and banana dashboard cells.

    Per fruit:
        ACTIVE  -> solid fruit color while the model is tracking it
        ADDED   -> flash overlay from the bridge when count rises
        REMOVED -> flash overlay from the bridge when count falls
        OUT     -> red blink after the store is opened and count is zero

    The added/removed cells are normally dark; flashes paint over
    this layer in ``paint_flashes``.
    """

    for name, active_key, out_key, color in (
        ("apple", KEY_APPLE_ACTIVE, KEY_APPLE_OUT, COLOR_APPLE),
        ("banana", KEY_BANANA_ACTIVE, KEY_BANANA_OUT, COLOR_BANANA),
    ):
        count, active, low = _fruit_info(name)
        if active:
            if low and count > 0:
                set_pad(active_key, breathe(COLOR_AMBER, scale(COLOR_AMBER, 12), ts, 900))
            else:
                set_pad(active_key, scale(color, 28))

        if _store_is_open() and count == 0:
            set_pad(out_key, blink(COLOR_RED, COLOR_OFF, ts, 360))


def paint_workflow(ts):
    """Row 3 sales/payment indicators only paint on active state.

    All of these default to OFF when nothing is happening. Each
    only lights when its specific event is in progress.
    """

    order_status = (state.get("order_status") or "").lower()
    if state.get("payment_pending") or order_status == "reserved":
        set_pad(KEY_PAYMENT, breathe(COLOR_GOLD, scale(COLOR_GOLD, 12), ts, 520))
    if order_status == "paid":
        set_pad(KEY_SOLD, breathe(COLOR_GREEN, scale(COLOR_GREEN, 15), ts, 620))
    elif order_status == "packed":
        # Briefly solid green so the operator sees "sold/packed"
        # before it fades; cleared on the next state push when the
        # order moves off the active list.
        set_pad(KEY_SOLD, scale(COLOR_GREEN, 60))


def paint_supply_buy_state(ts):
    """RESTOCK (key 14) shows the restock pipeline state.

    The paint_attention layer handles the "pending_approval" pulse
    on the top-row SUPPLY_BUY action key. This visual-only cell
    shows the restock pipeline phase before and after approval.
    """

    status = str(state.get("restock_status") or "")
    if status == "pending_approval":
        set_pad(KEY_RESTOCK, breathe(COLOR_AMBER, scale(COLOR_AMBER, 10), ts, 520))
    elif status in ("approved", "payment_started"):
        set_pad(KEY_RESTOCK, breathe(COLOR_CYAN, scale(COLOR_CYAN, 12), ts, 420))
    elif status in ("ordered", "received"):
        set_pad(KEY_RESTOCK, scale(COLOR_GREEN, 70))
    elif status in ("failed", "rejected"):
        set_pad(KEY_RESTOCK, blink(COLOR_RED, scale(COLOR_RED, 10), ts, 320))


def paint_health(ts):
    """ERROR cell paints only on backend/health problems.

    The grid is primarily an operator workflow surface, so detailed
    subsystem health stays in the kiosk. The Pico uses one catch-all
    ERROR cell for demo-visible failures.
    """

    health = state.get("health", {})
    problem = False
    warmup = False
    if isinstance(health, dict):
        for key in ("camera", "model", "phone"):
            status = (health.get(key) or "unknown").lower()
            if status == "warmup":
                warmup = True
            elif status in ("warn", "fail", "error", "down"):
                problem = True

    if state.get("error_message") or problem:
        set_pad(KEY_ERROR, blink(COLOR_RED, COLOR_WHITE, ts, 220))
    elif warmup:
        set_pad(KEY_ERROR, breathe(COLOR_AMBER, scale(COLOR_AMBER, 8), ts, 900))


def paint_flashes(ts):
    """Layer for transient bridge-pushed count-change flashes.

    Each flash is the bridge's signal that the model just noticed
    a fruit move:
      * green pulse on + cell     -> count went up (item added)
      * amber pulse on - cell     -> count went down (item removed/sold)
      * red strobe on out cell    -> count hit zero (out of stock)

    Flashes win over every other layer for the duration of their
    deadline, so a count change is visually the loudest event.
    """

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
    paint_legend(ts)              # everything off
    paint_ready_invite(ts)        # only if operator hasn't pressed READY
    paint_attention(ts)           # only keys that have pending work
    paint_status_grid(ts)         # lower 3x4 same-color video status block
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
            "fruit_status":    payload.get("fruit_status", state.get("fruit_status", {})),
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
