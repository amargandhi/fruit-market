from __future__ import annotations

import httpx

from fruit_market.integrations import paysponge


def test_paysponge_current_agent_request_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["accept"] = request.headers["Accept"]
        return httpx.Response(200, json={"id": "agent_test", "name": "Fruit Market"})

    monkeypatch.setenv("SPONGE_API_BASE", "https://api.wallet.paysponge.test")
    monkeypatch.setenv("SPONGE_API_KEY", "sponge_test")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    agent = paysponge.PaySpongeClient(client=client).get_current_agent()

    assert agent["id"] == "agent_test"
    assert seen["url"] == "https://api.wallet.paysponge.test/api/agents/me"
    assert seen["auth"] == "Bearer sponge_test"
    assert seen["accept"] == "application/json"


def test_paysponge_balances_can_request_usdc_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"balances": []})

    monkeypatch.setenv("SPONGE_API_KEY", "sponge_test")
    client = httpx.Client(transport=httpx.MockTransport(handler))

    balances = paysponge.PaySpongeClient(client=client).get_balances(only_usdc=True)

    assert balances == {"balances": []}
    assert seen["url"] == "https://api.wallet.paysponge.com/api/balances?onlyUsdc=true"
