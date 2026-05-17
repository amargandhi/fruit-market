"""PaySponge adapter for the optional restock flow.

This module does not import the PaySponge SDK at module import time.
The SDK is loaded only when ``PaySpongeWalletClient`` is constructed
for an enabled staging/live restock runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
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
    """Wallet client used by the live restock flow.

    PaySponge's public docs currently prioritize ``@paysponge/sdk`` for
    ``submitPlan``, ``approvePlan``, and ``paidFetch``. To keep the
    Python app stable while that SDK surface evolves, this client calls a
    tiny Node bridge only when staging/live restock is enabled.
    """

    def __init__(
        self,
        *,
        api_key: str,
        preferred_chain: str = "base",
        api_base: str | None = None,
        bridge_path: str | Path | None = None,
        node_bin: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("SPONGE_API_KEY is required for staging_live restock")
        root = Path(__file__).resolve().parents[2]
        self._bridge_path = Path(
            bridge_path
            or os.environ.get("RESTOCK_PAYSPONGE_BRIDGE_PATH", "")
            or root / "scripts" / "paysponge_wallet_bridge.mjs"
        )
        self._node_bin = node_bin or os.environ.get("RESTOCK_NODE_BIN", "node")
        self._api_key = api_key
        self._api_base = api_base or os.environ.get("SPONGE_API_BASE", "")
        self._preferred_chain = preferred_chain
        self._timeout_s = timeout_s

    def submit_plan(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePlan:
        raw = self._run_bridge("submit_plan", proposal, body)
        return SpongePlan(
            plan_id=_extract_id(raw, "plan_id", "planId", "id"),
            raw=raw.get("raw", raw),
        )

    def approve_plan(self, plan_id: str) -> object:
        raw = self._run_bridge_payload("approve_plan", {"plan_id": plan_id})
        return raw.get("raw", raw)

    def paid_fetch(self, proposal: RestockRecord, body: dict[str, object]) -> SpongePayment:
        raw = self._run_bridge("paid_fetch", proposal, body)
        return SpongePayment(
            payment_id=_extract_id(raw, "payment_id", "paymentId", "id", default=""),
            receipt=_extract_optional_str(raw, "payment_receipt", "paymentReceipt", "receipt"),
            response=raw.get("response", _extract_response(raw)),
            raw=raw,
        )

    def probe(self) -> dict[str, object]:
        return self._run_bridge_payload("probe", {})

    def _run_bridge(
        self, command: str, proposal: RestockRecord, body: dict[str, object]
    ) -> dict[str, object]:
        return self._run_bridge_payload(
            command,
            {
                "proposal": _proposal_payload(proposal),
                "body": body,
                "preferred_chain": self._preferred_chain,
            },
        )

    def _run_bridge_payload(
        self, command: str, payload: dict[str, object]
    ) -> dict[str, object]:
        env = os.environ.copy()
        env["SPONGE_API_KEY"] = self._api_key
        if self._api_base:
            env["SPONGE_API_BASE"] = self._api_base
        env["RESTOCK_SPONGE_PREFERRED_CHAIN"] = self._preferred_chain
        proc = subprocess.run(
            [self._node_bin, str(self._bridge_path), command],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=self._timeout_s,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            detail = _safe_bridge_error(proc.stdout, proc.stderr)
            raise RuntimeError(f"PaySponge bridge {command} failed: {detail}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("PaySponge bridge returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("PaySponge bridge returned a non-object JSON payload")
        if data.get("ok") is False:
            raise RuntimeError(str(data.get("error") or "PaySponge bridge failed"))
        return cast("dict[str, object]", data)


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


def _proposal_payload(proposal: RestockRecord) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "item_name": proposal.item_name,
        "qty": proposal.qty,
        "amount_cents": proposal.amount_cents,
        "gateway_url": proposal.gateway_url,
        "payload_hash": proposal.payload_hash,
    }


def _safe_bridge_error(stdout: str, stderr: str) -> str:
    for candidate in (stdout, stderr):
        text = candidate.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text[-500:]
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    return "exit status without error text"
