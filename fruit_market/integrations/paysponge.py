"""PaySponge wallet readiness checks.

This module intentionally exposes read-only calls. Spending flows for
restocking should go through the dedicated Sponge MCP boundary.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx

from fruit_market.integrations._http import (
    DEFAULT_TIMEOUT,
    auth_headers,
    json_object,
    required_env,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_API_BASE = "https://api.wallet.paysponge.com"
DEFAULT_MCP_URL = "https://api.wallet.paysponge.com/mcp"


class PaySpongeClient:
    def __init__(
        self,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_base = (api_base or os.environ.get("SPONGE_API_BASE", DEFAULT_API_BASE)).rstrip(
            "/"
        )
        self._api_key = api_key or required_env("SPONGE_API_KEY")
        self._client = client

    def get_current_agent(self) -> dict[str, Any]:
        return self._get("/api/agents/me")

    def get_balances(self, *, only_usdc: bool = False) -> dict[str, Any]:
        params = {"onlyUsdc": "true"} if only_usdc else None
        return self._get("/api/balances", params=params)

    def mcp_connection(self) -> dict[str, object]:
        return {
            "url": os.environ.get("SPONGE_MCP_URL", DEFAULT_MCP_URL),
            "headers": {"Authorization": f"Bearer {self._api_key}"},
        }

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._api_base}{path}"
        headers = auth_headers(self._api_key)
        if self._client is not None:
            response = self._client.get(
                url,
                headers=headers,
                params=params,
                timeout=DEFAULT_TIMEOUT,
            )
        else:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return json_object(response)


def get_current_agent() -> dict[str, Any]:
    return PaySpongeClient().get_current_agent()


def get_balances(*, only_usdc: bool = False) -> dict[str, Any]:
    return PaySpongeClient().get_balances(only_usdc=only_usdc)
