from __future__ import annotations

from typing import TYPE_CHECKING

from fruit_market.brain import gemini
from fruit_market.brain.gemini import DEFAULT_GEMINI_MODEL, DEFAULT_GEMINI_THINKING_LEVEL

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_customer_brain_defaults_to_gemini_flash_lite() -> None:
    assert DEFAULT_GEMINI_MODEL == "gemini-3.1-flash-lite"


def test_customer_brain_defaults_to_low_thinking_for_latency() -> None:
    assert DEFAULT_GEMINI_THINKING_LEVEL == "low"


def test_invalid_thinking_level_falls_back_to_low(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "turbo")

    assert gemini._gemini_thinking_level() == "low"
