"""Kiosk-facing API routes."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from fruit_market.api.schemas import (
    CatalogItemView,
    KioskSSEEvent,
    KioskStateSnapshot,
    OrderView,
    PackOrderResponse,
    SwitchActiveItemRequest,
    SwitchActiveItemResponse,
    TeachConfirmRequest,
    TeachProposalView,
    TeachRequest,
    TeachResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fruit_market.services.protocols import Item, Order, Services, TeachProposal

router = APIRouter(prefix="/api", tags=["kiosk"])


def get_services(request: Request) -> Services:
    return cast("Services", request.app.state.services)


@router.get("/state", response_model=KioskStateSnapshot)
def get_state(request: Request) -> KioskStateSnapshot:
    return build_snapshot(get_services(request))


@router.get("/state/stream")
async def stream_state(request: Request) -> StreamingResponse:
    services = get_services(request)
    return StreamingResponse(
        _stream_events(request, services),
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


def build_snapshot(services: Services) -> KioskStateSnapshot:
    active = services.catalog.get_active_item()
    active_id = active.id if active else None
    items = [_catalog_item_view(item, active_id) for item in services.catalog.list_items()]
    orders = [_order_view(services, order) for order in services.orders.list_active()]
    return KioskStateSnapshot(catalog=items, active_item_id=active_id, orders=orders)


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


async def _stream_events(request: Request, services: Services) -> AsyncIterator[str]:
    event_id = 1
    while not await request.is_disconnected():
        snapshot = build_snapshot(services)
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
        ]
        for event in events:
            yield _format_sse(event)
        low_items = [item for item in snapshot.catalog if item.is_low]
        if low_items:
            yield _format_sse(
                KioskSSEEvent(
                    event="state.stock_low",
                    data={"items": _dump(low_items)},
                    id=event_id + 4,
                )
            )
            event_id += 5
        else:
            event_id += 4
        await asyncio.sleep(2)


def _format_sse(event: KioskSSEEvent) -> str:
    return (
        f"id: {event.id}\n"
        f"event: {event.event}\n"
        f"data: {json.dumps(event.data, separators=(',', ':'))}\n\n"
    )


def _dump(models: list[CatalogItemView] | list[OrderView]) -> list[dict[str, object]]:
    return [model.model_dump(mode="json") for model in models]
