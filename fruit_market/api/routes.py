"""Kiosk-facing API routes."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from fruit_market.api.schemas import (
    CatalogItemView,
    KioskSSEEvent,
    KioskStateSnapshot,
    OrderView,
    PackOrderResponse,
    PendingActions,
    PicoActionRequest,
    PicoActionResponse,
    RestockView,
    SwitchActiveItemRequest,
    SwitchActiveItemResponse,
    SystemHealth,
    TeachConfirmRequest,
    TeachProposalView,
    TeachRequest,
    TeachResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from fruit_market.restock import RestockRuntime
    from fruit_market.services.protocols import Item, Order, Services, TeachProposal
    from fruit_market.vision import VisionBundle

router = APIRouter(prefix="/api", tags=["kiosk"])


def get_services(request: Request) -> Services:
    return cast("Services", request.app.state.services)


def get_vision(app: FastAPI) -> VisionBundle | None:
    return cast("VisionBundle | None", getattr(app.state, "vision", None))


def get_restock(app: FastAPI) -> RestockRuntime | None:
    return cast("RestockRuntime | None", getattr(app.state, "restock", None))


@router.get("/state", response_model=KioskStateSnapshot)
def get_state(request: Request) -> KioskStateSnapshot:
    return build_snapshot(
        get_services(request),
        get_vision(request.app),
        get_restock(request.app),
    )


@router.get("/state/stream")
async def stream_state(request: Request) -> StreamingResponse:
    services = get_services(request)
    return StreamingResponse(
        _stream_events(
            request,
            services,
            get_vision(request.app),
            get_restock(request.app),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/teach", response_model=TeachResponse)
def teach(request: Request, payload: TeachRequest) -> TeachResponse:
    proposal = get_services(request).teach.propose(payload.transcript)
    return TeachResponse(proposal=_proposal_view(proposal))


@router.post("/teach/confirm", response_model=CatalogItemView)
def confirm_teach(request: Request, payload: TeachConfirmRequest) -> CatalogItemView:
    services = get_services(request)
    try:
        item = services.teach.confirm(payload.proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown proposal") from exc
    return _catalog_item_view(item, item.id)


@router.post("/orders/{order_id}/pack", response_model=PackOrderResponse)
def pack_order(request: Request, order_id: str) -> PackOrderResponse:
    services = get_services(request)
    try:
        services.orders.mark_packed(order_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown order") from exc
    order = services.orders.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="unknown order")
    return PackOrderResponse(order_id=order.id, status=order.status)


@router.post("/active-item", response_model=SwitchActiveItemResponse)
def switch_active_item(
    request: Request,
    payload: SwitchActiveItemRequest,
) -> SwitchActiveItemResponse:
    try:
        get_services(request).catalog.set_active_item(payload.item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown item") from exc
    return SwitchActiveItemResponse(item_id=payload.item_id)


# ─── Camera feed ────────────────────────────────────────────────────


@router.get("/camera/frame.jpg")
def camera_frame(request: Request) -> Response:
    """Most recent camera snapshot as a JPEG.

    The kiosk polls this every second for a live feed. Returns
    503 with ``X-Camera-Status: starting`` if the watcher hasn't
    captured anything yet (e.g. lifespan still warming up, or no
    active item so the watcher is idle). Browsers can re-poll on
    a 503 without crashing the <img> tag.
    """

    vision = get_vision(request.app)
    if vision is None:
        return Response(
            status_code=503,
            content=b"vision pipeline disabled",
            media_type="text/plain",
            headers={"X-Camera-Status": "disabled"},
        )
    frame = vision.watcher.latest_frame
    if frame is None:
        return Response(
            status_code=503,
            content=b"no frame captured yet",
            media_type="text/plain",
            headers={"X-Camera-Status": "starting"},
        )
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={
            # Never cache — the kiosk polls this URL with a busted
            # query-string but downstream proxies might still try.
            "Cache-Control": "no-store, max-age=0",
            "X-Camera-Status": "ok",
        },
    )


# ─── Pico keypad ────────────────────────────────────────────────────


@router.post("/pico/action", response_model=PicoActionResponse)
def pico_action(request: Request, payload: PicoActionRequest) -> PicoActionResponse:
    """Dispatch a button event from the Pico sidecar bridge.

    The firmware emits one of the six canonical action names; we
    resolve the target resource from current state and call the
    appropriate service. Returns ``status="no_pending"`` when an
    action has nothing to act on — the bridge logs it but doesn't
    error.
    """

    services = get_services(request)
    restock = get_restock(request.app)
    action = payload.action

    if action == "ready":
        return PicoActionResponse(action=action, status="ok")

    if action == "packed":
        for order in services.orders.list_active():
            if order.status == "paid":
                services.orders.mark_packed(order.id)
                return PicoActionResponse(action=action, status="ok", order_id=order.id)
        return PicoActionResponse(action=action, status="no_pending")

    if action == "cancel":
        if restock is not None and restock.projection.current_pending_approval() is not None:
            result = restock.reject_pending(rejected_by="pico", reason="operator_cancel")
            return PicoActionResponse(
                ok=result.ok,
                action=action,
                status=result.status,
                detail=result.detail,
                proposal_id=result.proposal_id,
            )
        for order in services.orders.list_active():
            if order.status == "reserved":
                services.orders.cancel(order.id, "operator")
                return PicoActionResponse(action=action, status="ok", order_id=order.id)
        return PicoActionResponse(action=action, status="no_pending")

    if action == "count_now":
        # The watcher's motion gate is the only thing that
        # suppresses a tick on a static scene; forcing the next
        # tick is a follow-up enhancement (would need a
        # ``force_next_tick`` flag on VisionWatcher). For now
        # acknowledge so the bridge knows the press registered.
        return PicoActionResponse(action=action, status="acknowledged")

    if action == "confirm":
        # TeachService doesn't expose a list of pending proposals
        # yet — the kiosk owns the proposal_id flow. The Pico's
        # confirm button is reserved for the post-MVP path where
        # we surface the latest proposal here.
        return PicoActionResponse(action=action, status="no_pending")

    if action == "supply_buy":
        if restock is None:
            return PicoActionResponse(
                ok=False,
                action=action,
                status="disabled",
                detail="restock feature is disabled",
            )
        result = restock.approve_pending(approved_by="pico")
        return PicoActionResponse(
            ok=result.ok,
            action=action,
            status=result.status,
            detail=result.detail,
            proposal_id=result.proposal_id,
        )

    raise HTTPException(status_code=400, detail=f"unknown action: {action}")


# ─── Snapshot builder ───────────────────────────────────────────────


def build_snapshot(
    services: Services,
    vision: VisionBundle | None = None,
    restock: RestockRuntime | None = None,
) -> KioskStateSnapshot:
    active = services.catalog.get_active_item()
    active_id = active.id if active else None
    items = [_catalog_item_view(item, active_id) for item in services.catalog.list_items()]
    orders = [_order_view(services, order) for order in services.orders.list_active()]
    return KioskStateSnapshot(
        catalog=items,
        active_item_id=active_id,
        orders=orders,
        pending=_pending(services, restock),
        restock=_restock_view(restock),
        health=_health(vision),
    )


def _pending(services: Services, restock: RestockRuntime | None) -> PendingActions:
    """Compute which action keys on the Pico should be lit up."""

    active = services.orders.list_active()
    paid_order_id: str | None = None
    reservation_id: str | None = None
    for order in active:
        if order.status == "paid" and paid_order_id is None:
            paid_order_id = order.id
        if order.status == "reserved" and reservation_id is None:
            reservation_id = order.id
    return PendingActions(
        teach_proposal=None,  # surfaced in a follow-up; kiosk owns the flow
        paid_order=paid_order_id,
        reservation=reservation_id,
        supply_buy=(
            restock is not None
            and restock.projection.current_pending_approval() is not None
        ),
    )


def _restock_view(restock: RestockRuntime | None) -> RestockView | None:
    if restock is None:
        return None
    record = restock.projection.current_active()
    if record is None:
        return None
    return RestockView(
        proposal_id=record.proposal_id,
        item_id=record.item_id,
        item_name=record.item_name,
        qty=record.qty,
        supplier_name=record.supplier_name,
        amount_cents=record.amount_cents,
        status=record.status,
        eta_iso=record.eta_iso,
        failure_reason=record.failure_reason,
    )


def _health(vision: VisionBundle | None) -> SystemHealth:
    """Map subsystem state to the Pico's health vocabulary."""

    camera = "unknown"
    model = "unknown"
    if vision is not None:
        camera = "ok"
        status = vision.model.status()
        if status.get("loaded"):
            model = "ok"
        elif status.get("warmup_in_flight"):
            model = "warmup"
        else:
            model = "unknown"
    # Phone health requires an AgentPhone reachability ping; until
    # that's wired we report "mock" if AGENTPHONE_SEND_MODE is unset
    # or "live"/"mock"/"disabled" accordingly.
    import os  # noqa: PLC0415

    send_mode = os.environ.get("AGENTPHONE_SEND_MODE", "").strip().lower()
    if send_mode == "live":
        phone = "ok"
    elif send_mode == "disabled":
        phone = "down"
    else:
        phone = "mock"
    return SystemHealth(camera=camera, model=model, phone=phone)


def _catalog_item_view(item: Item, active_item_id: str | None) -> CatalogItemView:
    return CatalogItemView(
        item_id=item.id,
        name=item.name,
        price_cents=item.price_cents,
        physical_count=item.physical_count,
        is_active=item.id == active_item_id,
        is_low=item.physical_count <= item.reorder_threshold,
    )


def _order_view(services: Services, order: Order) -> OrderView:
    item = services.catalog.get_item(order.item_id)
    return OrderView(
        order_id=order.id,
        item_id=order.item_id,
        item_name=item.name if item else order.item_id,
        qty=order.qty,
        total_cents=order.total_cents,
        status=order.status,
        customer_phone=order.customer_phone,
    )


def _proposal_view(proposal: TeachProposal) -> TeachProposalView:
    return TeachProposalView(
        proposal_id=proposal.id,
        name=proposal.name,
        price_cents=proposal.price_cents,
        initial_count=proposal.initial_count,
        reorder_threshold=proposal.reorder_threshold,
    )


async def _stream_events(
    request: Request,
    services: Services,
    vision: VisionBundle | None,
    restock: RestockRuntime | None,
) -> AsyncIterator[str]:
    event_id = 1
    while not await request.is_disconnected():
        snapshot = build_snapshot(services, vision, restock)
        events = [
            KioskSSEEvent(event="state.catalog", data={"catalog": _dump(snapshot.catalog)}, id=event_id),
            KioskSSEEvent(
                event="state.inventory",
                data={"catalog": _dump(snapshot.catalog)},
                id=event_id + 1,
            ),
            KioskSSEEvent(event="state.orders", data={"orders": _dump(snapshot.orders)}, id=event_id + 2),
            KioskSSEEvent(
                event="state.active_item",
                data={"active_item_id": snapshot.active_item_id},
                id=event_id + 3,
            ),
            KioskSSEEvent(
                event="state.restock",
                data={
                    "pending": snapshot.pending.model_dump(mode="json"),
                    "restock": (
                        snapshot.restock.model_dump(mode="json")
                        if snapshot.restock is not None
                        else None
                    ),
                },
                id=event_id + 4,
            ),
        ]
        for event in events:
            yield _format_sse(event)
        low_items = [item for item in snapshot.catalog if item.is_low]
        if low_items:
            yield _format_sse(
                KioskSSEEvent(
                    event="state.stock_low",
                    data={"items": _dump(low_items)},
                    id=event_id + 5,
                )
            )
            event_id += 6
        else:
            event_id += 5
        await asyncio.sleep(2)


def _format_sse(event: KioskSSEEvent) -> str:
    return (
        f"id: {event.id}\n"
        f"event: {event.event}\n"
        f"data: {json.dumps(event.data, separators=(',', ':'))}\n\n"
    )


def _dump(models: list[CatalogItemView] | list[OrderView]) -> list[dict[str, object]]:
    return [model.model_dump(mode="json") for model in models]
