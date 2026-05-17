"""HTTP layer.

Phase 1 ships only the wire-format schemas. The FastAPI app and
routers themselves come from Track B.
"""

from fruit_market.api import schemas

__all__ = ["schemas"]
