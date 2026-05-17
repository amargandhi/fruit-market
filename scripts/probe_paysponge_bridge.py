"""Read-only probe for the PaySponge wallet bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    proc = subprocess.run(
        ["node", "scripts/paysponge_wallet_bridge.mjs", "probe"],
        input="{}",
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        timeout=30,
        check=False,
    )
    output = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        print(output or "PaySponge bridge probe failed", file=sys.stderr)
        return proc.returncode
    data = json.loads(output)
    tools = data.get("tools", {})
    print(
        "PaySponge bridge OK: "
        f"submitPlan={bool(tools.get('submitPlan'))} "
        f"approvePlan={bool(tools.get('approvePlan'))} "
        f"paidFetch={bool(tools.get('paidFetch'))} "
        f"x402Fetch={bool(tools.get('x402Fetch'))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
