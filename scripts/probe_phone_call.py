#!/usr/bin/env python3
"""End-to-end probe of the AgentPhone → Gemini → Stripe → SMS flow.

Posts a signed fake-AgentPhone webhook to the local server and prints
the full response chain. Use this to verify the brain works
independent of network/tunnel issues.

Run:
    .venv/bin/python scripts/probe_phone_call.py
    .venv/bin/python scripts/probe_phone_call.py "I want 3 apples please"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()


def post_signed(transcript: str, api_base: str = "http://127.0.0.1:8000") -> None:
    secret = os.environ.get("AGENTPHONE_WEBHOOK_SECRET", "").strip()
    if not secret:
        print("FAIL: AGENTPHONE_WEBHOOK_SECRET not set in .env")
        sys.exit(2)

    caller_phone = "+15551234567"
    payload = {
        "event": "agent.message",
        "channel": "voice",
        "call": {
            "call_id": "call_test_abc",
            "from": caller_phone,
            "to": os.environ.get("AGENTPHONE_NUMBER", "+15559990000"),
        },
        "transcript": transcript,
    }
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    signature = f"sha256={digest}"

    print(f">>> POST {api_base}/webhooks/phone")
    print(f"    transcript: {transcript!r}")
    print(f"    caller: {caller_phone}")
    print()
    try:
        r = httpx.post(
            f"{api_base}/webhooks/phone",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "X-Webhook-Timestamp": ts,
            },
            timeout=90.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        sys.exit(1)

    print(f"<<< status: {r.status_code}")
    try:
        body = r.json()
        print("<<< body:")
        print(json.dumps(body, indent=2))
    except Exception:  # noqa: BLE001
        print(f"<<< body (raw): {r.text[:800]}")


if __name__ == "__main__":
    transcript = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Hi, can I get 2 bananas please?"
    )
    post_signed(transcript)
