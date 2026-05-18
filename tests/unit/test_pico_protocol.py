"""Pure-Python tests for the Pico wire protocol.

These cover serialize_state (bridge → firmware) and parse_event_line
(firmware → bridge). No serial. No HTTP. Run on any machine.
"""

from __future__ import annotations

import json

from fruit_market.hardware.pico_protocol import (
    ACTION_NAMES,
    HEALTH_VALUES,
    PicoButtonEvent,
    PicoHello,
    PicoStatePayload,
    parse_event_line,
    serialize_state,
)

# ─── serialize_state ────────────────────────────────────────────────


def test_serialize_state_round_trips() -> None:
    payload = PicoStatePayload(
        active_item="banana",
        active_count=3,
        active_low=False,
        attention={"packed": True, "supply_buy": True},
        health={"camera": "ok", "model": "warmup", "phone": "mock"},
    )
    wire = serialize_state(payload)
    assert wire.endswith(b"\n")
    parsed = json.loads(wire.decode())
    assert parsed["event"] == "state"
    assert parsed["active_item"] == "banana"
    assert parsed["active_count"] == 3
    assert parsed["restock_status"] == ""
    # All action names present in attention, defaulted to False.
    for name in ACTION_NAMES:
        assert name in parsed["attention"]
    assert parsed["attention"]["packed"] is True
    assert parsed["attention"]["supply_buy"] is True
    # ``cancel`` is one of the four top-row actions; absent in the
    # input dict → defaulted to False on the wire.
    assert parsed["attention"]["cancel"] is False
    # All three health fields present.
    assert parsed["health"]["camera"] == "ok"
    assert parsed["health"]["model"] == "warmup"
    assert parsed["health"]["phone"] == "mock"


def test_serialize_state_clamps_negative_count_to_zero() -> None:
    payload = PicoStatePayload(active_item="x", active_count=-5)
    parsed = json.loads(serialize_state(payload).decode())
    assert parsed["active_count"] == 0


def test_serialize_state_includes_restock_status() -> None:
    payload = PicoStatePayload(restock_status="pending_approval")
    parsed = json.loads(serialize_state(payload).decode())
    assert parsed["restock_status"] == "pending_approval"


def test_serialize_state_coerces_unknown_health_to_unknown() -> None:
    payload = PicoStatePayload(health={"camera": "ALIENS", "model": "ok", "phone": "ok"})
    parsed = json.loads(serialize_state(payload).decode())
    assert parsed["health"]["camera"] == "unknown"
    assert parsed["health"]["model"] == "ok"


def test_health_values_includes_all_documented_states() -> None:
    """Regression: the firmware HEALTH_COLORS map and protocol
    HEALTH_VALUES tuple must enumerate the same set."""

    expected = {"ok", "warmup", "mock", "warn", "fail", "error", "down", "unknown"}
    assert expected.issubset(set(HEALTH_VALUES))


# ─── parse_event_line ──────────────────────────────────────────────


def test_parse_button_event() -> None:
    line = '{"event":"button","button":2,"action":"packed"}\n'
    event = parse_event_line(line)
    assert isinstance(event, PicoButtonEvent)
    assert event.action == "packed"
    assert event.button == 2
    assert event.is_known_action()


def test_parse_button_event_accepts_bytes() -> None:
    event = parse_event_line(b'{"event":"button","button":1,"action":"ready"}')
    assert isinstance(event, PicoButtonEvent)
    assert event.action == "ready"


def test_parse_hello_event() -> None:
    line = (
        '{"event":"hello","device":"fm-pico-keypad","version":1,'
        '"actions":["confirm","packed","supply_buy"]}'
    )
    hello = parse_event_line(line)
    assert isinstance(hello, PicoHello)
    assert hello.device == "fm-pico-keypad"
    assert hello.version == 1
    assert "supply_buy" in hello.actions


def test_parse_returns_none_for_unknown_action() -> None:
    line = '{"event":"button","button":1,"action":"launch_missiles"}'
    assert parse_event_line(line) is None


def test_parse_returns_none_for_unknown_event_type() -> None:
    line = '{"event":"banana","payload":42}'
    assert parse_event_line(line) is None


def test_parse_returns_none_for_garbage() -> None:
    assert parse_event_line("not json at all") is None
    assert parse_event_line("") is None
    assert parse_event_line(b"\x00\x01\x02") is None
    assert parse_event_line('"a string"') is None  # valid JSON, not a dict
