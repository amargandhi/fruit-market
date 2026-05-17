from __future__ import annotations

import httpx

from fruit_market.integrations import agentmail
from fruit_market.integrations._http import USER_AGENT
from fruit_market.restock.projection import RestockRecord
from fruit_market.services.protocols import Order


def test_agentmail_receipt_request_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["User-Agent"]
        seen["auth"] = request.headers["Authorization"]
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"message_id": "mail_test"})

    monkeypatch.setenv("AGENTMAIL_API_BASE", "https://api.agentmail.test/v0")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am_test")
    monkeypatch.setenv("AGENTMAIL_ADDRESS", "fruit@agentmail.test")
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    order = Order(
        id="ord_test",
        item_id="item_apple",
        qty=2,
        total_cents=300,
        status="paid",
        customer_phone="+15551234567",
        stripe_session_id="cs_test",
    )

    assert agentmail.send_receipt("buyer@example.com", order) == "mail_test"
    assert seen["url"] == (
        "https://api.agentmail.test/v0/inboxes/fruit@agentmail.test/messages/send"
    )
    assert seen["user_agent"] == USER_AGENT
    assert seen["auth"] == "Bearer am_test"
    assert '"to":"buyer@example.com"' in str(seen["json"])
    assert '"subject":"Fruit Market receipt for order ord_test"' in str(seen["json"])


def test_agentmail_restock_approval_request_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["User-Agent"]
        seen["auth"] = request.headers["Authorization"]
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"message_id": "mail_restock"})

    monkeypatch.setenv("AGENTMAIL_API_BASE", "https://api.agentmail.test/v0")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am_test")
    monkeypatch.setenv("AGENTMAIL_ADDRESS", "fruit@agentmail.test")
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    proposal = RestockRecord(
        proposal_id="restock_item_1",
        stock_low_offset=1,
        item_id="item_apple",
        item_name="apple",
        qty=24,
        supplier_id="demo_fruit_supplier",
        supplier_name="Demo Fruit Supplier",
        unit_price_cents=50,
        amount_cents=1200,
        gateway_url="https://gateway.paysponge.test/orders",
        payload_hash="a" * 64,
        idempotency_key="item_apple:1",
        expires_at_iso="2026-05-17T20:00:00Z",
        status="pending_approval",
        eta_minutes=30,
        basket_url="https://supplier.test/basket?proposal_id=restock_item_1",
    )

    assert agentmail.send_restock_approval("operator@example.com", proposal) == "mail_restock"
    assert seen["url"] == (
        "https://api.agentmail.test/v0/inboxes/fruit@agentmail.test/messages/send"
    )
    assert seen["user_agent"] == USER_AGENT
    assert seen["auth"] == "Bearer am_test"
    assert '"to":"operator@example.com"' in str(seen["json"])
    assert '"subject":"Approve apple restock: $12.00"' in str(seen["json"])
    assert "https://supplier.test/basket" in str(seen["json"])
    assert "Press the Pico restock key" in str(seen["json"])


def test_agentmail_operator_order_notification_request_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["User-Agent"]
        seen["auth"] = request.headers["Authorization"]
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"message_id": "mail_operator"})

    monkeypatch.setenv("AGENTMAIL_API_BASE", "https://api.agentmail.test/v0")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am_test")
    monkeypatch.setenv("AGENTMAIL_ADDRESS", "fruit@agentmail.test")
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    order = Order(
        id="ord_test",
        item_id="item_apple",
        qty=3,
        total_cents=300,
        status="paid",
        customer_phone="+15551234567",
        stripe_session_id="cs_test",
    )

    assert (
        agentmail.send_operator_order_notification(
            "operator@example.com",
            order,
            "apple",
        )
        == "mail_operator"
    )
    assert seen["url"] == (
        "https://api.agentmail.test/v0/inboxes/fruit@agentmail.test/messages/send"
    )
    assert seen["user_agent"] == USER_AGENT
    assert seen["auth"] == "Bearer am_test"
    assert '"to":"operator@example.com"' in str(seen["json"])
    assert '"subject":"Set aside 3 apples for order ord_test"' in str(seen["json"])
