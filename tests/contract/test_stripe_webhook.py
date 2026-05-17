from __future__ import annotations

import hashlib
import hmac

from fruit_market.integrations import stripe_checkout


def _stripe_signature(secret: str, timestamp: str, body: bytes) -> str:
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_webhook_verifies_signed_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    body = (
        b'{"id":"evt_test","object":"event","type":"checkout.session.completed",'
        b'"data":{"object":{"id":"cs_test","metadata":{"order_id":"ord_test"}}}}'
    )
    timestamp = "1790000000"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    event = stripe_checkout.verify_webhook(
        {"Stripe-Signature": _stripe_signature("whsec_test", timestamp, body)},
        body,
    )

    assert event["id"] == "evt_test"
    assert stripe_checkout.event_data_object(event)["id"] == "cs_test"


def test_stripe_webhook_rejects_bad_signature(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")

    try:
        stripe_checkout.verify_webhook(
            {"Stripe-Signature": "t=1790000000,v1=bad"},
            b'{"id":"evt_test","object":"event","type":"checkout.session.completed",'
            b'"data":{"object":{}}}',
        )
    except Exception:
        return

    raise AssertionError("bad Stripe signature was accepted")
