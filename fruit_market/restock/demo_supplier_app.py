"""Tiny staging supplier API for the PaySponge Gateway demo.

Run locally with:

    uvicorn fruit_market.restock.demo_supplier_app:app --port 8001

For the live demo, expose this HTTPS app and put only ``POST /orders``
behind PaySponge Gateway/x402. ``GET /catalog`` and ``POST /quotes``
can stay direct/free so the restock agent can plan before payment.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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
    proposal_id: str
    supplier_id: str
    item: str
    quantity: int
    amount_cents: int
    eta_iso: str
    status: str = "confirmed"


app = FastAPI(title="Fruit Market Demo Supplier")
_orders: dict[str, OrderResponse] = {}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def supplier_home() -> HTMLResponse:
    return HTMLResponse(_supplier_page())


@app.get("/basket", response_class=HTMLResponse, include_in_schema=False)
def supplier_basket(
    proposal_id: str = "",
    item: str = "apple",
    quantity: int = 24,
    supplier_id: str = SUPPLIER_ID,
    amount_cents: int = 1200,
    idempotency_key: str = "",
) -> HTMLResponse:
    return HTMLResponse(
        _basket_page(
            proposal_id=proposal_id,
            item=item,
            quantity=quantity,
            supplier_id=supplier_id,
            amount_cents=amount_cents,
            idempotency_key=idempotency_key,
        )
    )


@app.get("/demo-basket", response_class=HTMLResponse, include_in_schema=False)
def supplier_demo_basket(reset: bool = False) -> HTMLResponse:
    if reset:
        _orders.pop("item_apple:video_demo", None)
    return HTMLResponse(
        _basket_page(
            proposal_id="restock_video_demo",
            item="apple",
            quantity=24,
            supplier_id=SUPPLIER_ID,
            amount_cents=24 * APPLE_UNIT_PRICE_CENTS,
            idempotency_key="item_apple:video_demo",
        )
    )


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
        proposal_id=payload.proposal_id,
        supplier_id=SUPPLIER_ID,
        item="apple",
        quantity=payload.quantity,
        amount_cents=payload.amount_cents,
        eta_iso=_eta_iso(DEFAULT_ETA_MINUTES),
    )
    _orders[payload.idempotency_key] = response
    return response


@app.get("/orders", response_model=list[OrderResponse])
def list_orders() -> list[OrderResponse]:
    return list(_orders.values())


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


def _find_order_by_proposal(proposal_id: str) -> OrderResponse | None:
    for order in _orders.values():
        if order.proposal_id == proposal_id:
            return order
    return None


def _money(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _basket_page(
    *,
    proposal_id: str,
    item: str,
    quantity: int,
    supplier_id: str,
    amount_cents: int,
    idempotency_key: str,
) -> str:
    order = _find_order_by_proposal(proposal_id)
    paid = order is not None
    title = "Payment processed" if paid else "Waiting for Pico approval"
    copy = (
        "Sponge paid the Gateway order route. Supplier confirmation is locked."
        if paid
        else "The supplier basket is staged. Sponge payment only runs after the Pico restock key is pressed."
    )
    order_id = order.supplier_order_id if order else "pending"
    eta = order.eta_iso if order else f"{DEFAULT_ETA_MINUTES} min after approval"
    state = "paid" if paid else "waiting"
    item_label = item.strip().lower().rstrip("s") or "apple"
    safe_proposal = escape(proposal_id or "pending proposal")
    safe_item = escape(item_label)
    safe_supplier = escape(supplier_id)
    safe_idempotency = escape(idempotency_key or "pending")
    proposal_json = json.dumps(proposal_id)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Supplier Basket - {SUPPLIER_NAME}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f8f3;
        --ink: #152018;
        --muted: #627064;
        --paper: #ffffff;
        --line: #dfe7dd;
        --apple: #d93634;
        --leaf: #247a45;
        --amber: #d59722;
        --blue: #3275a8;
        --shadow: 0 22px 70px rgba(29, 46, 33, 0.16);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-width: 320px;
        background:
          radial-gradient(circle at 86% 12%, rgba(217, 54, 52, 0.12), transparent 24rem),
          linear-gradient(135deg, #f6f8f3 0%, #eaf3e9 100%);
        color: var(--ink);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      body[data-state="paid"] {{
        background:
          radial-gradient(circle at 86% 12%, rgba(36, 122, 69, 0.16), transparent 24rem),
          linear-gradient(135deg, #f6f8f3 0%, #e9f4ef 100%);
      }}
      main {{
        width: min(1120px, 100%);
        margin: 0 auto;
        padding: 28px;
      }}
      header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 22px;
      }}
      .brand {{
        display: flex;
        align-items: center;
        gap: 14px;
      }}
      .mark {{
        display: grid;
        width: 54px;
        height: 54px;
        place-items: center;
        border-radius: 14px;
        background: var(--apple);
        box-shadow: 0 12px 30px rgba(217, 54, 52, 0.22);
      }}
      h1, h2, p {{ margin: 0; }}
      h1 {{
        font-size: clamp(1.8rem, 4vw, 2.9rem);
        line-height: 1;
      }}
      .tagline {{
        color: var(--muted);
        font-weight: 750;
        margin-top: 6px;
      }}
      .status-pill {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        border: 1px solid rgba(213, 151, 34, 0.34);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.75);
        color: #835512;
        font-size: 0.92rem;
        font-weight: 850;
        padding: 10px 14px;
      }}
      body[data-state="paid"] .status-pill {{
        border-color: rgba(36, 122, 69, 0.28);
        color: #14552f;
      }}
      .dot {{
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: var(--amber);
        box-shadow: 0 0 0 5px rgba(213, 151, 34, 0.15);
      }}
      body[data-state="paid"] .dot {{
        background: var(--leaf);
        box-shadow: 0 0 0 5px rgba(36, 122, 69, 0.15);
      }}
      .basket-shell {{
        display: grid;
        grid-template-columns: minmax(320px, 0.85fr) minmax(0, 1.15fr);
        gap: 18px;
        align-items: stretch;
      }}
      .receipt, .basket-card {{
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.9);
        box-shadow: var(--shadow);
      }}
      .receipt {{
        display: grid;
        gap: 18px;
        align-content: start;
        padding: 26px;
      }}
      .receipt h2 {{
        font-size: 1.05rem;
      }}
      .receipt dl {{
        display: grid;
        gap: 11px;
        margin: 0;
      }}
      .line {{
        display: flex;
        justify-content: space-between;
        gap: 18px;
        border-bottom: 1px solid var(--line);
        padding-bottom: 11px;
      }}
      .line dt {{
        color: var(--muted);
        font-weight: 780;
      }}
      .line dd {{
        margin: 0;
        font-weight: 890;
        text-align: right;
      }}
      .total {{
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 16px;
        border-radius: 14px;
        background: #f1f8f1;
        padding: 18px;
      }}
      .total span {{
        color: var(--muted);
        font-size: 0.82rem;
        font-weight: 800;
        text-transform: uppercase;
      }}
      .total strong {{
        display: block;
        font-size: clamp(2rem, 6vw, 3.8rem);
        line-height: 0.95;
      }}
      .basket-card {{
        display: grid;
        grid-template-columns: minmax(360px, 1.18fr) minmax(220px, 0.62fr);
        min-height: 520px;
        overflow: hidden;
      }}
      .stage {{
        display: grid;
        align-content: center;
        gap: 24px;
        min-width: 0;
        padding: 40px;
      }}
      .stage h2 {{
        font-size: clamp(2.1rem, 4vw, 3.6rem);
        font-weight: 930;
        line-height: 1.02;
        overflow-wrap: anywhere;
      }}
      .stage p {{
        max-width: 540px;
        color: var(--muted);
        font-size: 1.08rem;
        font-weight: 680;
        line-height: 1.55;
        min-width: 0;
      }}
      .rail {{
        display: grid;
        gap: 10px;
        list-style: none;
        margin: 0;
        padding: 0;
      }}
      .rail li {{
        display: grid;
        grid-template-columns: 26px minmax(0, 1fr);
        gap: 10px;
        align-items: center;
        color: var(--muted);
        font-weight: 790;
        min-width: 0;
      }}
      .rail span {{
        display: grid;
        width: 26px;
        height: 26px;
        place-items: center;
        border-radius: 8px;
        background: #edf5ed;
        color: var(--leaf);
        font-size: 0.84rem;
        font-weight: 900;
      }}
      .visual {{
        display: grid;
        align-content: center;
        background:
          linear-gradient(180deg, rgba(36, 122, 69, 0.08), rgba(217, 54, 52, 0.08)),
          #eef6ed;
        padding: 30px;
      }}
      .crate {{
        display: grid;
        gap: 14px;
        border: 1px solid rgba(20, 85, 47, 0.16);
        border-radius: 18px;
        background: #fff8e8;
        padding: 22px;
        transform: rotate(-1.5deg);
        box-shadow: 0 24px 45px rgba(94, 68, 28, 0.14);
      }}
      .apples {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
      }}
      .apple {{
        aspect-ratio: 1;
        border-radius: 48% 52% 45% 55%;
        background:
          radial-gradient(circle at 35% 28%, rgba(255, 255, 255, 0.45), transparent 18%),
          var(--apple);
        box-shadow: inset -8px -12px 0 rgba(119, 0, 0, 0.12);
      }}
      .crate-label {{
        color: #6c4b18;
        font-size: 0.84rem;
        font-weight: 900;
        letter-spacing: 0.08em;
        text-align: center;
        text-transform: uppercase;
      }}
      .confirmation {{
        border: 1px solid rgba(50, 117, 168, 0.22);
        border-radius: 14px;
        background: #f0f7fb;
        color: #1f5d86;
        font-weight: 820;
        padding: 15px;
      }}
      body[data-state="paid"] .confirmation {{
        border-color: rgba(36, 122, 69, 0.24);
        background: #eef8f1;
        color: #14552f;
      }}
      @media (max-width: 900px) {{
        main {{ padding: 18px; }}
        header, .basket-shell, .basket-card {{
          grid-template-columns: 1fr;
        }}
        header {{
          align-items: flex-start;
          flex-direction: column;
        }}
        .stage {{
          padding: 28px;
        }}
      }}
    </style>
  </head>
  <body data-state="{state}">
    <main>
      <header>
        <div class="brand">
          <div class="mark" aria-hidden="true">
            <svg width="34" height="34" viewBox="0 0 40 40" role="img">
              <path fill="#fff" d="M21 9c5-6 10-4 11-4-1 7-6 10-11 9v-5Z"/>
              <path fill="#fff" d="M19 13c-4-5-13-2-13 8 0 9 6 15 14 15s14-6 14-15c0-10-9-13-13-8h-2Z"/>
            </svg>
          </div>
          <div>
            <h1>Supplier Basket</h1>
            <p class="tagline">{SUPPLIER_NAME}</p>
          </div>
        </div>
        <div class="status-pill"><span class="dot"></span><span id="status-pill">{escape(title)}</span></div>
      </header>

      <section class="basket-shell" aria-label="Restock basket">
        <aside class="receipt">
          <h2>Apple restock quote</h2>
          <dl>
            <div class="line"><dt>Proposal</dt><dd>{safe_proposal}</dd></div>
            <div class="line"><dt>Item</dt><dd>{safe_item}</dd></div>
            <div class="line"><dt>Quantity</dt><dd>{quantity}</dd></div>
            <div class="line"><dt>Supplier</dt><dd>{safe_supplier}</dd></div>
            <div class="line"><dt>Delivery ETA</dt><dd id="eta">{escape(eta)}</dd></div>
            <div class="line"><dt>Idempotency</dt><dd>{safe_idempotency}</dd></div>
          </dl>
          <div class="total">
            <span>Total due after Pico approval</span>
            <strong>{_money(amount_cents)}</strong>
          </div>
          <div class="confirmation" id="confirmation">Supplier order: {escape(order_id)}</div>
        </aside>

        <article class="basket-card">
          <div class="stage">
            <h2 id="status-title">{escape(title)}</h2>
            <p id="status-copy">{escape(copy)}</p>
            <ol class="rail">
              <li><span>1</span>Basket staged by the restock agent</li>
              <li><span>2</span>Operator email sent with cost and ETA</li>
              <li><span>3</span>Pico restock key approves payment</li>
              <li><span>4</span>Sponge pays POST /orders and supplier confirms</li>
            </ol>
          </div>
          <div class="visual" aria-hidden="true">
            <div class="crate">
              <div class="apples">
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
              </div>
              <div class="crate-label">{quantity} apples staged</div>
            </div>
          </div>
        </article>
      </section>
    </main>
    <script>
      const proposalId = {proposal_json};
      function formatEta(value) {{
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleTimeString([], {{ hour: "numeric", minute: "2-digit" }});
      }}
      async function refreshOrder() {{
        if (!proposalId) return;
        const response = await fetch("/orders", {{ headers: {{ "Accept": "application/json" }} }});
        if (!response.ok) return;
        const orders = await response.json();
        const order = orders.find((candidate) => candidate.proposal_id === proposalId);
        if (!order) return;
        document.body.dataset.state = "paid";
        document.querySelector("#status-pill").textContent = "Payment processed";
        document.querySelector("#status-title").textContent = "Payment processed";
        document.querySelector("#status-copy").textContent = "Sponge paid the Gateway order route. Supplier confirmation is locked.";
        document.querySelector("#confirmation").textContent = `Supplier order: ${{order.supplier_order_id}}`;
        document.querySelector("#eta").textContent = formatEta(order.eta_iso);
      }}
      window.setInterval(refreshOrder, 1200);
      void refreshOrder();
    </script>
  </body>
</html>"""


def _supplier_page() -> str:
    recent_orders = list(_orders.values())[-4:]
    order_rows = "\n".join(_order_row(order) for order in recent_orders)
    if not order_rows:
        order_rows = (
            '<li class="order empty-order">'
            "<span>No paid restock orders yet</span>"
            "<small>Waiting for Fruit Market approval</small>"
            "</li>"
        )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{SUPPLIER_NAME}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f7f8f2;
        --ink: #172018;
        --muted: #5f6c61;
        --line: #dfe6dc;
        --leaf: #247a45;
        --leaf-dark: #14552f;
        --apple: #d7332f;
        --gold: #dba834;
        --paper: #ffffff;
        --mist: #eef6ed;
        --shadow: 0 20px 60px rgba(33, 48, 36, 0.13);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-width: 320px;
        background:
          radial-gradient(circle at 10% 5%, rgba(219, 168, 52, 0.16), transparent 28rem),
          linear-gradient(135deg, #f7f8f2 0%, #edf4eb 100%);
        color: var(--ink);
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        width: min(1180px, 100%);
        margin: 0 auto;
        padding: 28px;
      }}
      header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 26px;
      }}
      .brand {{
        display: flex;
        align-items: center;
        gap: 14px;
      }}
      .mark {{
        display: grid;
        width: 56px;
        height: 56px;
        place-items: center;
        border-radius: 14px;
        background: var(--apple);
        box-shadow: 0 12px 30px rgba(215, 51, 47, 0.24);
      }}
      h1, h2, p {{ margin: 0; }}
      h1 {{
        font-size: clamp(1.8rem, 4vw, 3rem);
        line-height: 1;
      }}
      .tagline {{
        color: var(--muted);
        font-weight: 700;
        margin-top: 5px;
      }}
      .status {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        border: 1px solid rgba(36, 122, 69, 0.28);
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.72);
        color: var(--leaf-dark);
        font-weight: 850;
        padding: 10px 14px;
      }}
      .demo-link {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 1px solid rgba(215, 51, 47, 0.24);
        border-radius: 10px;
        background: var(--apple);
        color: #fff;
        font-weight: 850;
        padding: 12px 16px;
        text-decoration: none;
        width: fit-content;
      }}
      .dot {{
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: var(--leaf);
        box-shadow: 0 0 0 5px rgba(36, 122, 69, 0.12);
      }}
      .grid {{
        display: grid;
        grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
        gap: 18px;
        align-items: stretch;
      }}
      .hero, .panel {{
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.86);
        box-shadow: var(--shadow);
      }}
      .hero {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(280px, 0.75fr);
        gap: 18px;
        min-height: 480px;
        overflow: hidden;
      }}
      .hero-copy {{
        display: grid;
        align-content: center;
        gap: 24px;
        padding: 42px;
      }}
      .hero-copy h2 {{
        max-width: 620px;
        font-size: clamp(2.4rem, 7vw, 5.1rem);
        font-weight: 900;
        line-height: 0.94;
      }}
      .hero-copy p {{
        max-width: 540px;
        color: var(--muted);
        font-size: 1.08rem;
        font-weight: 650;
        line-height: 1.55;
      }}
      .stats {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }}
      .stat {{
        border: 1px solid var(--line);
        border-radius: 10px;
        background: var(--paper);
        padding: 14px;
      }}
      .stat strong {{
        display: block;
        font-size: 1.35rem;
        line-height: 1;
      }}
      .stat span {{
        display: block;
        color: var(--muted);
        font-size: 0.83rem;
        font-weight: 800;
        margin-top: 6px;
      }}
      .visual {{
        display: grid;
        align-content: center;
        background:
          linear-gradient(180deg, rgba(36, 122, 69, 0.08), rgba(215, 51, 47, 0.08)),
          var(--mist);
        padding: 30px;
      }}
      .crate {{
        display: grid;
        gap: 14px;
        border: 1px solid rgba(20, 85, 47, 0.16);
        border-radius: 18px;
        background: #fff8e8;
        padding: 22px;
        transform: rotate(-1.5deg);
        box-shadow: 0 24px 45px rgba(94, 68, 28, 0.14);
      }}
      .apples {{
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
      }}
      .apple {{
        aspect-ratio: 1;
        border-radius: 48% 52% 45% 55%;
        background:
          radial-gradient(circle at 35% 28%, rgba(255, 255, 255, 0.45), transparent 18%),
          var(--apple);
        box-shadow: inset -8px -12px 0 rgba(119, 0, 0, 0.12);
      }}
      .crate-label {{
        color: #6c4b18;
        font-size: 0.84rem;
        font-weight: 900;
        letter-spacing: 0.08em;
        text-align: center;
        text-transform: uppercase;
      }}
      .side {{
        display: grid;
        gap: 18px;
      }}
      .panel {{
        padding: 22px;
      }}
      .panel h2 {{
        font-size: 1.1rem;
        margin-bottom: 14px;
      }}
      .endpoint {{
        border: 1px solid rgba(36, 122, 69, 0.22);
        border-radius: 12px;
        background: #f1f8f1;
        color: var(--leaf-dark);
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 0.9rem;
        font-weight: 850;
        overflow-wrap: anywhere;
        padding: 14px;
      }}
      .orders {{
        display: grid;
        gap: 10px;
        list-style: none;
        margin: 0;
        padding: 0;
      }}
      .order {{
        display: grid;
        gap: 6px;
        border: 1px solid var(--line);
        border-radius: 12px;
        background: var(--paper);
        padding: 13px;
      }}
      .order span {{
        display: flex;
        justify-content: space-between;
        gap: 14px;
        font-weight: 850;
      }}
      .order small {{
        color: var(--muted);
        font-weight: 700;
      }}
      .empty-order {{
        border-style: dashed;
      }}
      .flow {{
        display: grid;
        gap: 8px;
        color: var(--muted);
        font-size: 0.94rem;
        font-weight: 760;
        line-height: 1.4;
        margin: 0;
        padding-left: 20px;
      }}
      @media (max-width: 900px) {{
        main {{ padding: 18px; }}
        header, .grid, .hero {{
          grid-template-columns: 1fr;
        }}
        header {{
          align-items: flex-start;
          flex-direction: column;
        }}
        .hero-copy {{
          padding: 28px;
        }}
        .stats {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div class="brand">
          <div class="mark" aria-hidden="true">
            <svg width="34" height="34" viewBox="0 0 40 40" role="img">
              <path fill="#fff" d="M21 9c5-6 10-4 11-4-1 7-6 10-11 9v-5Z"/>
              <path fill="#fff" d="M19 13c-4-5-13-2-13 8 0 9 6 15 14 15s14-6 14-15c0-10-9-13-13-8h-2Z"/>
            </svg>
          </div>
          <div>
            <h1>{SUPPLIER_NAME}</h1>
            <p class="tagline">Wholesale apples for autonomous market restocks</p>
          </div>
        </div>
        <div class="status"><span class="dot"></span> Gateway-ready supplier</div>
      </header>

      <section class="grid" aria-label="Supplier dashboard">
        <article class="hero">
          <div class="hero-copy">
            <h2>Fresh apples, paid through Sponge.</h2>
            <p>
              Fruit Market agents quote here before payment. The paid
              <strong>POST /orders</strong> endpoint is the only route that
              should sit behind PaySponge Gateway/x402.
            </p>
            <div class="stats">
              <div class="stat"><strong>240</strong><span>apples available</span></div>
              <div class="stat"><strong>$0.50</strong><span>wholesale unit price</span></div>
              <div class="stat"><strong>30m</strong><span>delivery ETA</span></div>
            </div>
            <a class="demo-link" href="/demo-basket">Open staged apple basket</a>
          </div>
          <div class="visual" aria-hidden="true">
            <div class="crate">
              <div class="apples">
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
                <div class="apple"></div><div class="apple"></div><div class="apple"></div><div class="apple"></div>
              </div>
              <div class="crate-label">demo crate · apples</div>
            </div>
          </div>
        </article>

        <aside class="side">
          <section class="panel">
            <h2>Paid Endpoint</h2>
            <div class="endpoint">POST /orders</div>
          </section>

          <section class="panel">
            <h2>Recent Supplier Confirmations</h2>
            <ul id="orders" class="orders">{order_rows}</ul>
          </section>

          <section class="panel">
            <h2>Demo Flow</h2>
            <ol class="flow">
              <li>Fruit Market detects apples are out.</li>
              <li>Agent stages this supplier basket and emails the operator.</li>
              <li>Operator approves restock on Pico.</li>
              <li>Sponge pays this supplier endpoint.</li>
              <li>Supplier returns order ID and ETA.</li>
            </ol>
          </section>
        </aside>
      </section>
    </main>
    <script>
      const dollars = (cents) => `$${{(cents / 100).toFixed(2)}}`;
      async function refreshOrders() {{
        const response = await fetch("/orders", {{ headers: {{ "Accept": "application/json" }} }});
        if (!response.ok) return;
        const orders = await response.json();
        const list = document.querySelector("#orders");
        if (!orders.length) return;
        list.innerHTML = orders.slice(-4).reverse().map((order) => `
          <li class="order">
            <span><b>${{order.supplier_order_id}}</b><b>${{dollars(order.amount_cents)}}</b></span>
            <small>${{order.quantity}} apples · ETA ${{new Date(order.eta_iso).toLocaleTimeString([], {{ hour: "numeric", minute: "2-digit" }})}}</small>
          </li>
        `).join("");
      }}
      window.setInterval(refreshOrders, 1500);
      void refreshOrders();
    </script>
  </body>
</html>"""


def _order_row(order: OrderResponse) -> str:
    return (
        '<li class="order">'
        f"<span><b>{escape(order.supplier_order_id)}</b><b>${order.amount_cents / 100:.2f}</b></span>"
        f"<small>{order.quantity} apples · ETA {escape(order.eta_iso)}</small>"
        "</li>"
    )
