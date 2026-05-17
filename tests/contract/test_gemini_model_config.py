from __future__ import annotations

from typing import TYPE_CHECKING

from fruit_market.api.schemas import AgentPhoneWebhookEnvelope
from fruit_market.brain import gemini, prompts
from fruit_market.brain.gemini import (
    DEFAULT_GEMINI_MAX_REMOTE_CALLS,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_THINKING_LEVEL,
)

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_customer_brain_defaults_to_gemini_flash_lite() -> None:
    assert DEFAULT_GEMINI_MODEL == "gemini-3.1-flash-lite"


def test_customer_brain_defaults_to_low_thinking_for_latency() -> None:
    assert DEFAULT_GEMINI_THINKING_LEVEL == "low"


def test_invalid_thinking_level_falls_back_to_low(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "turbo")

    assert gemini._gemini_thinking_level() == "low"


def test_customer_brain_bounds_automatic_function_calls() -> None:
    assert DEFAULT_GEMINI_MAX_REMOTE_CALLS == 6


def test_invalid_max_remote_calls_falls_back_to_default(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MAX_REMOTE_CALLS", "0")

    assert gemini._gemini_max_remote_calls() == 6


def test_system_prompt_includes_agentphone_caller_context(stub_services) -> None:  # type: ignore[no-untyped-def]
    prompt = prompts.system_prompt(stub_services, caller_phone="+15551234567")

    assert "Current caller phone: +15551234567" in prompt
    assert "Use this for reservations" in prompt


def test_agentphone_message_passes_caller_phone_to_gemini(
    monkeypatch: MonkeyPatch,
    stub_services,
) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, object] = {}

    def fake_generate_reply(
        transcript: str,
        services,
        caller_phone: str | None = None,
    ) -> str:  # type: ignore[no-untyped-def]
        seen["transcript"] = transcript
        seen["services"] = services
        seen["caller_phone"] = caller_phone
        return "reply"

    monkeypatch.setattr(gemini, "generate_reply", fake_generate_reply)
    envelope = AgentPhoneWebhookEnvelope.model_validate({"event": "agent.message"})

    reply = gemini.handle_agentphone_message(
        envelope,
        {
            "data": {
                "from": "+15551234567",
                "message": "Do you have apples?",
            }
        },
        stub_services,
    )

    assert reply == "reply"
    assert seen == {
        "transcript": "Do you have apples?",
        "services": stub_services,
        "caller_phone": "+15551234567",
    }


def test_tool_callables_use_caller_phone_when_phone_argument_is_omitted(
    stub_services,
) -> None:  # type: ignore[no-untyped-def]
    proposal = stub_services.teach.propose("These are apples, $1.00, 3 of them")
    item = stub_services.teach.confirm(proposal.id)
    callables = {
        tool.__name__: tool
        for tool in gemini._tool_callables(stub_services, caller_phone="+15551234567")
    }

    reserved = callables["reserve_order"](item.id, 1)

    assert reserved["total_cents"] == 100
    order = stub_services.orders.get(str(reserved["order_id"]))
    assert order is not None
    assert order.customer_phone == "+15551234567"
