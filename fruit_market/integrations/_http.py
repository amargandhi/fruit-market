"""Shared HTTP helpers for sponsor integrations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
DEFAULT_TIMEOUT = 15.0


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or "REPLACE_ME" in value:
        raise RuntimeError(f"{name} is not configured")
    return value


def optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or "REPLACE_ME" in value:
        return None
    return value


def auth_headers(token: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if extra:
        headers.update(extra)
    return headers


def json_object(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("expected JSON object response")
    return cast("dict[str, Any]", payload)
