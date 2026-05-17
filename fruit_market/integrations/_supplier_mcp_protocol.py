"""Supplier-discovery MCP client Protocol.

Used by the (stretch) restock agent to find a vendor for a given
item, get a quote, and place an order paid for via a Sponge payment
token.

Kept on its own MCP server so the customer brain can't call it —
only the restock agent can. Phase 1 ships the Protocol only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SupplierQuote:
    supplier_id: str
    supplier_name: str
    item_name: str
    qty_available: int
    unit_price_cents: int
    eta_minutes: int


@dataclass(frozen=True)
class SupplierOrder:
    supplier_order_id: str
    supplier_id: str
    item_name: str
    qty: int
    eta_minutes: int


@runtime_checkable
class SupplierMCPClient(Protocol):
    def find_supplier(self, item_name: str, qty: int) -> SupplierQuote | None: ...

    def place_order(
        self,
        supplier_id: str,
        payment_token: str,
        item_name: str,
        qty: int,
    ) -> SupplierOrder: ...
