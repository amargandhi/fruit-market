"""Tests for the Pico bridge with mocked serial + API.

The bridge has three interesting things to test that don't need
real hardware:

1. ``api_state_to_payload`` correctly maps the API snapshot shape.
2. The reader loop dispatches button events to the API client.
3. The pusher loop only writes when the serialized state changes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fruit_market.hardware.pico_bridge import (
    PicoBridge,
    api_state_to_payload,
)
from fruit_market.hardware.pico_protocol import (
    PicoStatePayload,
    serialize_state,
)

# ─── api_state_to_payload ──────────────────────────────────────────


def test_api_state_to_payload_extracts_active_item_from_catalog() -> None:
    api_state = {
        "active_item_id": "item_abc",
        "catalog": [
            {"item_id": "item_xyz", "name": "apple", "physical_count": 7, "is_low": False},
            {"item_id": "item_abc", "name": "banana", "physical_count": 2, "is_low": True},
        ],
        "pending": {"supply_buy": True},
        "health": {"camera": "ok", "model": "warmup", "phone": "ok"},
    }
    payload = api_state_to_payload(api_state)
    assert payload.active_item == "banana"
    assert payload.active_count == 2
    assert payload.active_low is True
    assert payload.attention["supply_buy"] is True
    assert payload.attention["packed"] is False  # not pending


def test_api_state_to_payload_tolerates_missing_fields() -> None:
    payload = api_state_to_payload({})  # nothing at all
    assert payload.active_item == ""
    assert payload.active_count == 0
    # ``ready`` defaults to attention=True when demo_active isn't
    # set (the operator hasn't pressed READY yet). The other five
    # action keys default to attention=False because nothing is
    # pending.
    assert payload.attention == {
        "ready": True,
        "packed": False,
        "cancel": False,
        "supply_buy": False,
        "count_now": False,
        "confirm": False,
    }
    assert payload.health == {"camera": "unknown", "model": "unknown", "phone": "unknown"}
    # New fields default safely.
    assert payload.order_status == ""
    assert payload.payment_pending is False
    assert payload.call_active is False


def test_api_state_to_payload_reflects_demo_active_for_ready_attention() -> None:
    """Once the operator presses READY, demo_active flips True and
    the READY key should stop demanding attention."""

    payload = api_state_to_payload({"demo_active": True})
    assert payload.attention["ready"] is False


def test_api_state_to_payload_maps_order_status() -> None:
    """A paid order should drive order_status=paid + clear
    payment_pending (which only fires for unpaid reservations)."""

    payload = api_state_to_payload({
        "orders": [
            {"order_id": "o1", "status": "paid", "item_id": "i", "item_name": "a",
             "qty": 1, "total_cents": 100, "customer_phone": "+1"},
        ],
    })
    assert payload.order_status == "paid"
    assert payload.payment_pending is False


def test_api_state_to_payload_flags_payment_pending_on_reservation() -> None:
    payload = api_state_to_payload({
        "orders": [
            {"order_id": "o1", "status": "reserved", "item_id": "i", "item_name": "a",
             "qty": 1, "total_cents": 100, "customer_phone": "+1"},
        ],
    })
    assert payload.payment_pending is True
    assert payload.order_status == "reserved"


def test_api_state_to_payload_maps_pending_to_attention() -> None:
    api_state = {
        "active_item_id": "i",
        "catalog": [],
        "pending": {
            "teach_proposal": "prop_123",
            "paid_order": "ord_456",
            "reservation": "ord_789",
            "supply_buy": True,
        },
    }
    payload = api_state_to_payload(api_state)
    assert payload.attention["confirm"] is True
    assert payload.attention["packed"] is True
    assert payload.attention["cancel"] is True
    assert payload.attention["supply_buy"] is True


def test_api_state_to_payload_maps_restock_status_and_cancel_attention() -> None:
    payload = api_state_to_payload({
        "pending": {"supply_buy": True},
        "restock": {"status": "pending_approval"},
    })
    assert payload.restock_status == "pending_approval"
    assert payload.attention["supply_buy"] is True
    assert payload.attention["cancel"] is True


# ─── Bridge with mocked serial + API ───────────────────────────────


class _FakeSerial:
    """Test double for pyserial.Serial.

    Mimics enough of the interface the bridge uses:
    ``readline()``, ``write()``, ``flush()``, ``close()``.
    """

    def __init__(self) -> None:
        self.write_buffer: bytearray = bytearray()
        self.flushes = 0
        self.closed = False
        self._read_queue: list[bytes] = []
        self._lock = threading.Lock()

    def queue_line(self, payload: bytes) -> None:
        with self._lock:
            self._read_queue.append(payload if payload.endswith(b"\n") else payload + b"\n")

    def readline(self) -> bytes:
        with self._lock:
            if not self._read_queue:
                return b""
            return self._read_queue.pop(0)

    def write(self, data: bytes) -> int:
        self.write_buffer.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


class _FakeApi:
    """Test double for ApiClient.

    Records every call so tests can assert on dispatch behavior.
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state_lock = threading.Lock()
        self._state = state or {}
        self.actions_posted: list[str] = []
        self.state_fetches = 0

    def set_state(self, state: dict[str, Any]) -> None:
        with self._state_lock:
            self._state = state

    def fetch_state(self) -> dict[str, Any]:
        self.state_fetches += 1
        with self._state_lock:
            return dict(self._state)

    def post_action(self, action: str) -> dict[str, Any]:
        self.actions_posted.append(action)
        return {"ok": True}


def _build_bridge(serial_obj: _FakeSerial, api: _FakeApi) -> PicoBridge:
    bridge = PicoBridge(api=api, port="/dev/fake", state_interval_seconds=0.05)
    # Stub out the serial open path so we never touch real hardware.
    bridge._serial = serial_obj  # type: ignore[assignment]
    bridge._serial_port = "/dev/fake"
    return bridge


def test_bridge_dispatches_button_event_to_api(monkeypatch) -> None:
    serial_obj = _FakeSerial()
    api = _FakeApi()
    bridge = _build_bridge(serial_obj, api)
    serial_obj.queue_line(
        b'{"event":"button","button":1,"action":"confirm"}'
    )

    bridge.start()
    # Give the reader thread a chance to drain the queued line.
    deadline = time.monotonic() + 1.0
    while not api.actions_posted and time.monotonic() < deadline:
        time.sleep(0.02)
    bridge.stop()

    assert api.actions_posted == ["confirm"]


def test_bridge_pushes_state_to_serial_on_change(monkeypatch) -> None:
    serial_obj = _FakeSerial()
    api = _FakeApi(state={
        "active_item_id": "i1",
        "catalog": [{"item_id": "i1", "name": "banana", "physical_count": 3, "is_low": False}],
        "pending": {},
        "health": {"camera": "ok", "model": "ok", "phone": "ok"},
    })
    bridge = _build_bridge(serial_obj, api)

    bridge.start()
    deadline = time.monotonic() + 1.0
    while serial_obj.flushes == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    # Capture how much got pushed for the first state.
    first_push_size = len(serial_obj.write_buffer)
    flushes_after_first = serial_obj.flushes

    # If we ask for state a second time with NO change, nothing
    # new should get written.
    time.sleep(0.15)
    no_change_push_size = len(serial_obj.write_buffer)
    bridge.stop()

    assert first_push_size > 0
    assert flushes_after_first >= 1
    assert no_change_push_size == first_push_size, (
        "bridge pushed redundant state when nothing changed"
    )


def test_bridge_pushes_again_when_state_changes(monkeypatch) -> None:
    serial_obj = _FakeSerial()
    api = _FakeApi(state={
        "active_item_id": "i1",
        "catalog": [{"item_id": "i1", "name": "banana", "physical_count": 3, "is_low": False}],
        "pending": {},
        "health": {"camera": "ok", "model": "ok", "phone": "ok"},
    })
    bridge = _build_bridge(serial_obj, api)

    bridge.start()
    deadline = time.monotonic() + 1.0
    while serial_obj.flushes == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    size_after_first = len(serial_obj.write_buffer)

    # Mutate the state — count drops to 1, supply_buy pends.
    api.set_state({
        "active_item_id": "i1",
        "catalog": [{"item_id": "i1", "name": "banana", "physical_count": 1, "is_low": True}],
        "pending": {"supply_buy": True},
        "health": {"camera": "ok", "model": "ok", "phone": "ok"},
    })
    time.sleep(0.15)
    bridge.stop()

    assert len(serial_obj.write_buffer) > size_after_first, (
        "bridge did not push the new state after it changed"
    )


def test_serialize_state_is_deterministic_for_same_payload() -> None:
    """The bridge's "did the wire bytes change?" comparison only
    works if equal payloads serialize to equal bytes."""

    p = PicoStatePayload(
        active_item="banana", active_count=3,
        attention={"packed": True}, health={"camera": "ok"},
    )
    a = serialize_state(p)
    b = serialize_state(p)
    assert a == b
