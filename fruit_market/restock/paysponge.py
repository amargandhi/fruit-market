"""PaySponge adapter for the optional restock flow.

This module does not import the PaySponge SDK at module import time.
The SDK is loaded only when ``PaySpongeWalletClient`` is constructed
for an enabled staging/live restock runtime.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from fruit_market.restock.projection import RestockRecord


@dataclass(frozen=True)
class SpongePlan:
    plan_id: str
    raw: object


@dataclass(frozen=True)
class SpongePayment:
    payment_id: str
    receipt: str | None
    response: object
    raw: object


@runtime_checkable
class PaySpongeClient(Protocol):
    def submit_plan(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePlan: ...

    def approve_plan(self, plan_id: str) -> object: ...

    def paid_fetch(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePayment: ...


class PaySpongeWalletClient:
    """Thin dynamic wrapper around the Python PaySponge wallet SDK."""

    def __init__(self, *, api_key: str, preferred_chain: str = "base") -> None:
        if not api_key:
            raise ValueError("SPONGE_API_KEY is required for staging_live restock")
        module = importlib.import_module("paysponge")
        wallet_cls = _required_attr(module, "SpongeWallet")
        self._wallet = _call(_method(wallet_cls, "connect"), api_key=api_key)
        self._preferred_chain = preferred_chain

    def submit_plan(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePlan:
        method = _method(self._wallet, "submit_plan", "submitPlan")
        payload = {
            "title": f"Restock {proposal.qty} {proposal.item_name}",
            "steps": [
                {
                    "type": "paid_fetch",
                    "args": self._fetch_args(proposal, body),
                }
            ],
            "metadata": {
                "proposal_id": proposal.proposal_id,
                "payload_hash": proposal.payload_hash,
                "amount_cents": proposal.amount_cents,
            },
        }
        raw = _call(method, payload)
        return SpongePlan(plan_id=_extract_id(raw, "plan_id", "planId", "id"), raw=raw)

    def approve_plan(self, plan_id: str) -> object:
        method = _method(self._wallet, "approve_plan", "approvePlan")
        return _call(method, plan_id)

    def paid_fetch(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePayment:
        method = _method(self._wallet, "paid_fetch", "paidFetch", "x402_fetch", "x402Fetch")
        raw = _call(method, self._fetch_args(proposal, body))
        response = _extract_response(raw)
        return SpongePayment(
            payment_id=_extract_id(raw, "payment_id", "paymentId", "id", default=""),
            receipt=_extract_optional_str(raw, "payment_receipt", "paymentReceipt", "receipt"),
            response=response,
            raw=raw,
        )

    def _fetch_args(
        self, proposal: RestockRecord, body: dict[str, object]
    ) -> dict[str, object]:
        return {
            "url": proposal.gateway_url,
            "method": "POST",
            "body": body,
            "preferred_chain": self._preferred_chain,
            "preferredChain": self._preferred_chain,
            "chain": self._preferred_chain,
        }


class UnavailablePaySpongeClient:
    """Placeholder used when live mode is enabled but SDK setup fails."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def submit_plan(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePlan:
        raise RuntimeError(self._reason)

    def approve_plan(self, plan_id: str) -> object:
        raise RuntimeError(self._reason)

    def paid_fetch(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePayment:
        raise RuntimeError(self._reason)


def _required_attr(obj: object, name: str) -> object:
    value = getattr(obj, name, None)
    if value is None:
        raise AttributeError(f"PaySponge SDK missing {name}")
    return cast("object", value)


def _method(obj: object, *names: str) -> Callable[..., object]:
    for name in names:
        method = getattr(obj, name, None)
        if callable(method):
            return cast("Callable[..., object]", method)
    raise AttributeError(f"PaySponge SDK missing any of: {', '.join(names)}")


def _call(fn: Callable[..., object], *args: object, **kwargs: object) -> object:
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        raise RuntimeError("async PaySponge SDK methods are not supported in this sync worker")
    return result


def _extract_id(raw: object, *names: str, default: str | None = None) -> str:
    value = _extract_optional(raw, *names)
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"PaySponge response missing id field; tried {names}")
    return str(value)


def _extract_optional_str(raw: object, *names: str) -> str | None:
    value = _extract_optional(raw, *names)
    return None if value is None else str(value)


def _extract_optional(raw: object, *names: str) -> object | None:
    if isinstance(raw, dict):
        for name in names:
            if name in raw and raw[name] is not None:
                return cast("object", raw[name])
            data = raw.get("data")
            if isinstance(data, dict) and data.get(name) is not None:
                return cast("object", data[name])
    for name in names:
        value = getattr(raw, name, None)
        if value is not None:
            return cast("object", value)
    return None


def _extract_response(raw: object) -> object:
    if isinstance(raw, dict):
        for key in ("response", "data", "result", "body"):
            if key in raw:
                return cast("object", raw[key])
    return raw
