"""Coordinator for the optional restock state machine."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from collections.abc import Callable

from fruit_market.restock.supplier import (
    parse_supplier_confirmation,
)
from fruit_market.state.events import (
    RestockApproved,
    RestockOrdered,
    RestockOperatorEmailFailed,
    RestockOperatorEmailed,
    RestockPaymentFailed,
    RestockPaymentStarted,
    RestockProposed,
    RestockRejected,
    RestockSpongePlanSubmitted,
    StockLow,
)

if TYPE_CHECKING:
    from fruit_market.restock.paysponge import PaySpongeClient
    from fruit_market.restock.projection import RestockProjection, RestockRecord
    from fruit_market.restock.settings import RestockSettings
    from fruit_market.restock.supplier import SupplierQuoteClient
    from fruit_market.services.protocols import CatalogService
    from fruit_market.state.store import EventStore


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    proposal_id: str | None
    status: str
    detail: str = ""


class RestockCoordinator:
    """Owns proposal creation, approval gates, and paid supplier calls."""

    def __init__(
        self,
        *,
        settings: RestockSettings,
        store: EventStore,
        projection: RestockProjection,
        catalog: CatalogService,
        supplier: SupplierQuoteClient,
        sponge: PaySpongeClient | None,
        operator_notifier: Callable[[str, RestockRecord], str] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._projection = projection
        self._catalog = catalog
        self._supplier = supplier
        self._sponge = sponge
        self._operator_notifier = operator_notifier
        self._lock = threading.Lock()

    def handle_stock_low(self, offset: int, event: StockLow) -> None:
        if not self._settings.enabled:
            return
        with self._lock:
            if self._projection.has_stock_low_offset(offset):
                return
            proposal_id = f"restock_{event.item_id}_{offset}"
            item = self._catalog.get_item(event.item_id)
            if item is None:
                self._fail(proposal_id, "proposal", f"unknown item: {event.item_id}")
                return

            try:
                quote = self._supplier.quote(item.name, self._settings.default_quantity)
            except Exception as exc:  # noqa: BLE001
                self._fail(proposal_id, "supplier", f"supplier quote failed: {exc}")
                return

            idempotency_key = f"{event.item_id}:{offset}"
            basket_url = _basket_url(
                public_base=self._settings.supplier_public_base,
                proposal_id=proposal_id,
                item_name=quote.item_name,
                qty=quote.qty,
                supplier_id=quote.supplier_id,
                amount_cents=quote.amount_cents,
                idempotency_key=idempotency_key,
            )
            order_body = _order_body(
                proposal_id=proposal_id,
                item_name=quote.item_name,
                qty=quote.qty,
                supplier_id=quote.supplier_id,
                amount_cents=quote.amount_cents,
                idempotency_key=idempotency_key,
            )
            payload_hash = _payload_hash(order_body)
            expires_at = datetime.now(tz=UTC) + timedelta(
                seconds=self._settings.proposal_ttl_seconds
            )

            self._store.append(
                RestockProposed(
                    proposal_id=proposal_id,
                    stock_low_offset=offset,
                    item_id=item.id,
                    item_name=item.name,
                    qty=quote.qty,
                    supplier_id=quote.supplier_id,
                    supplier_name=quote.supplier_name,
                    unit_price_cents=quote.unit_price_cents,
                    amount_cents=quote.amount_cents,
                    gateway_url=quote.gateway_url,
                    payload_hash=payload_hash,
                    idempotency_key=idempotency_key,
                    expires_at_iso=_iso(expires_at),
                    eta_minutes=quote.eta_minutes,
                    basket_url=basket_url,
                )
            )
            record = self._projection.get(proposal_id)
            if record is None:
                return

            cap_error = self._cap_error(record.amount_cents)
            if cap_error is not None:
                self._fail(record.proposal_id, "caps", cap_error)
                return

            self._notify_operator(record)

            if self._settings.payment_mode != "staging_live":
                return
            if not record.gateway_url:
                self._fail(
                    record.proposal_id,
                    "proposal",
                    "RESTOCK_SUPPLIER_GATEWAY_URL is required for staging_live",
                )
                return
            if self._settings.require_sponge_plan_approval:
                self._submit_sponge_plan(record, order_body)

    def approve_pending(self, *, approved_by: str = "pico") -> ApprovalResult:
        with self._lock:
            record = self._projection.current_pending_approval()
            if record is None:
                return ApprovalResult(False, None, "none", "no pending restock proposal")
            if self._is_expired(record):
                self._fail(record.proposal_id, "expired", "restock proposal expired")
                return ApprovalResult(False, record.proposal_id, "expired")

            self._store.append(
                RestockApproved(
                    proposal_id=record.proposal_id,
                    approved_by=approved_by,  # type: ignore[arg-type]
                    payload_hash=record.payload_hash,
                    amount_cents=record.amount_cents,
                )
            )
            self._process_approved(record.proposal_id)
            final = self._projection.get(record.proposal_id)
            return ApprovalResult(
                ok=final is not None and final.status == "ordered",
                proposal_id=record.proposal_id,
                status=final.status if final else "unknown",
                detail=final.failure_reason or "" if final else "",
            )

    def reject_pending(self, *, rejected_by: str = "pico", reason: str = "") -> ApprovalResult:
        with self._lock:
            record = self._projection.current_pending_approval()
            if record is None:
                return ApprovalResult(False, None, "none", "no pending restock proposal")
            self._store.append(
                RestockRejected(
                    proposal_id=record.proposal_id,
                    rejected_by=rejected_by,  # type: ignore[arg-type]
                    reason=reason,
                )
            )
            return ApprovalResult(True, record.proposal_id, "rejected", reason)

    def _submit_sponge_plan(
        self, record: RestockRecord, order_body: dict[str, object]
    ) -> None:
        if self._sponge is None:
            self._fail(record.proposal_id, "plan", "PaySponge client is not configured")
            return
        try:
            plan = self._sponge.submit_plan(record, order_body)
        except Exception as exc:  # noqa: BLE001
            self._fail(record.proposal_id, "plan", f"PaySponge plan failed: {exc}")
            return
        self._store.append(
            RestockSpongePlanSubmitted(
                proposal_id=record.proposal_id,
                sponge_plan_id=plan.plan_id,
            )
        )

    def _notify_operator(self, record: RestockRecord) -> None:
        to_email = self._settings.operator_email
        if not to_email:
            return
        try:
            sender = self._operator_notifier or _send_restock_email
            message_id = sender(to_email, record)
        except Exception as exc:  # noqa: BLE001
            self._store.append(
                RestockOperatorEmailFailed(
                    proposal_id=record.proposal_id,
                    to_email=to_email,
                    reason=f"operator email failed: {exc}",
                )
            )
            return
        self._store.append(
            RestockOperatorEmailed(
                proposal_id=record.proposal_id,
                to_email=to_email,
                message_id=message_id,
            )
        )

    def _process_approved(self, proposal_id: str) -> None:
        record = self._projection.get(proposal_id)
        if record is None:
            return
        if self._is_expired(record):
            self._fail(record.proposal_id, "expired", "restock proposal expired")
            return
        cap_error = self._cap_error(record.amount_cents)
        if cap_error is not None:
            self._fail(record.proposal_id, "caps", cap_error)
            return
        if self._settings.payment_mode != "staging_live":
            self._fail(record.proposal_id, "payment", "restock payment mode is disabled")
            return
        if self._sponge is None:
            self._fail(record.proposal_id, "payment", "PaySponge client is not configured")
            return
        if self._settings.require_sponge_plan_approval and not record.sponge_plan_id:
            self._fail(record.proposal_id, "approval", "missing PaySponge plan id")
            return

        if self._settings.require_sponge_plan_approval:
            try:
                self._sponge.approve_plan(record.sponge_plan_id or "")
            except Exception as exc:  # noqa: BLE001
                self._fail(record.proposal_id, "approval", f"PaySponge approval failed: {exc}")
                return

        self._store.append(
            RestockPaymentStarted(
                proposal_id=record.proposal_id,
                amount_cents=record.amount_cents,
                sponge_plan_id=record.sponge_plan_id,
            )
        )
        order_body = _order_body(
            proposal_id=record.proposal_id,
            item_name=record.item_name,
            qty=record.qty,
            supplier_id=record.supplier_id,
            amount_cents=record.amount_cents,
            idempotency_key=record.idempotency_key,
        )
        if _payload_hash(order_body) != record.payload_hash:
            self._fail(record.proposal_id, "payment", "proposal payload hash changed")
            return

        try:
            payment = self._sponge.paid_fetch(record, order_body)
            confirmation = parse_supplier_confirmation(payment.response)
        except Exception as exc:  # noqa: BLE001
            self._fail(record.proposal_id, "payment", f"paid supplier request failed: {exc}")
            return

        self._store.append(
            RestockOrdered(
                item_id=record.item_id,
                qty=record.qty,
                supplier_id=record.supplier_id,
                sponge_payment_id=payment.payment_id or f"spg_{record.proposal_id}",
                eta_iso=confirmation.eta_iso,
                proposal_id=record.proposal_id,
                supplier_order_id=confirmation.supplier_order_id,
                amount_cents=record.amount_cents,
                payment_receipt=payment.receipt,
            )
        )

    def _fail(self, proposal_id: str, stage: str, reason: str) -> None:
        self._store.append(
            RestockPaymentFailed(
                proposal_id=proposal_id,
                stage=stage,  # type: ignore[arg-type]
                reason=reason,
            )
        )

    def _is_expired(self, record: RestockRecord) -> bool:
        try:
            expires_at = datetime.fromisoformat(
                record.expires_at_iso.replace("Z", "+00:00")
            )
        except ValueError:
            return True
        return expires_at <= datetime.now(tz=UTC)

    def _cap_error(self, amount_cents: int) -> str | None:
        if amount_cents > self._settings.max_order_cents:
            return (
                f"restock amount {amount_cents} exceeds per-order cap "
                f"{self._settings.max_order_cents}"
            )
        now = datetime.now(tz=UTC)
        hour_spend = 0
        day_spend = 0
        for _, event in self._store.replay():
            if not isinstance(event, RestockPaymentStarted):
                continue
            age = now - event.ts
            if age <= timedelta(hours=1):
                hour_spend += event.amount_cents
            if age <= timedelta(days=1):
                day_spend += event.amount_cents
        if hour_spend + amount_cents > self._settings.max_hour_cents:
            return "restock would exceed hourly spend cap"
        if day_spend + amount_cents > self._settings.max_day_cents:
            return "restock would exceed daily spend cap"
        return None


def _order_body(
    *,
    proposal_id: str,
    item_name: str,
    qty: int,
    supplier_id: str,
    amount_cents: int,
    idempotency_key: str,
) -> dict[str, object]:
    return {
        "proposal_id": proposal_id,
        "item": item_name,
        "quantity": qty,
        "supplier_id": supplier_id,
        "amount_cents": amount_cents,
        "idempotency_key": idempotency_key,
    }


def _basket_url(
    *,
    public_base: str,
    proposal_id: str,
    item_name: str,
    qty: int,
    supplier_id: str,
    amount_cents: int,
    idempotency_key: str,
) -> str:
    if not public_base:
        return ""
    query = urlencode(
        {
            "proposal_id": proposal_id,
            "item": item_name,
            "quantity": qty,
            "supplier_id": supplier_id,
            "amount_cents": amount_cents,
            "idempotency_key": idempotency_key,
        }
    )
    return f"{public_base.rstrip('/')}/basket?{query}"


def _send_restock_email(to_email: str, record: RestockRecord) -> str:
    from fruit_market.integrations.agentmail import (  # noqa: PLC0415
        send_restock_approval,
    )

    return send_restock_approval(to_email, record)


def _payload_hash(body: dict[str, object]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
