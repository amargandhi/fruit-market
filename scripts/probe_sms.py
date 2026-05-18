#!/usr/bin/env python3
"""Direct AgentPhone SMS smoke test (no Gemini, no webhook).

Posts straight to AgentPhone's /v1/messages with our current
SMS payload shape so we can see if delivery is unblocked at the
account level.

Run:
    .venv/bin/python scripts/probe_sms.py +19064011589
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from fruit_market.integrations import agentphone  # noqa: E402


def main() -> int:
    to = sys.argv[1] if len(sys.argv) > 1 else "+19064011589"
    body = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "Fruit Market SMS smoke test — if you got this, SMS works."
    )
    print(f"sending to {to}...")
    print(f"  body: {body!r}")
    try:
        msg_id = agentphone.send_sms(to, body)
        print(f"SUCCESS: message_id={msg_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
