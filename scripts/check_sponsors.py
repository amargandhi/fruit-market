"""Ping sponsor APIs using credentials from .env.

Run via ``make check-sponsors`` (or ``uv run python scripts/check_sponsors.py``).

Exits 0 iff every required endpoint returns 200. Optional stretch
services can report SKIP without failing the command. The script
never prints secret values — only status codes and short,
non-sensitive identifiers. Safe to commit the output to a build log.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from dotenv import load_dotenv

if TYPE_CHECKING:
    from collections.abc import Callable

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
    skipped: bool = False


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


def probe_paysponge(env: dict[str, str]) -> Probe:
    key = env.get("SPONGE_API_KEY", "")
    enabled = _enabled(env.get("SPONGE_ENABLED", "")) or bool(key and "REPLACE_ME" not in key)
    if not enabled:
        return Probe("PaySponge", True, 0, "disabled (set SPONGE_ENABLED=1)", skipped=True)
    if not key or "REPLACE_ME" in key:
        return Probe("PaySponge", False, 0, "SPONGE_API_KEY not set")
    base = env.get("SPONGE_API_BASE", "https://api.wallet.paysponge.com").rstrip("/")
    with _client() as c:
        r = c.get(f"{base}/api/agents/me", headers={"Authorization": f"Bearer {key}"})
    return Probe("PaySponge", r.status_code == 200, r.status_code, "GET /api/agents/me")


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on", "live"}


def _probe_safely(
    name: str,
    fn: Callable[[dict[str, str]], Probe],
    env: dict[str, str],
) -> Probe:
    try:
        return fn(env)
    except httpx.HTTPError as exc:
        return Probe(name, False, 0, f"request failed: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001
        return Probe(name, False, 0, f"probe failed: {type(exc).__name__}")


def main() -> int:
    load_dotenv()
    env = {k: v for k, v in os.environ.items() if v is not None}

    checks = [
        ("AgentPhone", probe_agentphone),
        ("Gemini", probe_gemini),
        ("Stripe", probe_stripe),
        ("AgentMail", probe_agentmail),
        ("Moss", probe_moss),
        ("PaySponge", probe_paysponge),
    ]
    probes = [_probe_safely(name, fn, env) for name, fn in checks]

    print(f"{'sponsor':<12} {'code':<6} {'status':<8} {'detail'}")
    print("-" * 70)
    for p in probes:
        status = "SKIP" if p.skipped else "OK" if p.ok else "FAIL"
        print(f"{p.name:<12} {p.code:<6} {status:<8} {p.detail}")

    failed = [p for p in probes if not p.ok]
    if failed:
        print(f"\n{len(failed)} of {len(probes)} sponsors unreachable.", file=sys.stderr)
        return 1
    print(f"\nAll {len(probes)} sponsors reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
