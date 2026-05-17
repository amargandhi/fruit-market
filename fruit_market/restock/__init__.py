"""Optional PaySponge-backed restock feature block.

The package is intentionally isolated from the customer-facing
phone/order path. Nothing here is constructed unless
``RESTOCK_ENABLED=1`` at app startup or a test opts in directly.
"""

from fruit_market.restock.projection import RestockProjection, RestockRecord
from fruit_market.restock.runtime import RestockRuntime, build_restock_runtime
from fruit_market.restock.settings import RestockSettings

__all__ = [
    "RestockProjection",
    "RestockRecord",
    "RestockRuntime",
    "RestockSettings",
    "build_restock_runtime",
]
