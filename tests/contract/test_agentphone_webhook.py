from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import httpx

from fruit_market.integrations import agentphone
from fruit_market.integrations._http import USER_AGENT

FIXTURE = Path(__file__).parents[1] / "fixtures" / "agentphone_webhook.json"


def _signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_agentphone_webhook_accepts_valid_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    body = FIXTURE.read_bytes()
    timestamp = str(int(time.time()))
    monkeypatch.setenv("AGENTPHONE_WEBHOOK_SECRET", "whsec_test")

    assert agentphone.verify_webhook(
        {
            "X-Webhook-Signature": _signature("whsec_test", timestamp, body),
            "X-Webhook-Timestamp": timestamp,
        },
        body,
    )


def test_agentphone_webhook_rejects_invalid_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    body = FIXTURE.read_bytes()
    monkeypatch.setenv("AGENTPHONE_WEBHOOK_SECRET", "whsec_test")

    assert not agentphone.verify_webhook(
        {
            "X-Webhook-Signature": "sha256=bad",
            "X-Webhook-Timestamp": str(int(time.time())),
        },
        body,
    )


def test_agentphone_sms_uses_browser_user_agent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers["User-Agent"]
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"id": "msg_test"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ap = agentphone.AgentPhoneClient(
        api_base="https://api.agentphone.test/v1",
        api_key="apk_test",
        client=client,
    )

    assert ap.send_sms("+15551234567", "hello") == "msg_test"
    assert seen["url"] == "https://api.agentphone.test/v1/messages"
    assert seen["user_agent"] == USER_AGENT
    assert seen["auth"] == "Bearer apk_test"
    assert '"channel":"sms"' in str(seen["body"])
