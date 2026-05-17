"""Pydantic IO models for every tool the customer-facing brain can call.

These are the *only* shapes the Gemini tool loop produces and
consumes. Each tool's input is what Gemini will fill in;
each output is what the tool function returns to the model.

Why a separate file from ``api/schemas.py``: HTTP envelopes change
under different pressures than LLM tool signatures (auth, paging,
versioning). Decoupling them means we can evolve the wire format
without re-prompting the model.

Naming convention: one input/output pair per tool, with the tool's
verb form (``resolve_item`` → ``ResolveItemInput`` /
``ResolveItemOutput``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ─── resolve_item ───────────────────────────────────────────────────


class ResolveItemInput(_ToolModel):
    """Caller's spoken or typed reference to an item.

    Use this when the customer says something like ``"do you have any
    nanas?"`` — the brain calls ``resolve_item("nanas")`` and the
    tool returns the matching item (or ``None`` to trigger a Moss
    fallback)."""

    query: str = Field(min_length=1)


class ResolveItemOutput(_ToolModel):
    item_id: str
    name: str
    price_cents: int = Field(ge=0)
    available_count: int = Field(ge=0)


# ─── list_items ─────────────────────────────────────────────────────


class ListItemsInput(_ToolModel):
    pass


class ItemSummary(_ToolModel):
    item_id: str
    name: str
    price_cents: int = Field(ge=0)
    available_count: int = Field(ge=0)


class ListItemsOutput(_ToolModel):
    items: list[ItemSummary]


# ─── quote_order ────────────────────────────────────────────────────


class QuoteOrderInput(_ToolModel):
    item_id: str
    qty: int = Field(gt=0)


class QuoteOrderOutput(_ToolModel):
    item_id: str
    qty: int
    unit_amount_cents: int = Field(ge=0)
    total_cents: int = Field(ge=0)


# ─── reserve_order ──────────────────────────────────────────────────


class ReserveOrderInput(_ToolModel):
    item_id: str
    qty: int = Field(gt=0)
    customer_phone: str = Field(pattern=r"^\+\d{8,15}$")


class ReserveOrderOutput(_ToolModel):
    order_id: str
    total_cents: int


# ─── create_checkout ────────────────────────────────────────────────


class CreateCheckoutInput(_ToolModel):
    order_id: str


class CreateCheckoutOutput(_ToolModel):
    order_id: str
    checkout_url: str


# ─── get_inventory ──────────────────────────────────────────────────


class GetInventoryInput(_ToolModel):
    item_id: str


class GetInventoryOutput(_ToolModel):
    item_id: str
    physical_count: int = Field(ge=0)
    is_low: bool


# ─── get_venue_info ─────────────────────────────────────────────────


class GetVenueInfoInput(_ToolModel):
    pass


class GetVenueInfoOutput(_ToolModel):
    name: str
    location: str
    hours_today: str
    pickup: str


# ─── send_sms / send_imessage ──────────────────────────────────────


class SendSmsInput(_ToolModel):
    to_phone: str = Field(pattern=r"^\+\d{8,15}$")
    body: str = Field(min_length=1, max_length=1600)


class SendSmsOutput(_ToolModel):
    message_id: str


class SendImessageInput(_ToolModel):
    to_phone: str = Field(pattern=r"^\+\d{8,15}$")
    body: str = Field(min_length=1, max_length=10_000)


class SendImessageOutput(_ToolModel):
    message_id: str
