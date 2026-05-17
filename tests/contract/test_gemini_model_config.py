from __future__ import annotations

from fruit_market.brain.gemini import DEFAULT_GEMINI_MODEL


def test_customer_brain_defaults_to_gemini_flash_lite() -> None:
    assert DEFAULT_GEMINI_MODEL == "gemini-3.1-flash-lite"
