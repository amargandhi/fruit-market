"""Tool functions exposed to the customer-facing Gemini brain."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

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
    return CreateCheckoutOutput(
        order_id=order.id,
        checkout_url=stripe_checkout.session_url(session),
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
