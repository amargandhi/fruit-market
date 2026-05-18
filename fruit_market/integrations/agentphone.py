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
        agent_id: str | None = None,
        imessage_number_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_base = (api_base or required_env("AGENTPHONE_API_BASE")).rstrip("/")
        self._api_key = api_key or required_env("AGENTPHONE_API_KEY")
        # AgentPhone's /v1/messages requires ``agent_id`` (the
        # routing identifier for which configured agent should
        # send the message). Discovered via API 422 response:
        # ``{"error":{"details":[{"field":"body.agent_id","type":"missing"}]}}``.
        self._agent_id = agent_id or required_env("AGENTPHONE_AGENT_ID")
        self._imessage_number_id = imessage_number_id or optional_env(
            "AGENTPHONE_IMESSAGE_NUMBER_ID"
        )
        self._client = client

    def send_sms(self, to: str, body: str) -> str:
        # Field names confirmed via AgentPhone's 422 error body:
        #   {"field":"body.agent_id","type":"missing"}
        #   {"field":"body.to_number","type":"missing"}
        payload = {
            "agent_id": self._agent_id,
            "to_number": to,
            "body": body,
            "channel": "sms",
        }
        return self._post_message(payload)

    def send_imessage(self, to: str, body: str) -> str:
        if not self._imessage_number_id:
            raise RuntimeError("AGENTPHONE_IMESSAGE_NUMBER_ID is not configured")
        payload = {
            "agent_id": self._agent_id,
            "to_number": to,
            "body": body,
            "channel": "imessage",
            "fromNumberId": self._imessage_number_id,
        }
        return self._post_message(payload)

    def _post_message(self, payload: dict[str, str]) -> str:
        # Retry up to 3 times on 5xx (AgentPhone's upstream is
        # behind Cloudflare and occasionally returns 502 / 503 /
        # 504; their own response body says retryable=true). Wait
        # a short, increasing backoff between attempts. 4xx errors
        # are NOT retried — those are payload-shape or auth bugs.
        import time as _time  # noqa: PLC0415

        last_response: httpx.Response | None = None
        for attempt in range(3):
            response = self._post_once(payload)
            if response.status_code < 500:
                last_response = response
                break
            last_response = response
            backoff_s = 0.5 * (2 ** attempt)  # 0.5, 1.0, 2.0
            _time.sleep(backoff_s)

        assert last_response is not None  # loop ran at least once
        response = last_response

        # On 4xx the server tells us WHY in the body — surface that
        # in the exception so the operator sees the actual field
        # validation error instead of a bare status code.
        if response.status_code >= 400:
            try:
                detail = response.text[:600]
            except Exception:  # noqa: BLE001
                detail = "<unreadable body>"
            raise RuntimeError(
                f"AgentPhone /messages returned {response.status_code} "
                f"after retries: {detail} (sent fields: {sorted(payload.keys())})"
            )
        data = json_object(response)
        message_id = data.get("message_id") or data.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("AgentPhone response did not include a message id")
        return message_id

    def _post_once(self, payload: dict[str, str]) -> httpx.Response:
        """One HTTP POST to ``/messages``. No retry logic here —
        ``_post_message`` is the retrying wrapper."""

        headers = auth_headers(self._api_key)
        if self._client is not None:
            return self._client.post(
                f"{self._api_base}/messages",
                headers=headers,
                json=payload,
                timeout=DEFAULT_TIMEOUT,
            )
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            return client.post(
                f"{self._api_base}/messages",
                headers=headers,
                json=payload,
            )


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
