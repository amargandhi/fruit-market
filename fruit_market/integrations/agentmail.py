"""AgentMail receipt sender."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import httpx

from fruit_market.integrations._http import (
    DEFAULT_TIMEOUT,
    auth_headers,
    json_object,
    required_env,
)

if TYPE_CHECKING:
    from fruit_market.services.protocols import Order


def send_receipt(to_email: str, order: Order) -> str:
    api_base = required_env("AGENTMAIL_API_BASE").rstrip("/")
    api_key = required_env("AGENTMAIL_API_KEY")
    inbox = required_env("AGENTMAIL_ADDRESS")
    subject = f"Fruit Market receipt for order {order.id}"
    text = (
        "Thanks for your Fruit Market order.\n\n"
        f"Order: {order.id}\n"
        f"Item: {order.item_id}\n"
        f"Quantity: {order.qty}\n"
        f"Total: ${order.total_cents / 100:.2f}\n"
        f"Status: {order.status}\n"
    )
    html_body = (
        "<h1>Fruit Market receipt</h1>"
        f"<p>Order <strong>{html.escape(order.id)}</strong> is {html.escape(order.status)}.</p>"
        "<dl>"
        f"<dt>Item</dt><dd>{html.escape(order.item_id)}</dd>"
        f"<dt>Quantity</dt><dd>{order.qty}</dd>"
        f"<dt>Total</dt><dd>${order.total_cents / 100:.2f}</dd>"
        "</dl>"
    )
    payload = {
        "to": to_email,
        "subject": subject,
        "text": text,
        "html": html_body,
        "labels": ["fruit-market", "receipt"],
    }
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        response = client.post(
            f"{api_base}/inboxes/{inbox}/messages/send",
            headers=auth_headers(api_key),
            json=payload,
        )
    response.raise_for_status()
    data = json_object(response)
    message_id = data.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("AgentMail response did not include message_id")
    return message_id
