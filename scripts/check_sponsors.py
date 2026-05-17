"""Ping all 5 sponsor APIs using credentials from .env.

Run via ``make check-sponsors`` (or ``uv run python scripts/check_sponsors.py``).

Exits 0 iff every endpoint returns 200. The script never prints
secret values — only status codes and short, non-sensitive
identifiers. Safe to commit the output to a build log.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

# AgentPhone sits behind Cloudflare and rejects urllib's default
# User-Agent (HTTP 403 with Cloudflare error 1010). Every client in
# this codebase sets a real browser UA for that reason.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    code: int
    detail: str


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=15.0,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=False,
    )


def probe_agentphone(env: dict[str, str]) -> Probe:
    key = env.get("AGENTPHONE_API_KEY", "")
    base = env.get("AGENTPHONE_API_BASE", "").rstrip("/")
    if not key or not base or "REPLACE_ME" in key:
        return Probe("AgentPhone", False, 0, "AGENTPHONE_API_KEY not set")
    with _client() as c:
        r = c.get(f"{base}/agents", headers={"Authorization": f"Bearer {key}"})
    return Probe("AgentPhone", r.status_code == 200, r.status_code, "GET /agents")


def probe_gemini(env: dict[str, str]) -> Probe:
    key = env.get("GEMINI_API_KEY", "")
    if not key or "REPLACE_ME" in key:
        return Probe("Gemini", False, 0, "GEMINI_API_KEY not set")
    with _client() as c:
        r = c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
    return Probe("Gemini", r.status_code == 200, r.status_code, "GET /v1beta/models")


def probe_stripe(env: dict[str, str]) -> Probe:
    key = env.get("STRIPE_SECRET_KEY", "")
    if not key or "REPLACE_ME" in key:
        return Probe("Stripe", False, 0, "STRIPE_SECRET_KEY not set")
    if not key.startswith("sk_test_"):
        return Probe("Stripe", False, 0, "key is not sk_test_ (refuse to ping live mode)")
    with _client() as c:
        r = c.get("https://api.stripe.com/v1/balance", auth=(key, ""))
    return Probe("Stripe", r.status_code == 200, r.status_code, "GET /v1/balance")


def probe_agentmail(env: dict[str, str]) -> Probe:
    key = env.get("AGENTMAIL_API_KEY", "")
    addr = env.get("AGENTMAIL_ADDRESS", "")
    base = env.get("AGENTMAIL_API_BASE", "").rstrip("/")
    if not key or "REPLACE_ME" in key or not addr or "REPLACE_ME" in addr:
        return Probe("AgentMail", False, 0, "AGENTMAIL_API_KEY / ADDRESS not set")
    with _client() as c:
        r = c.get(
            f"{base}/inboxes/{addr}",
            headers={"Authorization": f"Bearer {key}"},
        )
    return Probe("AgentMail", r.status_code == 200, r.status_code, f"GET /inboxes/{addr}")


def probe_moss(env: dict[str, str]) -> Probe:
    pid = env.get("MOSS_PROJECT_ID", "")
    pkey = env.get("MOSS_PROJECT_KEY", "")
    base = env.get("MOSS_API_BASE", "").rstrip("/")
    if not pid or not pkey or "REPLACE_ME" in pid or "REPLACE_ME" in pkey:
        return Probe("Moss", False, 0, "MOSS_PROJECT_ID / KEY not set")
    # Moss is RPC-style: one endpoint, body carries action + creds.
    body = {"action": "listIndexes", "projectId": pid, "projectKey": pkey}
    with _client() as c:
        r = c.post(
            f"{base}/manage",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
    return Probe("Moss", r.status_code == 200, r.status_code, "POST /manage listIndexes")


def main() -> int:
    load_dotenv()
    env = {k: v for k, v in os.environ.items() if v is not None}

    probes = [
        probe_agentphone(env),
        probe_gemini(env),
        probe_stripe(env),
        probe_agentmail(env),
        probe_moss(env),
    ]

    print(f"{'sponsor':<12} {'code':<6} {'status':<8} {'detail'}")
    print("-" * 70)
    for p in probes:
        status = "OK" if p.ok else "FAIL"
        print(f"{p.name:<12} {p.code:<6} {status:<8} {p.detail}")

    failed = [p for p in probes if not p.ok]
    if failed:
        print(f"\n{len(failed)} of {len(probes)} sponsors unreachable.", file=sys.stderr)
        return 1
    print(f"\nAll {len(probes)} sponsors reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
