"""Gemini-backed customer brain."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fruit_market.api.schemas import AgentPhoneWebhookEnvelope
    from fruit_market.services.protocols import Services


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
