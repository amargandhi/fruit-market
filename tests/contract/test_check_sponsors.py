from __future__ import annotations

import httpx

from scripts import check_sponsors


def test_paysponge_probe_skips_when_disabled() -> None:
    probe = check_sponsors.probe_paysponge({})

    assert probe.ok
    assert probe.skipped
    assert probe.detail == "disabled (set SPONGE_ENABLED=1)"


def test_paysponge_probe_reads_current_agent(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"id": "agent_test"})

    monkeypatch.setattr(
        check_sponsors,
        "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    probe = check_sponsors.probe_paysponge(
        {
            "SPONGE_ENABLED": "1",
            "SPONGE_API_BASE": "https://api.wallet.paysponge.test",
            "SPONGE_API_KEY": "sponge_test",
        }
    )

    assert probe.ok
    assert not probe.skipped
    assert probe.detail == "GET /api/agents/me"
    assert seen["url"] == "https://api.wallet.paysponge.test/api/agents/me"
    assert seen["auth"] == "Bearer sponge_test"


def test_probe_safely_keeps_later_checks_from_aborting() -> None:
    def broken(_env: dict[str, str]) -> check_sponsors.Probe:
        raise httpx.ReadTimeout("timed out")

    probe = check_sponsors._probe_safely("Example", broken, {})

    assert not probe.ok
    assert probe.name == "Example"
    assert probe.detail == "request failed: ReadTimeout"
