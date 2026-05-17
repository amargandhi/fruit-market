from __future__ import annotations

from fruit_market.brain import tools
from fruit_market.brain.tool_specs import (
    CreateCheckoutInput,
    GetInventoryInput,
    GetVenueInfoInput,
    ListItemsInput,
    QuoteOrderInput,
    ReserveOrderInput,
    ResolveItemInput,
    SendSmsInput,
)


def test_brain_tools_cover_catalog_quote_reserve_inventory(stub_services) -> None:  # type: ignore[no-untyped-def]
    proposal = stub_services.teach.propose("These are bananas, $1.00, 6 of them")
    item = stub_services.teach.confirm(proposal.id)

    resolved = tools.resolve_item(stub_services, ResolveItemInput(query="banana"))
    assert resolved is not None
    assert resolved.item_id == item.id

    listed = tools.list_items(stub_services, ListItemsInput())
    assert listed.items[0].name == "banana"

    quote = tools.quote_order(stub_services, QuoteOrderInput(item_id=item.id, qty=2))
    assert quote.total_cents == 200

    reserved = tools.reserve_order(
        stub_services,
        ReserveOrderInput(item_id=item.id, qty=2, customer_phone="+15551234567"),
    )
    assert reserved.total_cents == 200

    inventory = tools.get_inventory(stub_services, GetInventoryInput(item_id=item.id))
    assert inventory.physical_count == 4


def test_brain_tools_return_venue_info(stub_services) -> None:  # type: ignore[no-untyped-def]
    venue = tools.get_venue_info(stub_services, GetVenueInfoInput())

    assert venue.name == "Fruit Market"


def test_create_checkout_uses_order_metadata(monkeypatch, stub_services) -> None:  # type: ignore[no-untyped-def]
    proposal = stub_services.teach.propose("These are oranges, $2.50, 3 of them")
    item = stub_services.teach.confirm(proposal.id)
    order = stub_services.orders.reserve(item.id, 1, "+15551234567")
    seen: dict[str, object] = {}

    def fake_create(**kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return {"url": "https://checkout.test/session"}

    monkeypatch.setattr(tools.stripe_checkout, "create", fake_create)

    checkout = tools.create_checkout(stub_services, CreateCheckoutInput(order_id=order.id))

    assert checkout.checkout_url == "https://checkout.test/session"
    assert seen["metadata"] == {"order_id": order.id}
    assert seen["unit_amount_cents"] == 250


def test_send_sms_tool_uses_agentphone(monkeypatch, stub_services) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tools.agentphone, "send_sms", lambda to, body: f"msg:{to}:{body}")

    sent = tools.send_sms(
        stub_services,
        SendSmsInput(to_phone="+15551234567", body="ready"),
    )

    assert sent.message_id == "msg:+15551234567:ready"
