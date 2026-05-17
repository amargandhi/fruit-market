"""``PaliGemmaCounter._coerce_text`` + integer extraction.

These are unit tests on the pure-Python parts of the model wrapper
— they don't load MLX or call the model. The real-model smoke test
lives behind the ``real_model`` marker.
"""

from __future__ import annotations

from fruit_market.vision.model import _coerce_text


def test_coerce_text_handles_plain_string() -> None:
    assert _coerce_text("count: 4") == "count: 4"


def test_coerce_text_handles_tuple_response() -> None:
    assert _coerce_text(("count: 6", {"meta": "ignored"})) == "count: 6"


def test_coerce_text_handles_object_with_text_attr() -> None:
    class _Resp:
        text = "count: 8"

    assert _coerce_text(_Resp()) == "count: 8"


def test_coerce_text_falls_back_to_str() -> None:
    assert _coerce_text(42) == "42"
