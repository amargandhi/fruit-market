"""Stripe Checkout integration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import stripe

from fruit_market.integrations._http import required_env

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


def create(
    item_name: str,
    qty: int,
    unit_amount_cents: int,
    success_url: str,
    cancel_url: str,
    customer_phone: str,
) -> stripe.checkout.Session:
    if qty <= 0:
        raise ValueError("qty must be > 0")
    if unit_amount_cents <= 0:
        raise ValueError("unit_amount_cents must be > 0")
    stripe.api_key = required_env("STRIPE_SECRET_KEY")
    session = stripe.checkout.Session.create(
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        customer_creation="if_required",
        phone_number_collection={"enabled": True},
        metadata={"customer_phone": customer_phone},
        line_items=[
            {
                "quantity": qty,
                "price_data": {
                    "currency": os.environ.get("STRIPE_CURRENCY", "usd"),
                    "unit_amount": unit_amount_cents,
                    "product_data": {"name": item_name},
                },
            }
        ],
    )
    return session


def verify_webhook(headers: Mapping[str, str], body: bytes) -> stripe.Event:
    signature = _header(headers, "Stripe-Signature")
    if not signature:
        raise ValueError("missing Stripe-Signature")
    construct_event = cast("Callable[..., stripe.Event]", stripe.Webhook.construct_event)
    return construct_event(
        payload=body,
        sig_header=signature,
        secret=required_env("STRIPE_WEBHOOK_SECRET"),
    )


def event_data_object(event: stripe.Event) -> dict[str, Any]:
    to_dict = cast(
        "Callable[[], Mapping[str, object]]",
        event.to_dict,
    )
    event_payload = to_dict()
    data = event_payload.get("data", {})
    if not isinstance(data, dict):
        return {}
    obj = data.get("object", {})
    return obj if isinstance(obj, dict) else {}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
