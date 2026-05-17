"""Read model for the optional restock state machine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from fruit_market.state.events import (
    Event,
    RestockApproved,
    RestockOrdered,
    RestockPaymentFailed,
    RestockPaymentStarted,
    RestockProposed,
    RestockReceived,
    RestockRejected,
    RestockSpongePlanSubmitted,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


RestockStatus = Literal[
    "pending_approval",
    "approved",
    "payment_started",
    "ordered",
    "rejected",
    "failed",
    "received",
]


@dataclass(frozen=True)
class RestockRecord:
    proposal_id: str
    stock_low_offset: int
    item_id: str
    item_name: str
    qty: int
    supplier_id: str
    supplier_name: str
    unit_price_cents: int
    amount_cents: int
    gateway_url: str
    payload_hash: str
    idempotency_key: str
    expires_at_iso: str
    status: RestockStatus
    sponge_plan_id: str | None = None
    supplier_order_id: str | None = None
    sponge_payment_id: str | None = None
    eta_iso: str | None = None
    failure_stage: str | None = None
    failure_reason: str | None = None


class RestockProjection:
    """Restock proposals by id plus helpers for current UI state."""

    def __init__(self) -> None:
        self._records: dict[str, RestockRecord] = {}
        self._by_stock_low_offset: dict[int, str] = {}

    @classmethod
    def hydrate(cls, events: Iterable[tuple[int, Event]]) -> RestockProjection:
        proj = cls()
        for _, ev in events:
            proj.apply(ev)
        return proj

    def apply(self, event: Event) -> None:
        if isinstance(event, RestockProposed):
            record = RestockRecord(
                proposal_id=event.proposal_id,
                stock_low_offset=event.stock_low_offset,
                item_id=event.item_id,
                item_name=event.item_name,
                qty=event.qty,
                supplier_id=event.supplier_id,
                supplier_name=event.supplier_name,
                unit_price_cents=event.unit_price_cents,
                amount_cents=event.amount_cents,
                gateway_url=event.gateway_url,
                payload_hash=event.payload_hash,
                idempotency_key=event.idempotency_key,
                expires_at_iso=event.expires_at_iso,
                status="pending_approval",
            )
            self._records[event.proposal_id] = record
            self._by_stock_low_offset[event.stock_low_offset] = event.proposal_id
        elif isinstance(event, RestockSpongePlanSubmitted):
            self._replace(
                event.proposal_id, sponge_plan_id=event.sponge_plan_id
            )
        elif isinstance(event, RestockApproved):
            self._replace(event.proposal_id, status="approved")
        elif isinstance(event, RestockRejected):
            self._replace(
                event.proposal_id,
                status="rejected",
                failure_reason=event.reason,
            )
        elif isinstance(event, RestockPaymentStarted):
            self._replace(event.proposal_id, status="payment_started")
        elif isinstance(event, RestockPaymentFailed):
            self._replace(
                event.proposal_id,
                status="failed",
                failure_stage=event.stage,
                failure_reason=event.reason,
            )
        elif isinstance(event, RestockOrdered):
            proposal_id = event.proposal_id
            if proposal_id:
                self._replace(
                    proposal_id,
                    status="ordered",
                    supplier_order_id=event.supplier_order_id or None,
                    sponge_payment_id=event.sponge_payment_id or None,
                    eta_iso=event.eta_iso,
                )
        elif isinstance(event, RestockReceived):
            self._replace(event.proposal_id, status="received")

    def _replace(self, proposal_id: str, **changes: object) -> None:
        existing = self._records.get(proposal_id)
        if existing is not None:
            self._records[proposal_id] = replace(existing, **changes)  # type: ignore[arg-type]

    def get(self, proposal_id: str) -> RestockRecord | None:
        return self._records.get(proposal_id)

    def list_all(self) -> list[RestockRecord]:
        return list(self._records.values())

    def has_stock_low_offset(self, offset: int) -> bool:
        return offset in self._by_stock_low_offset

    def current_pending_approval(self) -> RestockRecord | None:
        for record in reversed(self.list_all()):
            if record.status == "pending_approval":
                return record
        return None

    def current_active(self) -> RestockRecord | None:
        active_statuses = {
            "pending_approval",
            "approved",
            "payment_started",
            "ordered",
            "rejected",
            "failed",
        }
        for record in reversed(self.list_all()):
            if record.status in active_statuses:
                return record
        return None
