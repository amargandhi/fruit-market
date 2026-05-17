"""Tiny staging supplier API for the PaySponge Gateway demo.

Run locally with:

    uvicorn fruit_market.restock.demo_supplier_app:app --port 8001

For the live demo, expose this HTTPS app and put only ``POST /orders``
behind PaySponge Gateway/x402. ``GET /catalog`` and ``POST /quotes``
can stay direct/free so the restock agent can plan before payment.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field


SUPPLIER_ID = "demo_fruit_supplier"
SUPPLIER_NAME = "Demo Fruit Supplier"
APPLE_UNIT_PRICE_CENTS = 50
DEFAULT_ETA_MINUTES = 30


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogItem(_Model):
    item: str
    unit_price_cents: int = Field(ge=0)
    quantity_available: int = Field(ge=0)
    eta_minutes: int = Field(ge=0)


class QuoteRequest(_Model):
    item: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class QuoteResponse(_Model):
    supplier_id: str
    supplier_name: str
    item: str
    quantity: int = Field(gt=0)
    unit_price_cents: int = Field(ge=0)
    amount_cents: int = Field(gt=0)
    eta_minutes: int = Field(ge=0)


class OrderRequest(_Model):
    proposal_id: str = Field(min_length=1)
    item: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    supplier_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    idempotency_key: str = Field(min_length=8)


class OrderResponse(_Model):
    supplier_order_id: str
    supplier_id: str
    item: str
    quantity: int
    amount_cents: int
    eta_iso: str
    status: str = "confirmed"


app = FastAPI(title="Fruit Market Demo Supplier")
_orders: dict[str, OrderResponse] = {}


@app.get("/catalog", response_model=list[CatalogItem])
def catalog() -> list[CatalogItem]:
    return [
        CatalogItem(
            item="apple",
            unit_price_cents=APPLE_UNIT_PRICE_CENTS,
            quantity_available=240,
            eta_minutes=DEFAULT_ETA_MINUTES,
        )
    ]


@app.post("/quotes", response_model=QuoteResponse)
def quote(payload: QuoteRequest) -> QuoteResponse:
    _assert_apple(payload.item)
    return QuoteResponse(
        supplier_id=SUPPLIER_ID,
        supplier_name=SUPPLIER_NAME,
        item="apple",
        quantity=payload.quantity,
        unit_price_cents=APPLE_UNIT_PRICE_CENTS,
        amount_cents=payload.quantity * APPLE_UNIT_PRICE_CENTS,
        eta_minutes=DEFAULT_ETA_MINUTES,
    )


@app.post("/orders", response_model=OrderResponse)
def create_order(payload: OrderRequest) -> OrderResponse:
    existing = _orders.get(payload.idempotency_key)
    if existing is not None:
        return existing
    if payload.supplier_id != SUPPLIER_ID:
        raise HTTPException(status_code=400, detail="unknown supplier")
    _assert_apple(payload.item)
    expected = payload.quantity * APPLE_UNIT_PRICE_CENTS
    if payload.amount_cents != expected:
        raise HTTPException(status_code=400, detail="amount does not match quote")

    response = OrderResponse(
        supplier_order_id=f"demo_sup_{len(_orders) + 1:04d}",
        supplier_id=SUPPLIER_ID,
        item="apple",
        quantity=payload.quantity,
        amount_cents=payload.amount_cents,
        eta_iso=_eta_iso(DEFAULT_ETA_MINUTES),
    )
    _orders[payload.idempotency_key] = response
    return response


@app.get("/orders/{supplier_order_id}", response_model=OrderResponse)
def get_order(supplier_order_id: str) -> OrderResponse:
    for order in _orders.values():
        if order.supplier_order_id == supplier_order_id:
            return order
    raise HTTPException(status_code=404, detail="unknown order")


def _assert_apple(item: str) -> None:
    if item.strip().lower().rstrip("s") != "apple":
        raise HTTPException(status_code=404, detail="item unavailable")


def _eta_iso(minutes: int) -> str:
    eta = datetime.now(tz=UTC) + timedelta(minutes=minutes)
    return eta.isoformat().replace("+00:00", "Z")
