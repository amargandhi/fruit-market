"""Stripe webhook route."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from fruit_market.api.routes import get_services
from fruit_market.integrations import stripe_checkout

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def handle_stripe_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    try:
        event = stripe_checkout.verify_webhook(request.headers, body)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid signature") from exc

    event_type = event["type"]
    if event_type not in {"checkout.session.completed", "payment_intent.succeeded"}:
        return {"status": "ignored"}

    obj = stripe_checkout.event_data_object(event)
    metadata = obj.get("metadata", {})
    order_id = metadata.get("order_id") if isinstance(metadata, dict) else None
    stripe_session_id = obj.get("id")
    if not isinstance(order_id, str) or not isinstance(stripe_session_id, str):
        return {"status": "ignored"}

    services = get_services(request)
    try:
        services.orders.mark_paid(order_id, stripe_session_id)
    except KeyError:
        return {"status": "ignored"}
    return {"status": "ok"}
