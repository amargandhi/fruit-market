from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from fruit_market.api.app import app


def test_teach_confirm_state_and_active_item_routes() -> None:
    with TestClient(app) as client:
        proposal = client.post(
            "/api/teach",
            json={"transcript": "These are apples, $1.50, 6 of them"},
        ).json()["proposal"]

        confirmed = client.post(
            "/api/teach/confirm",
            json={"proposal_id": proposal["proposal_id"]},
        ).json()

        state = client.get("/api/state").json()
        assert confirmed["name"] == "apple"
        assert confirmed["physical_count"] == 6
        assert state["active_item_id"] == confirmed["item_id"]
        assert state["catalog"][0]["price_cents"] == 150

        switched = client.post(
            "/api/active-item",
            json={"item_id": confirmed["item_id"]},
        ).json()
        assert switched == {"item_id": confirmed["item_id"]}


def test_pack_order_route_marks_paid_order_packed() -> None:
    with TestClient(app) as client:
        services = client.app.state.services
        proposal = services.teach.propose("These are pears, $2.00, 4 of them")
        item = services.teach.confirm(proposal.id)
        order = services.orders.reserve(item.id, 1, "+15551234567")
        services.orders.mark_paid(order.id, "cs_test")

        packed = client.post(f"/api/orders/{order.id}/pack").json()

        assert packed == {"order_id": order.id, "status": "packed"}
        assert services.orders.get(order.id).status == "packed"


def test_phone_webhook_rejects_bad_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AGENTPHONE_WEBHOOK_SECRET", "whsec_test")
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/phone",
            content=b'{"event":"agent.message","data":{"message":"hi"}}',
            headers={
                "X-Webhook-Signature": "sha256=bad",
                "X-Webhook-Timestamp": str(int(time.time())),
            },
        )

    assert response.status_code == 401


def test_stripe_webhook_marks_order_paid(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    with TestClient(app) as client:
        services = client.app.state.services
        proposal = services.teach.propose("These are plums, $1.25, 5 of them")
        item = services.teach.confirm(proposal.id)
        order = services.orders.reserve(item.id, 1, "+15551234567")
        body = (
            b'{"id":"evt_test","object":"event","type":"checkout.session.completed",'
            b'"data":{"object":{"id":"cs_test","metadata":{"order_id":"'
            + order.id.encode()
            + b'"}}}}'
        )

        response = client.post(
            "/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": _stripe_signature("whsec_test", "1790000000", body)},
        )

        assert response.status_code == 200
        assert services.orders.get(order.id).status == "paid"


def _stripe_signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
