# Fruit Market — developer entry points.
# All targets assume `uv` is installed (https://docs.astral.sh/uv/).

.PHONY: install install-vision check test check-sponsors dev clean

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

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
