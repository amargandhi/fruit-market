from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from fruit_market.api.app import app


def test_teach_confirm_state_and_active_item_routes() -> None:
    # Uses "lemones" — not in the boot-seeded defaults (apple, banana)
    # so the teach path is exercised cleanly without colliding with a
    # pre-existing catalog entry.
    with TestClient(app) as client:
        proposal = client.post(
            "/api/teach",
            json={"transcript": "These are lemons, $1.50, 6 of them"},
        ).json()["proposal"]

        confirmed = client.post(
            "/api/teach/confirm",
            json={"proposal_id": proposal["proposal_id"]},
        ).json()

        state = client.get("/api/state").json()
        assert confirmed["name"] == "lemon"
        assert confirmed["physical_count"] == 6
        assert state["active_item_id"] == confirmed["item_id"]
        # Find the just-confirmed item by ID (catalog may contain
        # boot-seeded apple + banana ahead of it).
        lemon = next(
            item for item in state["catalog"] if item["item_id"] == confirmed["item_id"]
        )
        assert lemon["price_cents"] == 150

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


def test_stripe_webhook_sends_receipt_operator_email_and_sets_pico_pending(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("AGENTMAIL_ENABLED", "1")
    monkeypatch.setenv("OPERATOR_EMAIL", "operator@example.com")
    sent_receipts: list[tuple[str, str, str]] = []
    sent_operator: list[tuple[str, str, str]] = []

    from fruit_market.integrations import agentmail

    def fake_receipt(to_email, order, item_name=None):  # type: ignore[no-untyped-def]
        sent_receipts.append((to_email, order.id, item_name))
        return "mail_receipt"

    def fake_operator(to_email, order, item_name):  # type: ignore[no-untyped-def]
        sent_operator.append((to_email, order.id, item_name))
        return "mail_operator"

    monkeypatch.setattr(agentmail, "send_receipt", fake_receipt)
    monkeypatch.setattr(agentmail, "send_operator_order_notification", fake_operator)

    with TestClient(app) as client:
        services = client.app.state.services
        proposal = services.teach.propose("These are apples, $1.00, 6 of them")
        item = services.teach.confirm(proposal.id)
        order = services.orders.reserve(item.id, 3, "+15551234567")
        body = (
            b'{"id":"evt_test","object":"event","type":"checkout.session.completed",'
            b'"data":{"object":{"id":"cs_test_notify","customer_details":'
            b'{"email":"buyer@example.com"},"metadata":{"order_id":"'
            + order.id.encode()
            + b'"}}}}'
        )

        response = client.post(
            "/webhooks/stripe",
            content=body,
            headers={"Stripe-Signature": _stripe_signature("whsec_test", "1790000000", body)},
        )
        state = client.get("/api/state").json()

        assert response.status_code == 200
        assert services.orders.get(order.id).status == "paid"
        assert state["pending"]["paid_order"] == order.id

    assert sent_receipts == [("buyer@example.com", order.id, "apple")]
    assert sent_operator == [("operator@example.com", order.id, "apple")]


def _stripe_signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
