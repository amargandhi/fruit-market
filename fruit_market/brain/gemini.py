"""Gemini-backed customer brain."""

from __future__ import annotations

import importlib
import logging
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from fruit_market.brain import prompts, tools
from fruit_market.brain.tool_specs import (
    CreateCheckoutInput,
    GetInventoryInput,
    GetVenueInfoInput,
    ListItemsInput,
    QuoteOrderInput,
    ReserveOrderInput,
    ResolveItemInput,
    SendImessageInput,
    SendSmsInput,
)

if TYPE_CHECKING:
    from fruit_market.api.schemas import AgentPhoneWebhookEnvelope
    from fruit_market.services.protocols import Services

logger = logging.getLogger("fm.brain.gemini")

ToolCallable = Callable[..., dict[str, object] | None]


def handle_agentphone_message(
    envelope: AgentPhoneWebhookEnvelope,
    raw_payload: dict[str, Any],
    services: Services,
) -> str:
    transcript = _message_text(envelope, raw_payload)
    if not transcript:
        return "Thanks for calling Fruit Market. How can I help?"
    caller_phone = _caller_phone(envelope, raw_payload)
    return generate_reply(transcript, services, caller_phone=caller_phone)


def generate_reply(
    transcript: str,
    services: Services,
    *,
    caller_phone: str | None = None,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or "REPLACE_ME" in api_key:
        logger.warning("GEMINI_API_KEY not set — returning canned fallback")
        return _fallback_reply(services)

    logger.info(
        "brain: transcript=%r caller=%s",
        transcript[:120], caller_phone or "<unknown>",
    )
    started = time.monotonic()
    try:
        reply = _generate_with_gemini(
            transcript,
            services,
            api_key,
            caller_phone=caller_phone,
        )
    except Exception:
        # FULL traceback to the log — previously this was silently
        # swallowed and the operator saw the canned fallback with
        # no idea why.
        logger.exception("brain: gemini call FAILED — falling back")
        return _fallback_reply(services)

    elapsed = time.monotonic() - started
    if not reply:
        logger.warning(
            "brain: gemini returned empty reply (%.2fs) — falling back",
            elapsed,
        )
        return _fallback_reply(services)

    logger.info("brain: reply=%r (%.2fs)", reply[:200], elapsed)
    return reply


def _generate_with_gemini(
    transcript: str,
    services: Services,
    api_key: str,
    *,
    caller_phone: str | None = None,
) -> str:
    genai = importlib.import_module("google.genai")
    genai_types = importlib.import_module("google.genai.types")
    client = genai.Client(api_key=api_key)
    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    config = genai_types.GenerateContentConfig(
        system_instruction=prompts.system_prompt(services),
        tools=_tool_callables(services),
        temperature=0.2,
    )
    logger.info("brain: calling %s with %d tools", model, 9)
    response = client.models.generate_content(
        model=model,
        contents=_model_contents(transcript, caller_phone),
        config=config,
    )
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


def _model_contents(transcript: str, caller_phone: str | None) -> str:
    if caller_phone:
        return f"Caller phone: {caller_phone}\nTranscript: {transcript}"
    return transcript


def _fallback_reply(services: Services) -> str:
    active = services.catalog.get_active_item()
    if active is None:
        venue = services.venue
        return (
            f"Thanks for calling {venue.name}. We are still setting up today's catalog. "
            "Please check with the counter."
        )
    return (
        f"We have {active.physical_count} {active.name} available at "
        f"${active.price_cents / 100:.2f} each. Pickup is {services.venue.pickup}"
    )


def _tool_callables(services: Services) -> list[ToolCallable]:
    def resolve_item(query: str) -> dict[str, object] | None:
        result = tools.resolve_item(services, ResolveItemInput(query=query))
        return None if result is None else result.model_dump(mode="json")

    def list_items() -> dict[str, object]:
        return tools.list_items(services, ListItemsInput()).model_dump(mode="json")

    def quote_order(item_id: str, qty: int) -> dict[str, object]:
        return tools.quote_order(
            services,
            QuoteOrderInput(item_id=item_id, qty=qty),
        ).model_dump(mode="json")

    def reserve_order(item_id: str, qty: int, customer_phone: str) -> dict[str, object]:
        return tools.reserve_order(
            services,
            ReserveOrderInput(item_id=item_id, qty=qty, customer_phone=customer_phone),
        ).model_dump(mode="json")

    def create_checkout(order_id: str) -> dict[str, object]:
        return tools.create_checkout(
            services,
            CreateCheckoutInput(order_id=order_id),
        ).model_dump(mode="json")

    def get_inventory(item_id: str) -> dict[str, object]:
        return tools.get_inventory(
            services,
            GetInventoryInput(item_id=item_id),
        ).model_dump(mode="json")

    def get_venue_info() -> dict[str, object]:
        return tools.get_venue_info(services, GetVenueInfoInput()).model_dump(mode="json")

    def send_sms(to_phone: str, body: str) -> dict[str, object]:
        return tools.send_sms(
            services,
            SendSmsInput(to_phone=to_phone, body=body),
        ).model_dump(mode="json")

    def send_imessage(to_phone: str, body: str) -> dict[str, object]:
        return tools.send_imessage(
            services,
            SendImessageInput(to_phone=to_phone, body=body),
        ).model_dump(mode="json")

    return cast(
        "list[ToolCallable]",
        [
            resolve_item,
            list_items,
            quote_order,
            reserve_order,
            create_checkout,
            get_inventory,
            get_venue_info,
            send_sms,
            send_imessage,
        ],
    )


def _message_text(envelope: AgentPhoneWebhookEnvelope, raw_payload: dict[str, Any]) -> str:
    if envelope.transcript:
        return envelope.transcript
    if envelope.message:
        return envelope.message
    data = raw_payload.get("data", {})
    if isinstance(data, dict):
        message = data.get("message") or data.get("transcript")
        if isinstance(message, str):
            return message
    return ""


def _caller_phone(
    envelope: AgentPhoneWebhookEnvelope,
    raw_payload: dict[str, Any],
) -> str | None:
    if envelope.call is not None:
        return envelope.call.from_phone
    data = raw_payload.get("data", {})
    if isinstance(data, dict):
        value = data.get("from") or data.get("from_phone") or data.get("fromNumber")
        if isinstance(value, str) and value.startswith("+"):
            return value
    return None
