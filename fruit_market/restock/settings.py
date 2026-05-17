"""Settings for the optional restock feature."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal


PaymentMode = Literal["disabled", "staging_live"]


@dataclass(frozen=True)
class RestockSettings:
    enabled: bool = False
    payment_mode: PaymentMode = "disabled"
    require_pico_approval: bool = True
    require_sponge_plan_approval: bool = True
    max_order_cents: int = 1500
    max_hour_cents: int = 3000
    max_day_cents: int = 5000
    default_quantity: int = 24
    proposal_ttl_seconds: int = 600
    supplier_api_base: str = ""
    supplier_public_base: str = ""
    supplier_gateway_url: str = ""
    operator_email: str = ""
    operator_phone: str = ""
    sponge_api_key: str = ""
    sponge_preferred_chain: str = "base"

    @classmethod
    def from_env(cls) -> RestockSettings:
        supplier_api_base = os.environ.get("RESTOCK_SUPPLIER_API_BASE", "").rstrip("/")
        supplier_public_base = (
            os.environ.get("RESTOCK_SUPPLIER_PUBLIC_BASE", "").strip().rstrip("/")
            or supplier_api_base
        )
        operator_email = (
            os.environ.get("RESTOCK_OPERATOR_EMAIL", "").strip()
            or os.environ.get("OPERATOR_EMAIL", "").strip()
        )
        operator_phone = (
            os.environ.get("RESTOCK_OPERATOR_PHONE", "").strip()
            or os.environ.get("OPERATOR_PHONE", "").strip()
        )
        return cls(
            enabled=_env_bool("RESTOCK_ENABLED", False),
            payment_mode=_payment_mode(os.environ.get("RESTOCK_PAYMENT_MODE", "disabled")),
            require_pico_approval=_env_bool("RESTOCK_REQUIRE_PICO_APPROVAL", True),
            require_sponge_plan_approval=_env_bool(
                "RESTOCK_REQUIRE_SPONGE_PLAN_APPROVAL", True
            ),
            max_order_cents=_env_int("RESTOCK_MAX_ORDER_CENTS", 1500),
            max_hour_cents=_env_int("RESTOCK_MAX_HOUR_CENTS", 3000),
            max_day_cents=_env_int("RESTOCK_MAX_DAY_CENTS", 5000),
            default_quantity=_env_int("RESTOCK_DEFAULT_QTY", 24),
            proposal_ttl_seconds=_env_int("RESTOCK_PROPOSAL_TTL_SECONDS", 600),
            supplier_api_base=supplier_api_base,
            supplier_public_base=supplier_public_base,
            supplier_gateway_url=os.environ.get(
                "RESTOCK_SUPPLIER_GATEWAY_URL", ""
            ).strip(),
            operator_email=operator_email,
            operator_phone=operator_phone,
            sponge_api_key=os.environ.get("SPONGE_API_KEY", "").strip(),
            sponge_preferred_chain=os.environ.get(
                "RESTOCK_SPONGE_PREFERRED_CHAIN", "base"
            ).strip()
            or "base",
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _payment_mode(raw: str) -> PaymentMode:
    value = raw.strip().lower()
    if value == "staging_live":
        return "staging_live"
    return "disabled"
