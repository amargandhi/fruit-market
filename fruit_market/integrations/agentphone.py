"""AgentPhone HTTP client and webhook verifier."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING

import httpx

from fruit_market.integrations._http import (
    DEFAULT_TIMEOUT,
    auth_headers,
    json_object,
    optional_env,
    required_env,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SIGNATURE_TOLERANCE_SECONDS = 300


class AgentPhoneClient:
    def __init__(
        self,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        imessage_number_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_base = (api_base or required_env("AGENTPHONE_API_BASE")).rstrip("/")
        self._api_key = api_key or required_env("AGENTPHONE_API_KEY")
        self._imessage_number_id = imessage_number_id or optional_env(
            "AGENTPHONE_IMESSAGE_NUMBER_ID"
        )
        self._client = client

    def send_sms(self, to: str, body: str) -> str:
        payload = {"to": to, "body": body, "channel": "sms"}
        return self._post_message(payload)

    def send_imessage(self, to: str, body: str) -> str:
        if not self._imessage_number_id:
            raise RuntimeError("AGENTPHONE_IMESSAGE_NUMBER_ID is not configured")
        payload = {
            "to": to,
            "body": body,
            "channel": "imessage",
            "fromNumberId": self._imessage_number_id,
        }
        return self._post_message(payload)

    def _post_message(self, payload: dict[str, str]) -> str:
        headers = auth_headers(self._api_key)
        if self._client is not None:
            response = self._client.post(
                f"{self._api_base}/messages",
                headers=headers,
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            )
        else:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                response = client.post(
                    f"{self._api_base}/messages",
                    headers=headers,
                    json=payload,
                )
        response.raise_for_status()
        data = json_object(response)
        message_id = data.get("message_id") or data.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("AgentPhone response did not include a message id")
        return message_id


def send_sms(to: str, body: str) -> str:
    return AgentPhoneClient().send_sms(to, body)


def send_imessage(to: str, body: str) -> str:
    return AgentPhoneClient().send_imessage(to, body)


def verify_webhook(headers: Mapping[str, str], body: bytes) -> bool:
    secret = optional_env("AGENTPHONE_WEBHOOK_SECRET")
    if not secret:
        return False

    signature = _header(headers, "X-Webhook-Signature")
    timestamp = _header(headers, "X-Webhook-Timestamp")
    if not signature or not timestamp:
        return False

    try:
        delivered_at = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - delivered_at) > SIGNATURE_TOLERANCE_SECONDS:
        return False

    signed = timestamp.encode("utf-8") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
