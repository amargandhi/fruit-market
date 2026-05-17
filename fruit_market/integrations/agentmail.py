"""AgentMail senders for receipts and operator approvals."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

import httpx

from fruit_market.integrations._http import (
    DEFAULT_TIMEOUT,
    auth_headers,
    json_object,
    required_env,
)

if TYPE_CHECKING:
    from fruit_market.restock.projection import RestockRecord
    from fruit_market.services.protocols import Order


def send_receipt(to_email: str, order: Order) -> str:
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
    payload: dict[str, Any] = {
        "to": to_email,
        "subject": subject,
        "text": text,
        "html": html_body,
        "labels": ["fruit-market", "receipt"],
    }
    return _send(payload)


def send_restock_approval(to_email: str, proposal: RestockRecord) -> str:
    """Email the operator a locked supplier basket before Pico approval."""

    total = _money(proposal.amount_cents)
    unit = _money(proposal.unit_price_cents)
    eta = (
        f"{proposal.eta_minutes} minutes"
        if proposal.eta_minutes > 0
        else "supplier estimate pending"
    )
    basket_line = (
        f"\nSupplier basket: {proposal.basket_url}\n" if proposal.basket_url else ""
    )
    subject = f"Approve {proposal.item_name} restock: {total}"
    text = (
        "Fruit Market has staged a supplier restock basket.\n\n"
        f"Proposal: {proposal.proposal_id}\n"
        f"Supplier: {proposal.supplier_name}\n"
        f"Item: {proposal.item_name}\n"
        f"Quantity: {proposal.qty}\n"
        f"Unit cost: {unit}\n"
        f"Total: {total}\n"
        f"Delivery ETA: {eta}\n"
        f"{basket_line}\n"
        "Payment is still locked. Press the Pico restock key to approve Sponge payment."
    )
    basket_html = (
        f'<p><a href="{html.escape(proposal.basket_url)}">Open supplier basket</a></p>'
        if proposal.basket_url
        else ""
    )
    html_body = (
        f"<h1>Approve {html.escape(proposal.item_name)} restock</h1>"
        "<p>Fruit Market has staged a supplier basket. Payment is still locked.</p>"
        "<dl>"
        f"<dt>Proposal</dt><dd>{html.escape(proposal.proposal_id)}</dd>"
        f"<dt>Supplier</dt><dd>{html.escape(proposal.supplier_name)}</dd>"
        f"<dt>Item</dt><dd>{html.escape(proposal.item_name)}</dd>"
        f"<dt>Quantity</dt><dd>{proposal.qty}</dd>"
        f"<dt>Unit cost</dt><dd>{unit}</dd>"
        f"<dt>Total</dt><dd>{total}</dd>"
        f"<dt>Delivery ETA</dt><dd>{html.escape(eta)}</dd>"
        "</dl>"
        f"{basket_html}"
        "<p><strong>Approval:</strong> press the Pico restock key to approve Sponge payment.</p>"
    )
    payload: dict[str, Any] = {
        "to": to_email,
        "subject": subject,
        "text": text,
        "html": html_body,
        "labels": ["fruit-market", "restock", "approval"],
    }
    return _send(payload)


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _send(payload: dict[str, Any]) -> str:
    api_base = required_env("AGENTMAIL_API_BASE").rstrip("/")
    api_key = required_env("AGENTMAIL_API_KEY")
    inbox = required_env("AGENTMAIL_ADDRESS")
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
