"""Gemini-backed customer brain."""

from __future__ import annotations

import importlib
import os
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

ToolCallable = Callable[..., dict[str, object] | None]


def handle_agentphone_message(
    envelope: AgentPhoneWebhookEnvelope,
    raw_payload: dict[str, Any],
    services: Services,
) -> str:
    transcript = _message_text(envelope, raw_payload)
    if not transcript:
        return "Thanks for calling Fruit Market. How can I help?"
    return generate_reply(transcript, services)


def generate_reply(transcript: str, services: Services) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key and "REPLACE_ME" not in api_key:
        try:
            reply = _generate_with_gemini(transcript, services, api_key)
        except Exception:
            reply = ""
        if reply:
            return reply
    return _fallback_reply(services)


def _generate_with_gemini(transcript: str, services: Services, api_key: str) -> str:
    genai = importlib.import_module("google.genai")
    genai_types = importlib.import_module("google.genai.types")
    client = genai.Client(api_key=api_key)
    config = genai_types.GenerateContentConfig(
        system_instruction=prompts.system_prompt(services),
        tools=_tool_callables(services),
        temperature=0.2,
    )
    response = client.models.generate_content(
        # Gemini 3.1 Flash Lite is the right size for a tool-using
        # brain when the perception work is already done at the edge
        # (PaliGemma counts inventory; the phone agent just routes
        # intent → tool → response). Override via GEMINI_MODEL —
        # e.g. ``gemini-2.5-flash`` if a particular call needs richer
        # reasoning at the cost of ~3× latency.
        model=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        contents=transcript,
        config=config,
    )
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


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
