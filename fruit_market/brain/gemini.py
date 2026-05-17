"""Gemini-backed customer brain."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

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
GeminiThinkingLevel = Literal["minimal", "low", "medium", "high"]
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMINI_THINKING_LEVEL: GeminiThinkingLevel = "low"
GEMINI_THINKING_LEVELS: tuple[GeminiThinkingLevel, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
)


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
    model = _gemini_model()
    config_kwargs: dict[str, object] = {
        "system_instruction": prompts.system_prompt(services),
        "tools": _tool_callables(services),
    }
    if model.startswith("gemini-3"):
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_level=_gemini_thinking_level()
        )
    config = genai_types.GenerateContentConfig(**config_kwargs)
    response = client.models.generate_content(
        model=model,
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
        """Find the catalog item that matches a customer's wording.

        Args:
            query: Customer phrase, nickname, plural, or typo for an item.

        Returns:
            Matching item details, or None when no item exists.
        """

        result = tools.resolve_item(services, ResolveItemInput(query=query))
        return None if result is None else result.model_dump(mode="json")

    def list_items() -> dict[str, object]:
        """List all currently available catalog items.

        Returns:
            Item names, ids, prices in cents, and available counts.
        """

        return tools.list_items(services, ListItemsInput()).model_dump(mode="json")

    def quote_order(item_id: str, qty: int) -> dict[str, object]:
        """Quote the total price for a quantity of one catalog item.

        Args:
            item_id: Catalog item id returned by resolve_item or list_items.
            qty: Positive quantity the customer wants.

        Returns:
            Item id, quantity, unit amount in cents, and total in cents.
        """

        return tools.quote_order(
            services,
            QuoteOrderInput(item_id=item_id, qty=qty),
        ).model_dump(mode="json")

    def reserve_order(item_id: str, qty: int, customer_phone: str) -> dict[str, object]:
        """Reserve inventory for a customer before payment.

        Args:
            item_id: Catalog item id returned by resolve_item or list_items.
            qty: Positive quantity to reserve.
            customer_phone: Customer phone number in E.164 format.

        Returns:
            Reserved order id and total amount in cents.
        """

        return tools.reserve_order(
            services,
            ReserveOrderInput(item_id=item_id, qty=qty, customer_phone=customer_phone),
        ).model_dump(mode="json")

    def create_checkout(order_id: str) -> dict[str, object]:
        """Create a Stripe checkout link for a reserved order.

        Args:
            order_id: Order id returned by reserve_order.

        Returns:
            Order id and customer-facing checkout URL.
        """

        return tools.create_checkout(
            services,
            CreateCheckoutInput(order_id=order_id),
        ).model_dump(mode="json")

    def get_inventory(item_id: str) -> dict[str, object]:
        """Read physical inventory for one catalog item.

        Args:
            item_id: Catalog item id returned by resolve_item or list_items.

        Returns:
            Physical count and whether the item is below its reorder threshold.
        """

        return tools.get_inventory(
            services,
            GetInventoryInput(item_id=item_id),
        ).model_dump(mode="json")

    def get_venue_info() -> dict[str, object]:
        """Read venue details customers can ask about.

        Returns:
            Venue name, location, hours, and pickup instructions.
        """

        return tools.get_venue_info(services, GetVenueInfoInput()).model_dump(mode="json")

    def send_sms(to_phone: str, body: str) -> dict[str, object]:
        """Send an SMS follow-up to the customer.

        Args:
            to_phone: Destination phone number in E.164 format.
            body: Message body to send.

        Returns:
            Provider message id.
        """

        return tools.send_sms(
            services,
            SendSmsInput(to_phone=to_phone, body=body),
        ).model_dump(mode="json")

    def send_imessage(to_phone: str, body: str) -> dict[str, object]:
        """Send an iMessage follow-up to the customer.

        Args:
            to_phone: Destination phone number in E.164 format.
            body: Message body to send.

        Returns:
            Provider message id.
        """

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


def _gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL


def _gemini_thinking_level() -> GeminiThinkingLevel:
    level = os.environ.get("GEMINI_THINKING_LEVEL", DEFAULT_GEMINI_THINKING_LEVEL).strip()
    if level in GEMINI_THINKING_LEVELS:
        return level
    return DEFAULT_GEMINI_THINKING_LEVEL
