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


def test_ready_action_always_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.post("/api/pico/action", json={"action": "ready"})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "ready"
        assert body["status"] == "ok"


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
