# Fruit Market — developer entry points.
# All targets assume `uv` is installed (https://docs.astral.sh/uv/).

.PHONY: install install-vision check test check-sponsors dev app pico restock-supplier restock-tunnel restock-paysponge-probe restock-live-smoke clean

# Install runtime + dev deps. Idempotent.
install:
	uv sync --extra dev

# Same, plus the Mac-only vision stack (mlx-vlm + opencv).
install-vision:
	uv sync --extra dev --extra vision

# Static checks: lint + type check. Run before every commit.
check:
	uv run ruff check fruit_market scripts tests
	uv run mypy fruit_market

# Run the test suite (excludes real-hardware / real-network tests).
test:
	uv run pytest -m "not real_model and not real_sponsors"

# Ping all 5 sponsor APIs using credentials from .env. Exits 0 iff
# every endpoint returns 200. Safe to run any time.
check-sponsors:
	uv run python scripts/check_sponsors.py

# Run the FastAPI app with reload. Lands in Phase 2 (Track B).
dev:
	uv run uvicorn fruit_market.api.app:app --reload --port 8000

# Rebuild the macOS continuous-capture daemon (.app bundle). Also
# busts Finder + Dock icon caches so the brand icon shows up on
# the next launch without a logout/reboot. Run this after any
# change to apps/fm-camera/Sources/, Info.plist, or Resources/.
app:
	bash apps/fm-camera/build.sh

# Run the Pico USB bridge sidecar — polls /api/state every 1 s and
# forwards Pico keypad presses to /api/pico/action. Set PICO_PORT
# if auto-detect doesn't find the /dev/cu.usbmodem* device.
pico:
	uv run python -m fruit_market.hardware.pico_bridge --api http://127.0.0.1:8000

# Run the staging supplier API. Put this behind PaySponge Gateway/x402
# for the optional restock demo.
restock-supplier:
	uv run uvicorn fruit_market.restock.demo_supplier_app:app --port 8001

# Public HTTPS tunnel for the staging supplier. Use the printed
# https://*.trycloudflare.com URL as the Gateway upstream/API URL.
restock-tunnel:
	cloudflared tunnel --url http://localhost:8001

# Read-only check that the installed PaySponge SDK exposes the wallet
# tools the restock bridge needs. Loads SPONGE_API_KEY from .env.
restock-paysponge-probe:
	uv run python scripts/probe_paysponge_bridge.py

# Makes a real paid Gateway call; only run after RESTOCK_SUPPLIER_GATEWAY_URL
# points at the PaySponge x402 /orders URL.
restock-live-smoke:
	FRUITMARKET_RUN_REAL_SPONGE=1 uv run pytest -q tests/contract/test_real_restock_paysponge.py

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
