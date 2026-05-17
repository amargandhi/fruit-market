"""Prompts for the customer-facing phone brain."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fruit_market.services.protocols import Services


def system_prompt(services: Services, caller_phone: str | None = None) -> str:
    venue = services.venue
    caller_context = (
        f" Current caller phone: {caller_phone}. Use this for reservations and "
        "follow-up messages unless the customer gives a different phone number."
        if caller_phone
        else " If a customer wants to reserve or pay, ask for their phone number first."
    )
    return (
        "You are the Fruit Market phone agent for a single-stall operator. "
        "Use tools for catalog, inventory, pricing, orders, checkout, and messaging. "
        "Never invent inventory or prices. Money is integer cents in tools, but speak "
        "to customers in dollars. Keep replies short enough for phone or SMS. "
        f"Venue: {venue.name}. Location: {venue.location}. "
        f"Hours today: {venue.hours_today}. Pickup: {venue.pickup}."
        f"{caller_context}"
    )
