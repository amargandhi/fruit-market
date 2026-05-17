"""AgentPhone webhook route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from fruit_market.api.routes import get_services
from fruit_market.api.schemas import AgentPhoneWebhookEnvelope
from fruit_market.integrations import agentphone

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/phone")
async def handle_phone_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    if not agentphone.verify_webhook(request.headers, body):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    envelope = AgentPhoneWebhookEnvelope.model_validate(payload)
    from fruit_market.brain.gemini import handle_agentphone_message

    answer = handle_agentphone_message(envelope, payload, get_services(request))
    channel = _payload_channel(payload)
    if channel == "voice":
        return {"text": answer}
    return {"status": "ok", "text": answer}


def _payload_channel(payload: dict[str, Any]) -> str:
    channel = payload.get("channel")
    return channel if isinstance(channel, str) else ""
