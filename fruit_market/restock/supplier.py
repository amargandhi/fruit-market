"""Supplier-side helpers for the optional restock flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx


@dataclass(frozen=True)
class RestockQuote:
    supplier_id: str
    supplier_name: str
    item_name: str
    qty: int
    unit_price_cents: int
    eta_minutes: int
    gateway_url: str

    @property
    def amount_cents(self) -> int:
        return self.unit_price_cents * self.qty


@dataclass(frozen=True)
class SupplierConfirmation:
    supplier_order_id: str
    eta_iso: str
    raw: dict[str, object]


@runtime_checkable
class SupplierQuoteClient(Protocol):
    def quote(self, item_name: str, qty: int) -> RestockQuote: ...


class StaticSupplierClient:
    """Deterministic demo supplier used when no staging API is configured."""

    def __init__(self, gateway_url: str = "") -> None:
        self._gateway_url = gateway_url

    def quote(self, item_name: str, qty: int) -> RestockQuote:
        return RestockQuote(
            supplier_id="demo_fruit_supplier",
            supplier_name="Demo Fruit Supplier",
            item_name=item_name,
            qty=qty,
            unit_price_cents=50,
            eta_minutes=30,
            gateway_url=self._gateway_url,
        )


class HttpDemoSupplierClient:
    """Client for the small staging supplier API in ``demo_supplier_app``."""

    def __init__(
        self,
        *,
        api_base: str,
        gateway_url: str = "",
        timeout_s: float = 5.0,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._gateway_url = gateway_url
        self._timeout_s = timeout_s

    def quote(self, item_name: str, qty: int) -> RestockQuote:
        with httpx.Client(timeout=self._timeout_s) as client:
            response = client.post(
                f"{self._api_base}/quotes",
                json={"item": item_name, "quantity": qty},
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("supplier quote response was not an object")

        gateway_url = self._gateway_url or str(data.get("gateway_url") or "")
        if not gateway_url:
            gateway_url = f"{self._api_base}/orders"

        return RestockQuote(
            supplier_id=str(data.get("supplier_id") or "demo_fruit_supplier"),
            supplier_name=str(data.get("supplier_name") or "Demo Fruit Supplier"),
            item_name=str(data.get("item") or item_name),
            qty=int(data.get("quantity") or qty),
            unit_price_cents=int(data.get("unit_price_cents") or 0),
            eta_minutes=int(data.get("eta_minutes") or 0),
            gateway_url=gateway_url,
        )


def parse_supplier_confirmation(response: object) -> SupplierConfirmation:
    """Normalize the supplier response returned through PaySponge."""

    if not isinstance(response, dict):
        raise ValueError("supplier confirmation was not an object")
    supplier_order_id = response.get("supplier_order_id") or response.get("order_id")
    eta_iso = response.get("eta_iso")
    if not supplier_order_id:
        raise ValueError("supplier confirmation missing supplier_order_id")
    if not eta_iso:
        raise ValueError("supplier confirmation missing eta_iso")
    return SupplierConfirmation(
        supplier_order_id=str(supplier_order_id),
        eta_iso=str(eta_iso),
        raw=dict(response),
    )
