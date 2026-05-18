"""Contract tests for ``POST /api/pico/action``.

These verify the dispatch behavior the Pico bridge depends on:

* ``ready`` always 200s.
* ``packed`` finds the most recent paid order and packs it.
* ``cancel`` finds the most recent reserved order and cancels it.
* Unknown actions return 400.
* Missing pending state returns ``status=no_pending`` (200, not 404)
  so the bridge logs but doesn't escalate.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fruit_market.api.app import app


def _seed_paid_order(client: TestClient) -> str:
    services = client.app.state.services
    proposal = services.teach.propose(
        "These are bananas, $1.00, 6 of them"
    )
    item = services.teach.confirm(proposal.id)
    order = services.orders.reserve(item.id, 2, "+15551234567")
    services.orders.mark_paid(order.id, "cs_test_pico")
    return order.id


def _seed_reserved_order(client: TestClient) -> str:
    services = client.app.state.services
    proposal = services.teach.propose(
        "These are pears, $2.00, 4 of them"
    )
    item = services.teach.confirm(proposal.id)
    order = services.orders.reserve(item.id, 1, "+15551234567")
    return order.id


def test_ready_action_flips_open_for_orders() -> None:
    """READY = "shelf confirmed; open for phone orders."

    Note this does NOT gate video or inference (both run from
    boot regardless). It only flips the semantic ``demo_active``
    flag the kiosk surfaces as the "Open for orders" pill.
    """

    with TestClient(app) as client:
        # First press flips demo_active to True with status="ready".
        response = client.post("/api/pico/action", json={"action": "ready"})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "ready"
        assert body["status"] == "ready"
        # Flag is now on.
        assert client.get("/api/demo/active").json()["demo_active"] is True
        # Second press is idempotent.
        again = client.post("/api/pico/action", json={"action": "ready"}).json()
        assert again["status"] == "already_ready"


def test_demo_start_stop_endpoints() -> None:
    with TestClient(app) as client:
        assert client.get("/api/demo/active").json() == {"demo_active": False}
        assert client.post("/api/demo/start").json() == {"demo_active": True}
        assert client.get("/api/demo/active").json() == {"demo_active": True}
        assert client.post("/api/demo/stop").json() == {"demo_active": False}


def test_packed_action_packs_paid_order() -> None:
    with TestClient(app) as client:
        order_id = _seed_paid_order(client)
        response = client.post("/api/pico/action", json={"action": "packed"})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "packed"
        assert body["status"] == "ok"
        assert body["order_id"] == order_id


def test_packed_action_returns_no_pending_when_no_paid_orders() -> None:
    with TestClient(app) as client:
        response = client.post("/api/pico/action", json={"action": "packed"})
        assert response.status_code == 200
        assert response.json()["status"] == "no_pending"


def test_cancel_action_cancels_reservation() -> None:
    with TestClient(app) as client:
        order_id = _seed_reserved_order(client)
        response = client.post("/api/pico/action", json={"action": "cancel"})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "cancel"
        assert body["status"] == "ok"
        assert body["order_id"] == order_id


def test_count_now_is_acknowledged() -> None:
    with TestClient(app) as client:
        response = client.post("/api/pico/action", json={"action": "count_now"})
        assert response.status_code == 200
        assert response.json()["status"] == "acknowledged"


def test_unknown_action_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post("/api/pico/action", json={"action": "launch_missiles"})
        assert response.status_code == 400


def test_state_endpoint_now_includes_pending_and_health() -> None:
    """Bridge depends on these fields being present in /api/state."""

    with TestClient(app) as client:
        state = client.get("/api/state").json()
        assert "pending" in state
        assert set(state["pending"].keys()) >= {
            "teach_proposal", "paid_order", "reservation", "supply_buy",
        }
        assert "health" in state
        assert set(state["health"].keys()) >= {"camera", "model", "phone"}


def test_cancel_action_also_cancels_paid_orders() -> None:
    """If no reserved order exists, CANCEL falls back to the most
    recent paid order so the operator can unblock a wrongly-paid
    order from the keypad (refund handled out-of-band)."""

    with TestClient(app) as client:
        paid_order_id = _seed_paid_order(client)
        response = client.post("/api/pico/action", json={"action": "cancel"})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "cancel"
        assert body["status"] == "cancelled_paid"
        assert body["order_id"] == paid_order_id


def test_pico_debouncer_drops_rapid_duplicate_packed() -> None:
    """Two ``packed`` presses in quick succession: the second is
    debounced (status='debounced') so we don't double-pack."""

    with TestClient(app) as client:
        _seed_paid_order(client)
        first = client.post("/api/pico/action", json={"action": "packed"}).json()
        second = client.post("/api/pico/action", json={"action": "packed"}).json()
        assert first["status"] == "ok"
        assert second["status"] == "debounced"


def test_paid_order_cancellation_route() -> None:
    """``POST /api/orders/{id}/cancel`` works on paid orders too,
    so the kiosk can unblock a wrongly-paid order from the UI."""

    with TestClient(app) as client:
        order_id = _seed_paid_order(client)
        response = client.post(f"/api/orders/{order_id}/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["order_id"] == order_id
        assert body["status"] == "cancelled"


def test_teach_undo_zeroes_stock() -> None:
    """``DELETE /api/teach/{item_id}`` zeroes the item's stock so it
    stops appearing as available on the kiosk + phone agent."""

    with TestClient(app) as client:
        services = client.app.state.services
        proposal = services.teach.propose("These are kiwis, $1.50, 4 of them")
        item = services.teach.confirm(proposal.id)
        assert item.physical_count == 4

        response = client.delete(f"/api/teach/{item.id}")
        assert response.status_code == 200

        state = client.get("/api/state").json()
        kiwi = next(it for it in state["catalog"] if it["item_id"] == item.id)
        assert kiwi["physical_count"] == 0
