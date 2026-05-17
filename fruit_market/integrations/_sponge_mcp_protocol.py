"""Sponge MCP client Protocol.

Sponge is exposed as a Model Context Protocol server; the restock
agent discovers its tools dynamically and calls them. We don't
import a Sponge SDK in the customer-facing brain — payments live
behind an explicit MCP boundary so a phone caller can't reach
them.

Phase 1 ships only the Protocol. The HTTP/MCP client lands when
the stretch flow is wired up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SpongeCharge:
    payment_token: str
    amount_cents: int
    supplier_id: str
    charged_at_iso: str
    idempotency_key: str


@runtime_checkable
class SpongeMCPClient(Protocol):
    """Read-and-spend on the operator's Sponge balance.

    All amounts in integer cents. ``idempotency_key`` is mandatory
    so a flapping ``StockLow`` signal can't double-charge: the
    restock agent uses ``{item_id}:{stock_low_event_ts}`` as the
    key."""

    def charge(
        self,
        amount_cents: int,
        supplier_id: str,
        idempotency_key: str,
    ) -> SpongeCharge: ...

    def list_recent_charges(self, limit: int = 50) -> list[SpongeCharge]: ...
