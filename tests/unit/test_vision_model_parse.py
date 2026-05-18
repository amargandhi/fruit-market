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
    _LOC_TOKEN_RE,
    COUNT_PROMPT,
    DETECT_PROMPT,
    PaliGemmaCounter,
    _coerce_text,
    _iou,
    _nms,
    _parse_loc_boxes,
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
    assert DETECT_PROMPT.startswith("<image>")


def test_detect_loc_token_regex_counts_three_bananas() -> None:
    """``detect {noun}`` returns 4 ``<loc####>`` tokens per detected
    instance, followed by the noun. We count instances by counting
    location-token groups and dividing by 4."""

    response = (
        "<loc0123><loc0456><loc0789><loc0987> banana ;"
        " <loc0145><loc0470><loc0810><loc0995> banana ;"
        " <loc0250><loc0500><loc0900><loc1010> banana"
    )
    tokens = _LOC_TOKEN_RE.findall(response)
    assert len(tokens) == 12
    assert len(tokens) // 4 == 3


def test_detect_loc_token_regex_returns_zero_on_empty_response() -> None:
    """Empty response = the model saw nothing of that noun."""

    assert _LOC_TOKEN_RE.findall("") == []
    assert _LOC_TOKEN_RE.findall("no bananas") == []


def test_parse_loc_boxes_extracts_quartets() -> None:
    """Three quartets in the response → three (y0,x0,y1,x1) tuples."""

    response = (
        "<loc0010><loc0020><loc0500><loc0600> banana ;"
        " <loc0050><loc0060><loc0550><loc0650> banana"
    )
    boxes = _parse_loc_boxes(response)
    assert len(boxes) == 2
    assert boxes[0] == (10, 20, 500, 600)
    assert boxes[1] == (50, 60, 550, 650)


def test_parse_loc_boxes_swaps_inverted_coordinates() -> None:
    """If PaliGemma emits the box with y1<y0, we swap so it's valid."""

    response = "<loc0500><loc0600><loc0010><loc0020> apple"
    boxes = _parse_loc_boxes(response)
    assert boxes == [(10, 20, 500, 600)]


def test_iou_zero_for_disjoint_boxes() -> None:
    a = (0, 0, 100, 100)
    b = (200, 200, 300, 300)
    assert _iou(a, b) == 0.0


def test_iou_one_for_identical_boxes() -> None:
    box = (10, 20, 110, 120)
    assert _iou(box, box) == 1.0


def test_iou_halfway_overlap() -> None:
    """100×100 boxes overlapping by a 100×50 strip = 5000 inter, 15000 union."""

    a = (0, 0, 100, 100)
    b = (50, 0, 150, 100)
    assert abs(_iou(a, b) - (5000 / 15000)) < 1e-6


def test_nms_collapses_overlapping_boxes_for_same_fruit() -> None:
    """Two near-identical boxes (PaliGemma sometimes emits twins
    for one instance) collapse to one — the real fix for inflated
    counts on borderline scenes."""

    boxes = [
        (10, 10, 100, 100),   # the "real" detection
        (12, 12, 102, 102),   # near-duplicate, IoU > 0.5 → suppressed
        (200, 200, 290, 290), # genuinely separate fruit
    ]
    kept = _nms(boxes, iou_threshold=0.5)
    assert len(kept) == 2
    assert (200, 200, 290, 290) in kept


def test_nms_keeps_distinct_nearby_instances() -> None:
    """Two fruits sitting close together (IoU below threshold)
    must both survive — we never want to count two real fruits
    as one."""

    boxes = [
        (10, 10, 100, 100),
        (10, 110, 100, 200),  # adjacent, not overlapping
    ]
    kept = _nms(boxes, iou_threshold=0.5)
    assert len(kept) == 2


def test_default_mode_is_detect() -> None:
    """The default count mode should be detect (more accurate on
    clustered scenes). Operators can opt out via env."""

    counter = PaliGemmaCounter(model_id="dummy-model-id")
    snap = counter.status()
    assert snap["mode"] == "detect"


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
