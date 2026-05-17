"""Phase-1 skeleton tests.

Goal of this file: prove that the contracts compile and that the
in-memory stub bundle satisfies every Protocol the two parallel
tracks will depend on. **No real business logic is tested here.**

If a test in this file fails, the parallel tracks can't safely
start — Phase 1 is broken.
"""

from __future__ import annotations

import inspect

import pytest

from fruit_market.api import schemas as api_schemas
from fruit_market.brain import restock_agent_specs, tool_specs
from fruit_market.integrations import SpongeMCPClient, SupplierMCPClient
from fruit_market.services import (
    CatalogService,
    InventoryService,
    OrdersService,
    PricingService,
    Services,
    TeachService,
    make_services,
)
from fruit_market.state.events import (
    ActiveItemSet,
    CountSet,
    Event,  # noqa: F401  — imported to confirm the union resolves
    ItemTaught,
    OrderCancelled,
    OrderPacked,
    OrderPaid,
    OrderReserved,
    RestockApproved,
    RestockOperatorEmailed,
    RestockOperatorEmailFailed,
    RestockOrdered,
    RestockPaymentFailed,
    RestockPaymentStarted,
    RestockProposed,
    RestockReceived,
    RestockRejected,
    RestockSpongePlanSubmitted,
    StockLow,
)

# ─── events: every type instantiates and round-trips ────────────────


def test_every_event_type_instantiates() -> None:
    samples = [
        ItemTaught(item_id="item_1", name="banana", price_cents=100, initial_count=6),
        ActiveItemSet(item_id="item_1"),
        CountSet(item_id="item_1", count=4, source="model", confidence=0.92),
        StockLow(item_id="item_1", threshold=2, current_count=1),
        OrderReserved(
            order_id="ord_1",
            item_id="item_1",
            qty=2,
            total_cents=200,
            customer_phone="+15551234567",
        ),
        OrderPaid(order_id="ord_1", stripe_session_id="cs_test_123"),
        OrderPacked(order_id="ord_1"),
        OrderCancelled(order_id="ord_1", reason="timeout"),
        RestockProposed(
            proposal_id="restock_1",
            stock_low_offset=1,
            item_id="item_1",
            item_name="banana",
            qty=50,
            supplier_id="sup_1",
            supplier_name="Supplier",
            unit_price_cents=25,
            amount_cents=1250,
            gateway_url="https://supplier.x402.test/orders",
            payload_hash="a" * 64,
            idempotency_key="item_1:1",
            expires_at_iso="2026-05-17T15:00:00Z",
            eta_minutes=30,
            basket_url="https://supplier.test/basket?proposal_id=restock_1",
        ),
        RestockSpongePlanSubmitted(
            proposal_id="restock_1",
            sponge_plan_id="plan_1",
        ),
        RestockOperatorEmailed(
            proposal_id="restock_1",
            to_email="operator@example.com",
            message_id="mail_1",
        ),
        RestockOperatorEmailFailed(
            proposal_id="restock_1",
            to_email="operator@example.com",
            reason="mail down",
        ),
        RestockApproved(
            proposal_id="restock_1",
            payload_hash="a" * 64,
            amount_cents=1250,
        ),
        RestockRejected(proposal_id="restock_1", reason="operator"),
        RestockPaymentStarted(
            proposal_id="restock_1",
            amount_cents=1250,
            sponge_plan_id="plan_1",
        ),
        RestockPaymentFailed(
            proposal_id="restock_1",
            stage="payment",
            reason="declined",
        ),
        RestockOrdered(
            item_id="item_1",
            qty=50,
            supplier_id="sup_1",
            sponge_payment_id="pay_1",
            eta_iso="2026-05-17T15:00:00Z",
        ),
        RestockReceived(
            proposal_id="restock_1",
            item_id="item_1",
            qty=50,
        ),
    ]
    for ev in samples:
        # Round-trip through Pydantic JSON to confirm serialization stability.
        payload = ev.model_dump_json()
        cls = type(ev)
        again = cls.model_validate_json(payload)
        assert again == ev


def test_money_fields_reject_negative_values() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ItemTaught(item_id="x", name="x", price_cents=-1, initial_count=0)
    with pytest.raises(ValidationError):
        OrderReserved(
            order_id="x",
            item_id="x",
            qty=1,
            total_cents=-1,
            customer_phone="+15551234567",
        )


# ─── services: stubs satisfy every Protocol ────────────────────────


def test_make_services_returns_services_bundle() -> None:
    services = make_services()
    assert isinstance(services, Services)
    # Each member is duck-checked against its Protocol via runtime_checkable.
    assert isinstance(services.catalog, CatalogService)
    assert isinstance(services.inventory, InventoryService)
    assert isinstance(services.pricing, PricingService)
    assert isinstance(services.orders, OrdersService)
    assert isinstance(services.teach, TeachService)


def test_stub_services_can_run_a_teach_quote_reserve_loop() -> None:
    services = make_services()
    proposal = services.teach.propose("These are bananas, $1.00, 6 of them")
    item = services.teach.confirm(proposal.id)

    # Pricing
    assert services.pricing.quote(item.id, 3) == 3 * item.price_cents

    # Reservation reduces inventory
    order = services.orders.reserve(item.id, 2, "+15551234567")
    assert order.status == "reserved"
    assert services.inventory.get_physical_count(item.id) == item.physical_count - 2

    # Mark paid is idempotent
    services.orders.mark_paid(order.id, "cs_test_123")
    services.orders.mark_paid(order.id, "cs_test_123")
    assert services.orders.get(order.id) is not None
    assert services.orders.get(order.id).status == "paid"  # type: ignore[union-attr]


# ─── tool specs: every input/output is a Pydantic model ────────────


def _pydantic_model_names(module: object) -> list[str]:
    return [
        name
        for name, obj in inspect.getmembers(module, inspect.isclass)
        if hasattr(obj, "model_fields") and not name.startswith("_")
    ]


def test_tool_specs_module_has_paired_input_output() -> None:
    names = _pydantic_model_names(tool_specs)
    assert "ResolveItemInput" in names
    assert "ResolveItemOutput" in names
    assert "QuoteOrderInput" in names
    assert "QuoteOrderOutput" in names
    assert "ReserveOrderInput" in names
    assert "ReserveOrderOutput" in names
    assert "CreateCheckoutInput" in names
    assert "CreateCheckoutOutput" in names
    assert "GetInventoryInput" in names
    assert "GetInventoryOutput" in names
    assert "GetVenueInfoInput" in names
    assert "GetVenueInfoOutput" in names
    assert "ListItemsInput" in names
    assert "ListItemsOutput" in names
    assert "SendSmsInput" in names
    assert "SendSmsOutput" in names
    assert "SendImessageInput" in names
    assert "SendImessageOutput" in names


def test_restock_agent_specs_module_has_three_tools() -> None:
    names = _pydantic_model_names(restock_agent_specs)
    for verb in ("FindSupplier", "SpongeCharge", "PlaceSupplierOrder"):
        assert f"{verb}Input" in names, verb
        assert f"{verb}Output" in names, verb


# ─── api schemas: webhooks parse a representative envelope ─────────


def test_agentphone_webhook_envelope_parses_minimal_payload() -> None:
    env = api_schemas.AgentPhoneWebhookEnvelope.model_validate(
        {
            "type": "call.started",
            "call": {
                "call_id": "call_abc",
                "from": "+15551234567",
                "to": "+15312284935",
            },
        }
    )
    assert env.type == "call.started"
    assert env.call is not None
    assert env.call.from_phone == "+15551234567"


def test_stripe_webhook_envelope_parses_minimal_payload() -> None:
    env = api_schemas.StripeWebhookEnvelope.model_validate(
        {
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_123"}},
        }
    )
    assert env.id == "evt_test_123"
    assert env.type == "checkout.session.completed"


# ─── integrations: MCP Protocols import without error ──────────────


def test_mcp_protocol_classes_are_importable() -> None:
    # Pure import sanity. Real clients land later.
    assert SpongeMCPClient is not None
    assert SupplierMCPClient is not None
