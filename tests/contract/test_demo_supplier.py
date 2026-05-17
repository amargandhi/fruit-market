from __future__ import annotations

from fastapi.testclient import TestClient

from fruit_market.restock.demo_supplier_app import app


def test_demo_supplier_home_page_is_visual() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Demo Fruit Supplier" in response.text
    assert "POST /orders" in response.text
    assert "Recent Supplier Confirmations" in response.text
    assert "Fresh apples, paid through Sponge." in response.text
    assert "emails the operator" in response.text
    assert "/demo-basket" in response.text


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
        assert first["proposal_id"] == "restock_1"
        assert first["eta_iso"].endswith("Z")


def test_demo_supplier_orders_list_updates_after_order() -> None:
    with TestClient(app) as client:
        payload = {
            "proposal_id": "restock_2",
            "item": "apple",
            "quantity": 24,
            "supplier_id": "demo_fruit_supplier",
            "amount_cents": 1200,
            "idempotency_key": "item_1:2",
        }
        created = client.post("/orders", json=payload).json()
        orders = client.get("/orders").json()
        page = client.get("/").text

        assert created in orders
        assert created["supplier_order_id"] in page


def test_demo_supplier_basket_page_updates_after_paid_order() -> None:
    with TestClient(app) as client:
        waiting = client.get(
            "/basket",
            params={
                "proposal_id": "restock_basket_1",
                "item": "apple",
                "quantity": 24,
                "supplier_id": "demo_fruit_supplier",
                "amount_cents": 1200,
                "idempotency_key": "item_1:basket",
            },
        )
        assert waiting.status_code == 200
        assert "Supplier Basket" in waiting.text
        assert "Waiting for Pico approval" in waiting.text
        assert "Total due after Pico approval" in waiting.text

        payload = {
            "proposal_id": "restock_basket_1",
            "item": "apple",
            "quantity": 24,
            "supplier_id": "demo_fruit_supplier",
            "amount_cents": 1200,
            "idempotency_key": "item_1:basket",
        }
        created = client.post("/orders", json=payload).json()
        paid = client.get("/basket", params={"proposal_id": "restock_basket_1"})

        assert "Payment processed" in paid.text
        assert created["supplier_order_id"] in paid.text


def test_demo_supplier_stable_demo_basket_route_loads() -> None:
    with TestClient(app) as client:
        response = client.get("/demo-basket")

    assert response.status_code == 200
    assert "Supplier Basket" in response.text
    assert "restock_video_demo" in response.text
    assert "Waiting for Pico approval" in response.text


def test_demo_supplier_stable_demo_basket_can_reset() -> None:
    with TestClient(app) as client:
        payload = {
            "proposal_id": "restock_video_demo",
            "item": "apple",
            "quantity": 24,
            "supplier_id": "demo_fruit_supplier",
            "amount_cents": 1200,
            "idempotency_key": "item_apple:video_demo",
        }
        created = client.post("/orders", json=payload).json()
        paid = client.get("/demo-basket").text
        reset = client.get("/demo-basket?reset=1").text

        assert created["supplier_order_id"] in paid
        assert "Supplier order: pending" in reset
        assert "Waiting for Pico approval" in reset
