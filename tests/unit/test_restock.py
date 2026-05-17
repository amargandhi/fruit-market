from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from fruit_market.restock.coordinator import RestockCoordinator
from fruit_market.restock.paysponge import SpongePayment, SpongePlan
from fruit_market.restock.projection import RestockProjection
from fruit_market.restock.runtime import build_restock_runtime
from fruit_market.restock.settings import RestockSettings
from fruit_market.restock.supplier import RestockQuote, StaticSupplierClient
from fruit_market.services.factory import make_real_services
from fruit_market.state.events import (
    RestockApproved,
    RestockOperatorEmailed,
    RestockOperatorEmailFailed,
    RestockOrdered,
    RestockPaymentFailed,
    RestockPaymentStarted,
    RestockProposed,
    RestockRejected,
    RestockSpongePlanSubmitted,
    StockLow,
)
from fruit_market.state.store import EventStore

if TYPE_CHECKING:
    from pathlib import Path


class _FakeSponge:
    def __init__(self) -> None:
        self.submit_calls = 0
        self.approve_calls = 0
        self.pay_calls = 0
        self.fail_submit = False
        self.fail_approve = False
        self.fail_pay = False
        self.last_body: dict[str, object] | None = None

    def submit_plan(self, record, body):  # type: ignore[no-untyped-def]
        self.submit_calls += 1
        if self.fail_submit:
            raise RuntimeError("plan unavailable")
        return SpongePlan(plan_id=f"plan_{record.proposal_id}", raw={})

    def approve_plan(self, plan_id: str) -> object:
        self.approve_calls += 1
        if self.fail_approve:
            raise RuntimeError("approval refused")
        return {"plan_id": plan_id, "status": "approved"}

    def paid_fetch(self, record, body):  # type: ignore[no-untyped-def]
        self.pay_calls += 1
        self.last_body = dict(body)
        if self.fail_pay:
            raise RuntimeError("payment failed")
        return SpongePayment(
            payment_id="spg_test",
            receipt="receipt_test",
            response={
                "supplier_order_id": "sup_ord_1",
                "eta_iso": "2026-05-17T20:00:00Z",
            },
            raw={},
        )


class _FailingSupplier:
    def quote(self, item_name: str, qty: int) -> RestockQuote:
        raise RuntimeError("supplier down")


def test_restock_projection_transitions() -> None:
    projection = RestockProjection()
    proposed = _proposal()
    projection.apply(proposed)
    projection.apply(
        RestockSpongePlanSubmitted(
            proposal_id=proposed.proposal_id,
            sponge_plan_id="plan_1",
        )
    )
    projection.apply(
        RestockOperatorEmailed(
            proposal_id=proposed.proposal_id,
            to_email="operator@example.com",
            message_id="mail_1",
        )
    )
    projection.apply(
        RestockApproved(
            proposal_id=proposed.proposal_id,
            payload_hash=proposed.payload_hash,
            amount_cents=proposed.amount_cents,
        )
    )
    projection.apply(
        RestockPaymentStarted(
            proposal_id=proposed.proposal_id,
            amount_cents=proposed.amount_cents,
            sponge_plan_id="plan_1",
        )
    )
    projection.apply(
        RestockOrdered(
            item_id=proposed.item_id,
            qty=proposed.qty,
            supplier_id=proposed.supplier_id,
            sponge_payment_id="spg_1",
            eta_iso="2026-05-17T20:00:00Z",
            proposal_id=proposed.proposal_id,
            supplier_order_id="sup_1",
            amount_cents=proposed.amount_cents,
        )
    )

    record = projection.get(proposed.proposal_id)
    assert record is not None
    assert record.status == "ordered"
    assert record.sponge_plan_id == "plan_1"
    assert record.email_status == "sent"
    assert record.email_message_id == "mail_1"
    assert record.supplier_order_id == "sup_1"


def test_restock_projection_terminal_rejection_and_failure() -> None:
    projection = RestockProjection()
    proposed = _proposal()
    projection.apply(proposed)
    projection.apply(RestockRejected(proposal_id=proposed.proposal_id, reason="no"))
    assert projection.current_pending_approval() is None
    assert projection.current_active().status == "rejected"  # type: ignore[union-attr]

    second = proposed.model_copy(
        update={"proposal_id": "restock_item_2", "stock_low_offset": 2}
    )
    projection.apply(second)
    projection.apply(
        RestockOperatorEmailFailed(
            proposal_id=second.proposal_id,
            to_email="operator@example.com",
            reason="mail down",
        )
    )
    projection.apply(
        RestockPaymentFailed(
            proposal_id=second.proposal_id,
            stage="payment",
            reason="declined",
        )
    )
    assert projection.current_active().status == "failed"  # type: ignore[union-attr]
    assert projection.current_active().email_status == "failed"  # type: ignore[union-attr]


def test_disabled_restock_runtime_is_not_constructed(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    services = make_real_services(store=store)
    runtime = build_restock_runtime(
        settings=RestockSettings(enabled=False),
        store=store,
        services=services,
    )
    assert runtime is None


def test_stock_low_creates_locked_proposal_but_does_not_pay_before_pico(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    record = harness.projection.current_pending_approval()
    assert record is not None
    assert record.item_name == "apple"
    assert record.qty == 24
    assert record.amount_cents == 1200
    assert record.sponge_plan_id == f"plan_{record.proposal_id}"
    assert harness.sponge.submit_calls == 1
    assert harness.sponge.approve_calls == 0
    assert harness.sponge.pay_calls == 0


def test_stock_low_stages_supplier_basket_and_emails_operator(tmp_path: Path) -> None:
    sent: list[tuple[str, str]] = []

    def notifier(to_email, record):  # type: ignore[no-untyped-def]
        sent.append((to_email, record.basket_url))
        return "mail_restock"

    harness = _Harness(
        tmp_path,
        settings=replace(
            _settings(),
            operator_email="operator@example.com",
            supplier_public_base="https://supplier.example",
        ),
        operator_notifier=notifier,
    )
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    record = harness.projection.current_pending_approval()
    assert record is not None
    assert record.basket_url.startswith("https://supplier.example/basket?")
    assert f"proposal_id={record.proposal_id}" in record.basket_url
    assert "amount_cents=1200" in record.basket_url
    assert record.eta_minutes == 30
    assert record.email_status == "sent"
    assert record.email_message_id == "mail_restock"
    assert sent == [("operator@example.com", record.basket_url)]
    assert harness.sponge.approve_calls == 0
    assert harness.sponge.pay_calls == 0


def test_stock_low_texts_operator_restock_details(tmp_path: Path) -> None:
    sent: list[tuple[str, str, int, str]] = []

    def sms_notifier(to_phone, record):  # type: ignore[no-untyped-def]
        sent.append((to_phone, record.item_name, record.qty, record.basket_url))
        return "sms_restock"

    harness = _Harness(
        tmp_path,
        settings=replace(
            _settings(),
            operator_phone="+14155550100",
            supplier_public_base="https://supplier.example",
        ),
        operator_sms_notifier=sms_notifier,
    )
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    record = harness.projection.current_pending_approval()
    assert record is not None
    assert sent == [
        ("+14155550100", "apple", 24, record.basket_url),
    ]
    assert harness.sponge.approve_calls == 0
    assert harness.sponge.pay_calls == 0


def test_operator_email_failure_does_not_block_pico_approval(tmp_path: Path) -> None:
    def notifier(_to_email, _record):  # type: ignore[no-untyped-def]
        raise RuntimeError("mail down")

    harness = _Harness(
        tmp_path,
        settings=replace(
            _settings(),
            operator_email="operator@example.com",
            supplier_public_base="https://supplier.example",
        ),
        operator_notifier=notifier,
    )
    offset, event = harness.stock_low()
    harness.coordinator.handle_stock_low(offset, event)

    record = harness.projection.current_pending_approval()
    assert record is not None
    assert record.email_status == "failed"
    assert record.email_failure_reason == "operator email failed: mail down"

    result = harness.coordinator.approve_pending(approved_by="pico")

    assert result.ok is True
    assert result.status == "ordered"
    assert harness.sponge.pay_calls == 1


def test_pico_approval_approves_sponge_plan_and_pays_once(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    offset, event = harness.stock_low()
    harness.coordinator.handle_stock_low(offset, event)

    result = harness.coordinator.approve_pending(approved_by="pico")

    assert result.ok is True
    assert result.status == "ordered"
    assert harness.sponge.approve_calls == 1
    assert harness.sponge.pay_calls == 1
    assert harness.sponge.last_body is not None
    assert harness.sponge.last_body["idempotency_key"] == f"{harness.item.id}:{offset}"
    record = harness.projection.current_active()
    assert record is not None
    assert record.status == "ordered"
    assert record.supplier_order_id == "sup_ord_1"


def test_duplicate_stock_low_and_approval_do_not_double_pay(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    offset, event = harness.stock_low()
    harness.coordinator.handle_stock_low(offset, event)
    harness.coordinator.handle_stock_low(offset, event)

    assert len(harness.projection.list_all()) == 1
    first = harness.coordinator.approve_pending(approved_by="pico")
    second = harness.coordinator.approve_pending(approved_by="pico")
    assert first.status == "ordered"
    assert second.status == "none"
    assert harness.sponge.pay_calls == 1


def test_reject_pending_restock_does_not_pay(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    offset, event = harness.stock_low()
    harness.coordinator.handle_stock_low(offset, event)

    result = harness.coordinator.reject_pending(
        rejected_by="pico",
        reason="operator_cancel",
    )

    assert result.ok is True
    assert result.status == "rejected"
    assert harness.sponge.pay_calls == 0
    assert harness.projection.current_active().status == "rejected"  # type: ignore[union-attr]


def test_expired_proposal_is_not_paid(tmp_path: Path) -> None:
    harness = _Harness(
        tmp_path,
        settings=replace(_settings(), proposal_ttl_seconds=-1),
    )
    offset, event = harness.stock_low()
    harness.coordinator.handle_stock_low(offset, event)

    result = harness.coordinator.approve_pending(approved_by="pico")

    assert result.ok is False
    assert result.status == "expired"
    assert harness.sponge.pay_calls == 0
    assert harness.projection.current_active().status == "failed"  # type: ignore[union-attr]


def test_cap_exceeded_fails_before_sponge_plan(tmp_path: Path) -> None:
    harness = _Harness(
        tmp_path,
        settings=replace(_settings(), max_order_cents=500),
    )
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    assert harness.projection.current_active().status == "failed"  # type: ignore[union-attr]
    assert harness.projection.current_active().failure_stage == "caps"  # type: ignore[union-attr]
    assert harness.sponge.submit_calls == 0
    assert harness.sponge.pay_calls == 0


def test_supplier_quote_failure_is_logged(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, supplier=_FailingSupplier())
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    events = [ev for _, ev in harness.store.replay()]
    failures = [ev for ev in events if isinstance(ev, RestockPaymentFailed)]
    assert failures[-1].stage == "supplier"
    assert harness.sponge.submit_calls == 0


def test_paysponge_plan_failure_marks_proposal_failed(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.sponge.fail_submit = True
    offset, event = harness.stock_low()

    harness.coordinator.handle_stock_low(offset, event)

    assert harness.projection.current_active().status == "failed"  # type: ignore[union-attr]
    assert harness.projection.current_active().failure_stage == "plan"  # type: ignore[union-attr]
    assert harness.sponge.pay_calls == 0


class _Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        settings: RestockSettings | None = None,
        supplier=None,  # type: ignore[no-untyped-def]
        operator_notifier=None,  # type: ignore[no-untyped-def]
        operator_sms_notifier=None,  # type: ignore[no-untyped-def]
    ) -> None:
        self.store = EventStore(tmp_path / "events.db")
        self.services = make_real_services(store=self.store)
        self.projection = RestockProjection.hydrate(self.store.replay())
        self.store.subscribe(lambda _offset, event: self.projection.apply(event))
        self.sponge = _FakeSponge()
        self.coordinator = RestockCoordinator(
            settings=settings or _settings(),
            store=self.store,
            projection=self.projection,
            catalog=self.services.catalog,
            supplier=supplier or StaticSupplierClient("https://supplier.x402.test/orders"),
            sponge=self.sponge,
            operator_notifier=operator_notifier,
            operator_sms_notifier=operator_sms_notifier,
        )
        self.item = self.services.teach.confirm(
            self.services.teach.propose("These are apples, $1.50, 6 of them").id
        )

    def stock_low(self) -> tuple[int, StockLow]:
        event = StockLow(item_id=self.item.id, threshold=2, current_count=0)
        offset = self.store.append(event)
        return offset, event


def _settings() -> RestockSettings:
    return RestockSettings(
        enabled=True,
        payment_mode="staging_live",
        require_pico_approval=True,
        require_sponge_plan_approval=True,
        supplier_gateway_url="https://supplier.x402.test/orders",
    )


def _proposal() -> RestockProposed:
    return RestockProposed(
        proposal_id="restock_item_1",
        stock_low_offset=1,
        item_id="item_1",
        item_name="apple",
        qty=24,
        supplier_id="demo",
        supplier_name="Demo Fruit Supplier",
        unit_price_cents=50,
        amount_cents=1200,
        gateway_url="https://supplier.x402.test/orders",
        payload_hash="a" * 64,
        idempotency_key="item_1:1",
        expires_at_iso="2026-05-17T20:00:00Z",
    )
