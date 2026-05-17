"""Hardware adapters (Pico keypad bridge).

The bridge process talks to the firmware over USB serial and to the
fruit-market HTTP API. It can run in-process with the FastAPI app
or as a separate sidecar — we ship it as a sidecar so a Pico crash
can't take down the customer-facing brain.

Public surface:

* :class:`PicoStatePayload` — what the bridge sends to the firmware
  every push (full snapshot, never a patch).
* :class:`PicoButtonEvent` — what the firmware sends back.
* :data:`ACTION_NAMES` — canonical action names the firmware will
  emit; anything else is treated as garbage and dropped.
"""

from fruit_market.hardware.pico_protocol import (
    ACTION_NAMES,
    HEALTH_VALUES,
    PicoButtonEvent,
    PicoHello,
    PicoStatePayload,
    parse_event_line,
    serialize_state,
)

__all__ = [
    "ACTION_NAMES",
    "HEALTH_VALUES",
    "PicoButtonEvent",
    "PicoHello",
    "PicoStatePayload",
    "parse_event_line",
    "serialize_state",
]
