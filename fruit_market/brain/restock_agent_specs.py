"""Tool IO models for the (stretch) restock agent.

The restock agent is a separate Gemini agent (different system
prompt, different tool set, different process) triggered by
``StockLow`` events. It uses Sponge to pay a supplier so a phone
caller can never trigger a wholesale purchase.

Phase 1 ships the shapes only. The real agent lands post-MVP.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ─── find_supplier ──────────────────────────────────────────────────


class FindSupplierInput(_ToolModel):
    item_name: str = Field(min_length=1)
    qty_needed: int = Field(gt=0)


class FindSupplierOutput(_ToolModel):
    supplier_id: str
    supplier_name: str
    unit_price_cents: int = Field(ge=0)
    eta_minutes: int = Field(ge=0)


# ─── sponge_charge ──────────────────────────────────────────────────


class SpongeChargeInput(_ToolModel):
    amount_cents: int = Field(gt=0)
    supplier_id: str
    idempotency_key: str = Field(
        min_length=8,
        description=(
            "Stable across retries. Use ``{item_id}:{stock_low_event_ts}`` "
            "so a flapping low-stock signal can't double-charge."
        ),
    )


class SpongeChargeOutput(_ToolModel):
    payment_token: str
    charged_at_iso: str


# ─── place_supplier_order ───────────────────────────────────────────


class PlaceSupplierOrderInput(_ToolModel):
    supplier_id: str
    payment_token: str
    item_name: str
    qty: int = Field(gt=0)


class PlaceSupplierOrderOutput(_ToolModel):
    supplier_order_id: str
    eta_minutes: int = Field(ge=0)
