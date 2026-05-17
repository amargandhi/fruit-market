"""``PaliGemmaCounter`` — non-MLX-touching unit tests.

These exercise the pure-Python parts of the model wrapper (response
coercion, status surface, async warmup lifecycle). They never load
MLX or call the model. The real-model smoke test lives behind the
``real_model`` marker.
"""

from __future__ import annotations

import threading
import time

from fruit_market.vision.model import (
    COUNT_PROMPT,
    PaliGemmaCounter,
    _coerce_text,
)


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


def test_count_prompt_starts_with_image_token() -> None:
    """Regression: PaliGemma 2's processor expects an explicit
    <image> token. Without it, newer transformers builds drop to a
    slower path and emit a warning. The prior validated build set
    this prefix; we match it."""

    assert COUNT_PROMPT.startswith("<image>")


def test_status_before_load_reports_unloaded() -> None:
    counter = PaliGemmaCounter(model_id="dummy-model-id")
    snap = counter.status()
    assert snap["loaded"] is False
    assert snap["model_id"] == "dummy-model-id"
    assert snap["engine_load_s"] is None
    assert snap["warmup_in_flight"] is False
    assert snap["last_raw_response"] is None


def test_warmup_async_returns_immediately_and_marks_in_flight() -> None:
    """``warmup_async`` must return faster than the load takes. We
    fake the load by replacing ``_load_locked`` with a slow no-op
    and confirm the caller is unblocked while the daemon thread
    works."""

    counter = PaliGemmaCounter(model_id="dummy-model-id")

    loaded = threading.Event()

    def slow_fake_load() -> None:
        time.sleep(0.1)
        # Mark the model as "loaded" so the warmup thread exits its
        # branch cleanly (mirrors the real _load_locked side effects).
        counter._model = object()
        counter._processor = object()
        counter._engine_load_s = 0.1
        loaded.set()

    counter._load_locked = slow_fake_load  # type: ignore[assignment]

    started = time.monotonic()
    counter.warmup_async()
    returned_at = time.monotonic() - started

    # Should return in <50 ms even though the fake load takes 100 ms.
    assert returned_at < 0.05, f"warmup_async blocked for {returned_at*1000:.1f}ms"

    snap_during = counter.status()
    assert snap_during["warmup_in_flight"] is True

    # Wait for the background work to finish.
    assert loaded.wait(timeout=1.0), "background warmup never completed"
    # Give the thread a moment to exit so .is_alive() flips.
    time.sleep(0.05)

    snap_after = counter.status()
    assert snap_after["loaded"] is True
    assert snap_after["warmup_in_flight"] is False
    assert snap_after["engine_load_s"] == 0.1


def test_warmup_async_is_idempotent_while_loading() -> None:
    """Calling warmup_async repeatedly while a load is in flight
    must not spawn extra threads."""

    counter = PaliGemmaCounter(model_id="dummy-model-id")
    started_count = 0
    started_lock = threading.Lock()

    def slow_fake_load() -> None:
        nonlocal started_count
        with started_lock:
            started_count += 1
        time.sleep(0.1)
        counter._model = object()
        counter._processor = object()
        counter._engine_load_s = 0.1

    counter._load_locked = slow_fake_load  # type: ignore[assignment]

    counter.warmup_async()
    counter.warmup_async()
    counter.warmup_async()

    # Wait for whichever thread is in flight to finish.
    time.sleep(0.2)

    assert started_count == 1, (
        f"warmup_async spawned {started_count} loads instead of 1"
    )
