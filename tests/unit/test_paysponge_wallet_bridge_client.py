from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from fruit_market.restock.paysponge import PaySpongeWalletClient
from tests.unit.test_restock import _proposal

if TYPE_CHECKING:
    from pathlib import Path


def test_paysponge_wallet_client_uses_bridge(tmp_path: Path) -> None:
    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        """
import json
import sys

command = sys.argv[1]
payload = json.loads(sys.stdin.read() or "{}")
if command == "probe":
    print(json.dumps({"ok": True, "tools": {"paidFetch": True}}))
elif command == "submit_plan":
    print(json.dumps({"ok": True, "plan_id": "plan_test", "raw": payload}))
elif command == "approve_plan":
    print(json.dumps({"ok": True, "raw": {"approved": payload["plan_id"]}}))
elif command == "paid_fetch":
    print(json.dumps({
        "ok": True,
        "payment_id": "pay_test",
        "receipt": "receipt_test",
        "response": {"supplier_order_id": "sup_1", "eta_iso": "2026-05-17T20:00:00Z"},
        "raw": payload,
    }))
else:
    print(json.dumps({"ok": False, "error": command}))
    sys.exit(1)
""",
        encoding="utf-8",
    )
    client = PaySpongeWalletClient(
        api_key="sponge_test",
        bridge_path=bridge,
        node_bin=sys.executable,
    )
    proposal = _proposal()
    body = {"idempotency_key": "item_1:1"}

    assert client.probe()["tools"] == {"paidFetch": True}
    assert client.submit_plan(proposal, body).plan_id == "plan_test"
    assert client.approve_plan("plan_test") == {"approved": "plan_test"}
    payment = client.paid_fetch(proposal, body)
    assert payment.payment_id == "pay_test"
    assert payment.receipt == "receipt_test"
    assert payment.response == {
        "supplier_order_id": "sup_1",
        "eta_iso": "2026-05-17T20:00:00Z",
    }
