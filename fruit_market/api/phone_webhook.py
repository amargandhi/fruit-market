"""AgentPhone webhook route.

Two-tier dispatch keeps the hot path snappy:

1. **Fast-path (deterministic)** — if the transcript clearly asks
   to buy N {fruit}, ``brain.quick_intent`` catches it in
   microseconds. We respond IMMEDIATELY with "Got it, texting you
   the link now" so the caller never hears dead air, and spawn a
   FastAPI background task to do the slow Stripe+SMS work. The
   SMS lands within ~3 s of the call.

2. **Slow-path (LLM)** — anything not a clear purchase
   ("what's good today?", "do you have lemons?", "how much for two
   apples?") routes to Gemini Flash Lite with the full tool
   set. ~1-2 s sync, response goes straight back to the caller.

This keeps Gemini in the loop for chit-chat + product knowledge
while removing it from the latency-critical purchase hot path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from fruit_market.api.routes import get_services
from fruit_market.api.schemas import AgentPhoneWebhookEnvelope
from fruit_market.brain.quick_intent import detect_purchase
from fruit_market.brain.tool_specs import (
    CreateCheckoutInput,
    ReserveOrderInput,
    ResolveItemInput,
)
from fruit_market.integrations import agentphone

if TYPE_CHECKING:
    from fruit_market.brain.quick_intent import Purchase
    from fruit_market.services.protocols import Services

logger = logging.getLogger("fm.phone_webhook")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/phone")
async def handle_phone_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    body = await request.body()
    if not agentphone.verify_webhook(request.headers, body):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    envelope = AgentPhoneWebhookEnvelope.model_validate(payload)

    from fruit_market.brain.gemini import (  # noqa: PLC0415
        _caller_phone,
        _message_text,
        generate_reply,
    )

    transcript = _message_text(envelope, payload)
    caller_phone = _caller_phone(envelope, payload)
    services = get_services(request)
    channel = _payload_channel(payload)

    # ─── Fast-path: clear purchase intent ──────────────────────────
    purchase = detect_purchase(transcript)
    if purchase is not None and caller_phone:
        logger.info(
            "phone: fast-path purchase=%s caller=%s",
            purchase.label, caller_phone,
        )
        background_tasks.add_task(
            _process_purchase_async,
            services,
            purchase,
            caller_phone,
        )
        # Speak the confirmation while the background task runs.
        spoken = (
            f"Got it, texting you a link to pay for {purchase.label} now. "
            "Tap the link to complete your order."
        )
        return _voice_response(spoken, channel)

    # ─── Slow-path: questions, chit-chat, anything ambiguous ──────
    logger.info(
        "phone: slow-path transcript=%r caller=%s",
        transcript[:120] if transcript else "<empty>", caller_phone or "<unknown>",
    )
    answer = generate_reply(transcript, services, caller_phone=caller_phone)
    return _voice_response(answer, channel)


def _process_purchase_async(
    services: Services,
    purchase: Purchase,
    customer_phone: str,
) -> None:
    """Reserve the order, create the Stripe checkout, text the link.

    Runs in a FastAPI background task after the webhook has already
    responded to the caller. Errors are logged but never raised — a
    failed background task must not crash the worker.
    """

    from fruit_market.brain import tools  # noqa: PLC0415

    try:
        # 1. Resolve item by name (fuzzy lookup tolerates "banana"
        # vs "bananas" and aliases like "nana").
        resolved = tools.resolve_item(
            services, ResolveItemInput(query=purchase.item_name),
        )
        if resolved is None:
            logger.warning(
                "phone bg: no catalog item for %r — texting apology",
                purchase.item_name,
            )
            _try_apology_sms(
                customer_phone,
                f"Sorry, we don't have {purchase.item_name} today.",
            )
            return

        # 2. Reserve stock atomically.
        reserved = tools.reserve_order(
            services,
            ReserveOrderInput(
                item_id=resolved.item_id,
                qty=purchase.qty,
                customer_phone=customer_phone,
            ),
        )
        logger.info(
            "phone bg: reserved order=%s qty=%d item=%s",
            reserved.order_id, purchase.qty, resolved.name,
        )

        # 3. Create the Stripe checkout session. ``create_checkout``
        # already deterministically texts the link via
        # ``_try_text_checkout_link`` (gated on AGENTPHONE_SEND_MODE
        # = live). The SMS is the headline deliverable here.
        checkout = tools.create_checkout(
            services, CreateCheckoutInput(order_id=reserved.order_id),
        )
        logger.info(
            "phone bg: checkout %s url=%s",
            reserved.order_id, checkout.checkout_url,
        )
    except Exception:  # noqa: BLE001
        # Don't crash the worker; surface a polite SMS so the
        # customer knows something went wrong.
        logger.exception(
            "phone bg: purchase pipeline FAILED for %s qty=%d",
            purchase.item_name, purchase.qty,
        )
        _try_apology_sms(
            customer_phone,
            "Sorry, something went wrong on our end. "
            "Please try again or call back.",
        )


def _try_apology_sms(to_phone: str, body: str) -> None:
    """Best-effort SMS apology; silent on AgentPhone failure."""

    import os  # noqa: PLC0415

    if os.environ.get("AGENTPHONE_SEND_MODE", "").strip().lower() != "live":
        logger.info("apology SMS would say: %s", body)
        return
    try:
        agentphone.send_sms(to_phone, body)
    except Exception:  # noqa: BLE001
        logger.exception("apology SMS failed to %s", to_phone)


def _voice_response(text: str, channel: str) -> dict[str, str]:
    """Shape the webhook response to whatever AgentPhone expects.

    Voice channel returns just ``text`` (AgentPhone speaks it via
    its own TTS). SMS / other channels get a wrapped envelope.
    """

    if channel == "voice":
        return {"text": text}
    return {"status": "ok", "text": text}


def _payload_channel(payload: dict[str, Any]) -> str:
    channel = payload.get("channel")
    return channel if isinstance(channel, str) else ""
