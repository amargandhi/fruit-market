# Fruit Market — Pico keypad firmware

A Pimoroni RGB Keypad attached to a Pico 2 W gives the stall
operator a physical, glance-able control surface during the demo.
The top row is four real actions; the bottom three rows are one
large same-color visual status block for fruit movement, sales,
restock, and errors.

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

The keypad lights up once the firmware is running and the bridge
starts pushing backend state.

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
  "fruit_status": {
    "apple": {"count": 4, "active": false, "low": false},
    "banana": {"count": 3, "active": true, "low": false}
  },
  "attention": {
    "ready": false,
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
 "actions":["cancel","packed","ready","supply_buy"]}
```

## Layout

```
┌───────────┬───────────┬───────────┬───────────┐
│ 1 ready   │ 2 packed  │ 3 cancel  │ 4 supply  │
├───────────┼───────────┼───────────┼───────────┤
│        bottom 3x4 same-color status block       │
├───────────┼───────────┼───────────┼───────────┤
│        solid = steady, pulse/blink = event      │
├───────────┼───────────┼───────────┼───────────┤
│        green/amber/red/blue/cyan/gold together  │
└───────────┴───────────┴───────────┴───────────┘
```

Buttons 1-4 emit events. Buttons 5-16 are display-only — pressing
them does nothing, so an accidental finger on the table never
sends a bogus command.

- **Button 1 (ready)** pulses green until the operator opens the store.
- **Button 2 (packed)** pulses amber when a paid order is waiting.
- **Button 3 (cancel)** pulses red for a cancellable reservation or restock.
- **Button 4 (supply)** pulses amber when a restock approval is pending.
- **Bottom 3x4 blue solid** means open/steady.
- **Bottom 3x4 green pulse** means fruit added or sold/paid.
- **Bottom 3x4 amber pulse** means fruit removed, low stock, or restock pending.
- **Bottom 3x4 gold pulse** means checkout/payment pending.
- **Bottom 3x4 cyan pulse** means restock payment is in progress.
- **Bottom 3x4 red blink** means out of stock or error.
- **Bottom 3x4 green solid** means restock/order landed.

## Smoke test

With the bridge running:

```bash
# in one terminal: start the bridge
uv run python -m fruit_market.hardware.pico_bridge --serial /dev/cu.usbmodem*

# in another: simulate a state push directly
echo '{"event":"state","active_count":3,"active_item":"banana",
 "fruit_status":{"apple":{"count":4,"active":false,"low":false},
 "banana":{"count":3,"active":true,"low":false}},
 "attention":{"supply_buy":true},"health":{"camera":"ok","model":"warmup","phone":"mock"}}' \
  | mpremote connect /dev/cu.usbmodem* repl --inject-code='import sys; print(sys.stdin.readline().strip())'
```

The keypad should breathe the supply key and paint the whole
bottom 3x4 block one matching status color for the current event.
