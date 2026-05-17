from __future__ import annotations

from fastapi.testclient import TestClient

from fruit_market.api.app import app


def test_kiosk_html_and_state_endpoint_load() -> None:
    with TestClient(app) as client:
        html = client.get("/").text
        state = client.get("/api/state").json()

    assert 'id="catalog-panel"' in html
    assert 'id="orders-panel"' in html
    assert 'id="teach-panel"' in html
    assert "catalog" in state
    assert "orders" in state
