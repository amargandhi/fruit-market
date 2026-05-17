from __future__ import annotations

import httpx

from fruit_market.integrations import agentmail
from fruit_market.integrations._http import USER_AGENT
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
