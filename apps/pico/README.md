# Fruit Market — Pico keypad firmware

A Pimoroni RGB Keypad attached to a Pico 2 W gives the stall
operator a physical, glance-able control surface during the demo.
Five action keys + a stock gauge + three health LEDs + an error
strobe.

## Hardware

- Raspberry Pi Pico 2 W
- [Pimoroni Pico RGB Keypad Base](https://shop.pimoroni.com/products/pico-rgb-keypad-base) (4x4)
- USB-A → USB-C (or whichever connects your Pico to the host Mac)

## Flash MicroPython on the Pico (once per Pico)

1. Hold BOOTSEL on the Pico, plug it into the Mac. It mounts as
   `RPI-RP2` (or `RP2350` for Pico 2 W).
2. Download Pimoroni's MicroPython build for your Pico variant
   from <https://github.com/pimoroni/pimoroni-pico/releases>
   (look for `pimoroni-picow-…-micropython.uf2` for Pico W or the
   Pico 2 W equivalent — the file embeds the `picokeypad` driver).
3. Copy the `.uf2` to the mounted volume. The Pico reboots into
   MicroPython.

## Install this firmware

```bash
# `mpremote` is the simplest MicroPython tool — installs via pip.
pip install mpremote
mpremote connect /dev/cu.usbmodem* fs cp apps/pico/main.py :main.py
mpremote connect /dev/cu.usbmodem* reset
```

The keypad lights up in its idle state (dim grey across all 16
keys) once the firmware is running.

## Protocol

The firmware talks to the bridge over USB serial (the same TTY
that `mpremote` uses) as newline-delimited JSON.

### Bridge → firmware (push state)

```json
{
  "event": "state",
  "active_item": "banana",
  "active_count": 3,
  "active_low": false,
  "attention": {
    "confirm": false,
    "packed": true,
    "cancel": false,
    "supply_buy": true
  },
  "health": {"camera": "ok", "model": "ok", "phone": "mock"},
  "error_message": ""
}
```

The bridge sends a full snapshot every push (no patch protocol —
keeps the firmware stateless between messages and resilient to
dropped frames).

### Firmware → bridge (button events)

```json
{"event":"button","button":4,"action":"supply_buy"}
```

`button` is the 1-based key index for human reading; `action` is
the canonical event name the bridge dispatches.

### Bootstrap

On reset the firmware emits a hello so the bridge knows the device
is alive and which actions it can produce:

```json
{"event":"hello","device":"fm-pico-keypad","version":1,
 "actions":["cancel","confirm","count_now","packed","ready","supply_buy"]}
```

## Layout

```
┌───────────┬───────────┬───────────┬───────────┐
│ 1 confirm │ 2 packed  │ 3 cancel  │ 4 count   │
├───────────┼───────────┼───────────┼───────────┤
│ 5 supply  │ 6 ─       │ 7 ─       │ 8 ready   │
├───────────┼───────────┼───────────┼───────────┤
│ 9 stock   │10 stock   │11 stock   │12 stock   │
├───────────┼───────────┼───────────┼───────────┤
│13 cam     │14 model   │15 phone   │16 error   │
└───────────┴───────────┴───────────┴───────────┘
```

Buttons 1, 2, 3, 4, 5, 8 emit events. Buttons 6, 7, and all of
rows 3-4 are display-only — pressing them does nothing, so an
accidental finger on the table never sends a bogus command.

- **Button 5 (supply)** breathes teal when the host sets
  `attention.supply_buy = true` (e.g. inventory hit zero and the
  restock agent is asking to confirm a purchase). Press to ack.
- **Buttons 1, 2, 3** breathe in their action color when there's
  something to do (a pending teach proposal / paid order /
  reservation respectively).
- **Button 8 (ready)** always emits; useful as an "I'm here, send
  me the latest state" tap.

## Smoke test

With the bridge running:

```bash
# in one terminal: start the bridge
uv run python -m fruit_market.hardware.pico_bridge --serial /dev/cu.usbmodem*

# in another: simulate a state push directly
echo '{"event":"state","active_count":3,"active_item":"banana",
 "attention":{"supply_buy":true},"health":{"camera":"ok","model":"warmup","phone":"mock"}}' \
  | mpremote connect /dev/cu.usbmodem* repl --inject-code='import sys; print(sys.stdin.readline().strip())'
```

The keypad should: light 3 of the 4 stock-bar LEDs green, breathe
the supply key in teal, hold the model health key in pulsing
amber, and hold camera + phone in green/blue respectively.
