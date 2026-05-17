from __future__ import annotations

from fastapi.testclient import TestClient

from fruit_market.restock.demo_supplier_app import app


def test_demo_supplier_quotes_and_idempotent_orders() -> None:
    with TestClient(app) as client:
        quote = client.post("/quotes", json={"item": "apples", "quantity": 24}).json()
        assert quote["amount_cents"] == 1200

        payload = {
            "proposal_id": "restock_1",
            "item": "apple",
            "quantity": 24,
            "supplier_id": quote["supplier_id"],
            "amount_cents": quote["amount_cents"],
            "idempotency_key": "item_1:1",
        }
        first = client.post("/orders", json=payload).json()
        second = client.post("/orders", json=payload).json()

        assert first == second
        assert first["supplier_order_id"].startswith("demo_sup_")
        assert first["eta_iso"].endswith("Z")
