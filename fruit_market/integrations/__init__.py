"""External service clients.

Phase 1 ships only Protocol stubs for the two MCP-style clients
(Sponge, supplier discovery) used by the stretch restock agent.
The HTTP clients for AgentPhone / Stripe / AgentMail land in
Track B.
"""

from fruit_market.integrations._sponge_mcp_protocol import (
    SpongeCharge,
    SpongeMCPClient,
)
from fruit_market.integrations._supplier_mcp_protocol import (
    SupplierMCPClient,
    SupplierOrder,
    SupplierQuote,
)

__all__ = [
    "SpongeCharge",
    "SpongeMCPClient",
    "SupplierMCPClient",
    "SupplierOrder",
    "SupplierQuote",
]
