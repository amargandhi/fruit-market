"""Tool functions exposed to the customer-facing Gemini brain."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fruit_market.brain.product_knowledge import knowledge_for
from fruit_market.brain.tool_specs import (
    CreateCheckoutInput,
    CreateCheckoutOutput,
    GetInventoryInput,
    GetInventoryOutput,
    GetVenueInfoInput,
    GetVenueInfoOutput,
    ItemSummary,
    ListItemsInput,
    ListItemsOutput,
    QuoteOrderInput,
    QuoteOrderOutput,
    ReserveOrderInput,
    ReserveOrderOutput,
    ResolveItemInput,
    ResolveItemOutput,
    SendImessageInput,
    SendImessageOutput,
    SendSmsInput,
    SendSmsOutput,
)
from fruit_market.integrations import agentphone, stripe_checkout

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fruit_market.services.protocols import Services


def resolve_item(services: Services, payload: ResolveItemInput) -> ResolveItemOutput | None:
    item = services.catalog.resolve_by_name(payload.query)
    if item is None:
        return None
    return ResolveItemOutput(
        item_id=item.id,
        name=item.name,
        price_cents=item.price_cents,
        available_count=item.physical_count,
        **_product_fields(item.name),
    )


def list_items(services: Services, payload: ListItemsInput) -> ListItemsOutput:
    _ = payload
    return ListItemsOutput(
        items=[
            ItemSummary(
                item_id=item.id,
                name=item.name,
                price_cents=item.price_cents,
                available_count=item.physical_count,
                **_product_fields(item.name),
            )
            for item in services.catalog.list_items()
        ]
    )


def quote_order(services: Services, payload: QuoteOrderInput) -> QuoteOrderOutput:
    item = services.catalog.get_item(payload.item_id)
    if item is None:
        raise ValueError(f"unknown item: {payload.item_id}")
    total = services.pricing.quote(payload.item_id, payload.qty)
    return QuoteOrderOutput(
        item_id=payload.item_id,
        qty=payload.qty,
        unit_amount_cents=item.price_cents,
        total_cents=total,
    )


def reserve_order(services: Services, payload: ReserveOrderInput) -> ReserveOrderOutput:
    order = services.orders.reserve(payload.item_id, payload.qty, payload.customer_phone)
    return ReserveOrderOutput(order_id=order.id, total_cents=order.total_cents)


def create_checkout(services: Services, payload: CreateCheckoutInput) -> CreateCheckoutOutput:
    """Build a Stripe Checkout link AND text it to the customer.

    The SMS send is **deterministic**: every successful checkout
    creation also calls AgentPhone to text the link to
    ``order.customer_phone``. This removes the model's freedom to
    "forget" the send_sms step on step 13 of the demo chain. If
    AgentPhone send mode is not "live" the SMS attempt no-ops
    cleanly (no exception) so unit / mock environments stay quiet.
    """

    order = services.orders.get(payload.order_id)
    if order is None:
        raise ValueError(f"unknown order: {payload.order_id}")
    item = services.catalog.get_item(order.item_id)
    if item is None:
        raise ValueError(f"unknown item: {order.item_id}")
    session = stripe_checkout.create(
        item_name=item.name,
        qty=order.qty,
        unit_amount_cents=order.total_cents // order.qty,
        success_url=_checkout_url("STRIPE_SUCCESS_URL"),
        cancel_url=_checkout_url("STRIPE_CANCEL_URL"),
        customer_phone=order.customer_phone,
        metadata={"order_id": order.id},
    )
    checkout_url = stripe_checkout.session_url(session)

    # Belt-and-braces customer SMS. The Gemini system prompt also
    # asks the model to call send_sms with this URL — but a model
    # hallucination at the wrong moment can silently drop step 13
    # and the customer never gets the link. Sending here as well
    # makes the link delivery the same shape as the receipt email:
    # something that just happens after a successful step.
    _try_text_checkout_link(order.customer_phone, item.name, order.qty, checkout_url)

    return CreateCheckoutOutput(
        order_id=order.id,
        checkout_url=checkout_url,
    )


def _try_text_checkout_link(
    to_phone: str,
    item_name: str,
    qty: int,
    checkout_url: str,
) -> None:
    """SMS the Stripe checkout link to the customer.

    SMS is the only delivery channel — no iMessage fallback. On
    failure we log loudly (with the upstream error body, the
    customer phone, and the checkout URL) so the operator can
    deliver the link manually if needed, but we never raise:
    the checkout session has already been created and we don't
    want to undo it because of a delivery glitch.
    """

    send_mode = os.environ.get("AGENTPHONE_SEND_MODE", "").strip().lower()
    if send_mode != "live":
        logger.info(
            "skip customer SMS (AGENTPHONE_SEND_MODE=%r); link=%s",
            send_mode or "<unset>", checkout_url,
        )
        return
    if not to_phone or not to_phone.startswith("+"):
        logger.warning(
            "skip customer SMS — invalid customer_phone %r", to_phone,
        )
        return

    qty_label = f"{qty} {item_name}" if qty == 1 else f"{qty} {item_name}s"
    body = (
        f"Fruit Market: tap to pay for your {qty_label} — {checkout_url}"
    )

    try:
        agentphone.send_sms(to_phone, body)
        logger.info("texted Stripe link to %s via SMS", to_phone)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "SMS delivery FAILED to %s — checkout link NOT delivered. "
            "Upstream: %s. Link: %s",
            to_phone, str(exc)[:250], checkout_url,
        )


def get_inventory(services: Services, payload: GetInventoryInput) -> GetInventoryOutput:
    item = services.catalog.get_item(payload.item_id)
    if item is None:
        raise ValueError(f"unknown item: {payload.item_id}")
    count = services.inventory.get_physical_count(payload.item_id)
    return GetInventoryOutput(
        item_id=payload.item_id,
        physical_count=count,
        is_low=count <= item.reorder_threshold,
    )


def get_venue_info(services: Services, payload: GetVenueInfoInput) -> GetVenueInfoOutput:
    _ = payload
    venue = services.venue
    return GetVenueInfoOutput(
        name=venue.name,
        location=venue.location,
        hours_today=venue.hours_today,
        pickup=venue.pickup,
    )


def send_sms(services: Services, payload: SendSmsInput) -> SendSmsOutput:
    _ = services
    message_id = agentphone.send_sms(payload.to_phone, payload.body)
    return SendSmsOutput(message_id=message_id)


def send_imessage(services: Services, payload: SendImessageInput) -> SendImessageOutput:
    _ = services
    message_id = agentphone.send_imessage(payload.to_phone, payload.body)
    return SendImessageOutput(message_id=message_id)


def _checkout_url(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return "http://localhost:8000/"


def _product_fields(name: str) -> dict[str, str]:
    knowledge = knowledge_for(name)
    if knowledge is None:
        return {}
    return {
        "variety": knowledge.variety,
        "short_description": knowledge.short_description,
        "tasting_notes": knowledge.tasting_notes,
        "best_for": knowledge.best_for,
        "ripeness_cues": knowledge.ripeness_cues,
        "sales_tip": knowledge.sales_tip,
        "pairings": knowledge.pairings,
    }
