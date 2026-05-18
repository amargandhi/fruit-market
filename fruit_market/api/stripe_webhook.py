"""Stripe webhook route."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from fruit_market.api.routes import get_services
from fruit_market.integrations import stripe_checkout

if TYPE_CHECKING:
    from fruit_market.services.protocols import Order, Services

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/stripe")
async def handle_stripe_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    try:
        event = stripe_checkout.verify_webhook(request.headers, body)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid signature") from exc

    event_type = event["type"]

    obj = stripe_checkout.event_data_object(event)
    metadata = obj.get("metadata", {})
    order_id = metadata.get("order_id") if isinstance(metadata, dict) else None
    stripe_session_id = obj.get("id")
    if not isinstance(order_id, str) or not isinstance(stripe_session_id, str):
        return {"status": "ignored"}

    services = get_services(request)

    # Failure paths first — these used to be silently ignored, which
    # meant the operator never heard about a declined card or an
    # expired checkout session. Now we cancel the reservation (so
    # the held stock returns) and SMS the operator.
    failure_events = {
        "checkout.session.expired":     "expired",
        "checkout.session.async_payment_failed": "async_payment_failed",
        "payment_intent.payment_failed": "payment_failed",
        "charge.failed":                 "charge_failed",
    }
    if event_type in failure_events:
        _handle_failed_checkout(
            services,
            order_id,
            failure_events[event_type],
        )
        return {"status": "ok"}

    if event_type not in {"checkout.session.completed", "payment_intent.succeeded"}:
        return {"status": "ignored"}

    order_before = services.orders.get(order_id)
    if order_before is None:
        return {"status": "ignored"}
    if order_before.status == "paid":
        return {"status": "ok"}
    try:
        services.orders.mark_paid(order_id, stripe_session_id)
    except KeyError:
        return {"status": "ignored"}
    order = services.orders.get(order_id)
    if order is not None:
        _notify_after_payment(services, order, obj)
    return {"status": "ok"}


def _handle_failed_checkout(
    services: Services,
    order_id: str,
    failure_reason: str,
) -> None:
    """Release held stock + tell the operator a payment failed."""

    order = services.orders.get(order_id)
    if order is None:
        return
    if order.status != "reserved":
        # Already paid, packed, or cancelled — nothing to do here.
        return
    item = services.catalog.get_item(order.item_id)
    item_name = item.name if item is not None else order.item_id

    try:
        services.orders.cancel(order_id, "customer_request")
    except Exception:  # noqa: BLE001
        logger.exception(
            "stripe %s but order cancel failed for %s", failure_reason, order_id,
        )
        return

    logger.warning(
        "stripe %s — released %d %s reservation %s",
        failure_reason, order.qty, item_name, order_id,
    )

    operator_phone = os.environ.get("OPERATOR_PHONE", "").strip()
    send_mode = os.environ.get("AGENTPHONE_SEND_MODE", "").strip().lower()
    if operator_phone and send_mode == "live":
        try:
            from fruit_market.integrations.agentphone import send_sms  # noqa: PLC0415

            send_sms(
                operator_phone,
                (
                    f"Fruit Market: Stripe {failure_reason} for order {order_id} "
                    f"({_quantity_label(order.qty, item_name)}). Reservation released."
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to text operator about Stripe %s for %s",
                failure_reason, order_id,
            )


def _notify_after_payment(
    services: Services,
    order: Order,
    stripe_object: dict[str, Any],
) -> None:
    item = services.catalog.get_item(order.item_id)
    item_name = item.name if item is not None else order.item_id
    buyer_email = _customer_email(stripe_object)
    if buyer_email:
        _send_receipt(buyer_email, order, item_name)
    _notify_operator(order, item_name)


def _customer_email(stripe_object: dict[str, Any]) -> str | None:
    customer_details = stripe_object.get("customer_details")
    if isinstance(customer_details, dict):
        email = customer_details.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    for key in ("customer_email", "receipt_email"):
        value = stripe_object.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _send_receipt(to_email: str, order: Order, item_name: str) -> None:
    if os.environ.get("AGENTMAIL_ENABLED", "0").strip() != "1":
        return
    try:
        from fruit_market.integrations.agentmail import send_receipt  # noqa: PLC0415

        send_receipt(to_email, order, item_name=item_name)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send receipt email for order %s", order.id)


def _notify_operator(order: Order, item_name: str) -> None:
    operator_email = os.environ.get("OPERATOR_EMAIL", "").strip()
    if (
        operator_email
        and os.environ.get("AGENTMAIL_ENABLED", "0").strip() == "1"
    ):
        try:
            from fruit_market.integrations.agentmail import (  # noqa: PLC0415
                send_operator_order_notification,
            )

            send_operator_order_notification(operator_email, order, item_name)
            return
        except Exception:  # noqa: BLE001
            logger.exception("failed to email operator for order %s", order.id)

    operator_phone = os.environ.get("OPERATOR_PHONE", "").strip()
    send_mode = os.environ.get("AGENTPHONE_SEND_MODE", "").strip().lower()
    if operator_phone and send_mode == "live":
        try:
            from fruit_market.integrations.agentphone import send_sms  # noqa: PLC0415

            send_sms(
                operator_phone,
                (
                    f"Paid Fruit Market order {order.id}: set aside "
                    f"{_quantity_label(order.qty, item_name)} for "
                    f"{order.customer_phone}."
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to text operator for order %s", order.id)


def _quantity_label(qty: int, item_name: str) -> str:
    if qty == 1 or item_name.endswith("s"):
        return f"{qty} {item_name}"
    return f"{qty} {item_name}s"
