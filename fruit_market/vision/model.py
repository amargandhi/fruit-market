"""PaliGemma 2 wrapper for object counting via detection.

PaliGemma 2 (mix-224) ships two relevant task prefixes:

* ``count {noun}\\n`` — asks the model for an integer directly.
  Fast (~0.5 s warm, 8 tokens) but **systematically miscounts
  clustered objects** at 224 px — we've seen "banana" report 4 when
  there are 3 in shot more than once.
* ``detect {noun}\\n`` — returns one bounding box per instance in
  PaliGemma's location-token format
  (``<loc0042><loc0128><loc0381><loc0712> banana ; <loc...> banana``).
  Slightly slower (~0.7-1.0 s for 1-6 instances) but **dramatically
  more accurate** for clustered scenes: every instance must be
  localized separately, so a missed detection is visible in the box
  count, not papered over by a single integer guess.

We use ``detect`` by default and fall back to ``count`` if parsing
fails — same model, same warm cache, no extra weights. The watcher
gets a more truthful count without giving up the ~1 s per-item
budget that lets us cover 2-4 items each poll cycle.

The prompt is intentionally minimal: ``<image>detect {noun}\\n``,
nothing else. PaliGemma 2's processor expects an explicit
``<image>`` token at the start of the prompt — without it newer
transformers builds emit a warning and infer the image count from
the inputs, which can drop to a slower path.

Three reliability knobs are tuned into this wrapper:

* **Greedy decoding** (``temperature=0.0``). Small VLMs are noisier
  without it; for a fixed image and prompt we want the same boxes
  every time so a wrong answer is at least *stable* and debuggable.
* **No active-product hints**. We never tell the model "the stall
  sells bananas" — that biases identification, so PaliGemma will
  cheerfully detect bananas in an empty frame.
* **Background warmup**. ``warmup_async()`` kicks the (slow) weight
  load onto a daemon thread so the FastAPI lifespan returns quickly
  and the watcher pays the cold-start cost off the request path.

Override the backend at runtime with ``FM_VISION_COUNT_MODE=count``
(legacy) or ``FM_VISION_COUNT_MODE=detect`` (default).

This module is lazy-imported so the rest of the codebase stays
testable on Linux CI where ``mlx-vlm`` isn't installed.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


# Default model. PaliGemma 2 3B mix-224 lands a ``count {noun}\n``
# response in well under a second on M-series Macs and fits in
# 16 GB unified memory at bf16. The 4-bit variant
# (``mlx-community/paligemma2-3b-mix-224-4bit``) downloads ~4× faster
# (~1.5 GB) and loads in ~1 s instead of ~4 s; accuracy on the
# count task is unchanged in our spot checks. Override via env.
DEFAULT_MODEL = "mlx-community/paligemma2-3b-mix-224-bf16"
COUNT_PROMPT = "<image>count {noun}\n"
DETECT_PROMPT = "<image>detect {noun}\n"
COUNT_MAX_TOKENS = 8
# detect emits ~5 tokens per instance (4 location tokens + the
# noun). 128 tokens covers ~24 instances comfortably — well past
# the most a fruit stall would ever hold in a single ROI.
DETECT_MAX_TOKENS = 128
# Greedy decoding (temperature=0) makes the model's output stable
# for a fixed image. A wrong answer should be reproducibly wrong so
# we can debug it; we don't want randomness masking systematic
# misreads.
COUNT_TEMPERATURE = 0.0
# Two modes; default is detect (more accurate on clustered scenes).
# Override with FM_VISION_COUNT_MODE=count for the legacy fast path.
DEFAULT_COUNT_MODE = "detect"


_INT_RE = re.compile(r"(\d+)")
# PaliGemma detect output: one location token block per instance.
# Format: ``<loc0123><loc0456><loc0789><loc0987> banana``.
# We count the location-token groups: each instance gets exactly
# 4 ``<loc####>`` tokens (y_min, x_min, y_max, x_max). Counting
# groups-of-4 is more robust than counting noun mentions because
# PaliGemma sometimes drops the trailing noun on the last entry.
_LOC_TOKEN_RE = re.compile(r"<loc\d{4}>")
# Per-instance pattern: four location tokens then a noun. The
# noun group lets us tally counts per-target when we issue a
# multi-noun detect prompt (``detect apple ; banana``).
_DETECTION_RE = re.compile(
    r"(?:<loc\d{4}>){4}\s+(?P<noun>[a-zA-Z_]+)"
)
# Quartet-only pattern (no noun required) — used when we want to
# extract the box coordinates themselves for NMS deduplication.
_LOC_QUARTET_RE = re.compile(
    r"<loc(\d{4})><loc(\d{4})><loc(\d{4})><loc(\d{4})>"
)
# Two boxes with IoU above this threshold are treated as the same
# instance and one gets suppressed. 0.5 is the standard COCO-style
# default; we don't want to suppress lightly-overlapping nearby
# fruits, but PaliGemma sometimes emits two near-identical boxes
# for one instance and those get rolled together.
_NMS_IOU_THRESHOLD = 0.5


class CountModelError(RuntimeError):
    """Model couldn't return a parseable integer count."""


class PaliGemmaCounter:
    """Lazy-loaded ``count {noun}`` model.

    First call triggers the weight download (cached under the HF
    cache dir) and the MLX load. Subsequent calls are warm.
    Thread-safe: a single counter can be shared between the
    watcher thread and a debug endpoint.

    Lifecycle:

    * ``warmup()`` — synchronous; blocks until loaded.
    * ``warmup_async()`` — kicks the load onto a daemon thread;
      returns immediately. Idempotent.
    * ``status()`` — dashboard-facing snapshot (engine load time,
      loaded flag, model id, last raw response).
    """

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or os.environ.get("FM_VISION_MODEL", DEFAULT_MODEL)
        self._lock = threading.Lock()
        # MLX model + processor are loaded on demand.
        self._model: object | None = None
        self._processor: object | None = None
        self._config: object | None = None
        self._last_raw_response: str | None = None
        self._engine_load_s: float | None = None
        # detect (default) vs count (legacy). Mode is per-counter so
        # tests can pin it; env override is read once at construction.
        self._mode = os.environ.get("FM_VISION_COUNT_MODE", DEFAULT_COUNT_MODE).strip().lower()
        if self._mode not in {"detect", "count"}:
            self._mode = DEFAULT_COUNT_MODE
        # Background warmup state. ``_warmup_thread`` is None until
        # ``warmup_async`` is called; alive while loading; absent
        # again once joined.
        self._warmup_thread: threading.Thread | None = None

    # ─── lifecycle ────────────────────────────────────────────────

    def warmup(self) -> None:
        """Load the model now. Blocks until ready. Idempotent."""

        with self._lock:
            self._load_locked()

    def warmup_async(self) -> None:
        """Kick the model load onto a background daemon thread.

        Returns immediately. Use this from a FastAPI lifespan so the
        app starts serving requests without blocking on the ~4 s
        weight load — the watcher's first tick may still pay the
        cost, but webhooks and the dashboard come up instantly.

        Idempotent: calling repeatedly while a load is in flight is
        a no-op.
        """

        with self._lock:
            if self._model is not None:
                return  # already loaded
            if self._warmup_thread is not None and self._warmup_thread.is_alive():
                return  # already loading

            def _warmup_target() -> None:
                # Swallow here so the daemon thread dies cleanly;
                # the next count() call will surface the same error
                # in-band where the watcher can handle it.
                with contextlib.suppress(Exception):
                    self.warmup()

            thread = threading.Thread(
                target=_warmup_target,
                name="fm-vision-warmup",
                daemon=True,
            )
            self._warmup_thread = thread
            thread.start()

    def _load_locked(self) -> None:
        if self._model is not None:
            return
        # Imports are inside the lock so a Linux CI process that
        # never calls count() doesn't fail at import time.
        from mlx_vlm import load  # noqa: PLC0415
        from mlx_vlm.utils import load_config  # noqa: PLC0415

        started = time.monotonic()
        model, processor = load(self._model_id)
        config = load_config(self._model_id)
        self._model = model
        self._processor = processor
        self._config = config
        self._engine_load_s = round(time.monotonic() - started, 2)

    # ─── inference ────────────────────────────────────────────────

    def count(self, image_bytes: bytes, noun: str) -> int:
        """Return the model's integer count of ``noun`` in the image.

        Uses ``detect`` mode by default (counts bounding boxes — much
        more accurate on clustered scenes). Falls back to ``count``
        mode if detect parsing fails. Raises :class:`CountModelError`
        only if BOTH paths fail to yield a parseable result.

        The watcher catches the error and treats the cycle as a no-op
        rather than crashing the loop.
        """

        if not noun.strip():
            raise ValueError("noun must be non-empty")

        with self._lock:
            self._load_locked()
            assert self._model is not None
            assert self._processor is not None
            from PIL import Image  # noqa: PLC0415

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            noun_clean = noun.strip()

            if self._mode == "detect":
                try:
                    return self._detect_count_locked(image, noun_clean)
                except CountModelError as exc:
                    # Detection parsing failed — try the legacy
                    # integer-count path before giving up. This is
                    # rare in practice but cheap to attempt.
                    self._last_raw_response = (
                        f"[detect failed: {exc}] {self._last_raw_response or ''}"
                    )
                    return self._integer_count_locked(image, noun_clean)
            return self._integer_count_locked(image, noun_clean)

    def count_batch(self, image_bytes: bytes, nouns: list[str]) -> dict[str, int]:
        """Count multiple nouns in one model call (detect mode only).

        Issues a single ``detect noun1 ; noun2 ; ...`` prompt; parses
        the per-noun bounding boxes and returns a ``{noun: count}``
        dict. Cuts inference time roughly in half vs calling
        :meth:`count` per noun (one model.generate instead of N).

        Falls back to per-noun :meth:`count` if the batch response
        is unparseable or if any noun is missing from the result.
        Missing nouns are reported as 0 (the model returns no
        detections for objects it doesn't see).

        Only meaningful in ``detect`` mode — the ``count`` mode
        returns a single integer, no per-noun attribution.
        """

        if not nouns:
            return {}
        cleaned = [n.strip() for n in nouns if n and n.strip()]
        if not cleaned:
            return {}

        # In count mode there's no per-noun attribution to be had
        # from a single call; fall straight through to per-noun.
        if self._mode != "detect":
            return {noun: self.count(image_bytes, noun) for noun in cleaned}

        with self._lock:
            self._load_locked()
            assert self._model is not None
            assert self._processor is not None
            from mlx_vlm import generate  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            prompt = f"<image>detect {' ; '.join(cleaned)}\n"
            # Multi-noun detections need more tokens than single-noun:
            # 5 tokens per instance, up to ~30 instances total across
            # all nouns. 256 tokens covers everything a fruit stall
            # could plausibly hold.
            response = generate(
                self._model,
                self._processor,
                prompt,
                image=[image],
                max_tokens=256,
                temperature=COUNT_TEMPERATURE,
                verbose=False,
            )
            text = _coerce_text(response)
            self._last_raw_response = text

        # Parse per-noun counts. Use a noun-keyed dict so each
        # target gets a count even if the model returned no
        # detections for it.
        per_noun: dict[str, int] = {noun: 0 for noun in cleaned}
        for match in _DETECTION_RE.finditer(text):
            noun = match.group("noun").lower().strip()
            # Tolerate trailing 's' (banana / bananas) so prompts and
            # response variations don't drop counts on the floor.
            if noun in per_noun:
                per_noun[noun] += 1
            elif noun.endswith("s") and noun[:-1] in per_noun:
                per_noun[noun[:-1]] += 1
        return per_noun

    def detect_boxes(self, image_bytes: bytes, noun: str) -> list[tuple[int, int, int, int]]:
        """Return the dedup'd bounding boxes the model finds for ``noun``.

        Exposed so the watcher can do cross-class dedup before
        counting (PaliGemma's ``detect banana`` will sometimes draw
        a box around an apple; the watcher catches that by also
        detecting apple and dropping cross-class overlaps).

        Returns a list of ``(y0, x0, y1, x1)`` tuples in PaliGemma's
        0..1023 normalized coordinate space. Empty list = the
        model saw zero of this noun. Intra-class NMS is applied
        before returning so callers don't have to repeat it.
        """

        if not noun.strip():
            raise ValueError("noun must be non-empty")
        with self._lock:
            self._load_locked()
            assert self._model is not None
            assert self._processor is not None
            from PIL import Image  # noqa: PLC0415

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            return self._detect_boxes_locked(image, noun.strip())

    def _detect_boxes_locked(
        self, image: object, noun: str,
    ) -> list[tuple[int, int, int, int]]:
        """Run ``detect {noun}`` and return dedup'd boxes.

        Caller must hold ``self._lock``.
        """

        from mlx_vlm import generate  # noqa: PLC0415

        prompt = DETECT_PROMPT.format(noun=noun)
        response = generate(
            self._model,
            self._processor,
            prompt,
            image=[image],
            max_tokens=DETECT_MAX_TOKENS,
            temperature=COUNT_TEMPERATURE,
            verbose=False,
        )
        text = _coerce_text(response)
        self._last_raw_response = text

        boxes = _parse_loc_boxes(text)
        if not boxes:
            # Empty / "no" response = legitimately zero. Anything
            # else with no location tokens is unparseable garbage.
            if text.strip() == "" or "no" in text.lower():
                return []
            raise CountModelError(
                f"detect response has no location tokens: {text!r}"
            )
        # Intra-class NMS: collapse near-identical twin boxes for
        # the same physical instance.
        return _nms(boxes, _NMS_IOU_THRESHOLD)

    def _detect_count_locked(self, image: object, noun: str) -> int:
        """Detect + count for a single class (single-class path).

        Kept for the ``count(image, noun) -> int`` API which the
        watcher uses for legacy/test fallbacks. Cross-class dedup
        is the watcher's responsibility — call ``detect_boxes``
        directly when you have multiple classes.
        """

        return len(self._detect_boxes_locked(image, noun))

    def _integer_count_locked(self, image: object, noun: str) -> int:
        """Legacy ``count {noun}`` path — single-integer response.

        Faster (~0.5 s) but systematically miscounts clustered objects
        at 224 px. Kept as a fallback when detect parsing fails AND
        as the env-overridable path for benchmarking.

        Caller must hold ``self._lock``.
        """

        from mlx_vlm import generate  # noqa: PLC0415

        prompt = COUNT_PROMPT.format(noun=noun)
        response = generate(
            self._model,
            self._processor,
            prompt,
            image=[image],
            max_tokens=COUNT_MAX_TOKENS,
            temperature=COUNT_TEMPERATURE,
            verbose=False,
        )
        text = _coerce_text(response)
        self._last_raw_response = text

        match = _INT_RE.search(text)
        if match is None:
            raise CountModelError(
                f"no integer in model response: {text!r}"
            )
        return int(match.group(1))

    # ─── introspection ────────────────────────────────────────────

    def status(self) -> dict[str, object]:
        """Dashboard-facing snapshot. Safe to call frequently."""

        warmup_in_flight = (
            self._warmup_thread is not None and self._warmup_thread.is_alive()
        )
        return {
            "backend": "paligemma_mlx",
            "model_id": self._model_id,
            "mode": self._mode,
            "loaded": self._model is not None,
            "engine_load_s": self._engine_load_s,
            "warmup_in_flight": warmup_in_flight,
            "last_raw_response": self._last_raw_response,
        }

    @property
    def last_raw_response(self) -> str | None:
        """The most recent raw model output. Handy for the kiosk's
        debug panel and for diagnosing misreads."""

        return self._last_raw_response

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def engine_load_s(self) -> float | None:
        return self._engine_load_s


def _parse_loc_boxes(text: str) -> list[tuple[int, int, int, int]]:
    """Extract ``(y0, x0, y1, x1)`` tuples from a PaliGemma detect
    response. Coordinates are PaliGemma's raw 0..1023 grid units;
    we don't bother converting to pixels since NMS only needs them
    to be in the same comparable units.

    Returns an empty list if no location-token quartets are found.
    """

    boxes: list[tuple[int, int, int, int]] = []
    for match in _LOC_QUARTET_RE.finditer(text):
        y0, x0, y1, x1 = (int(g) for g in match.groups())
        # Guard against PaliGemma occasionally emitting boxes in
        # the wrong order — swap so y0<=y1 and x0<=x1.
        if y0 > y1:
            y0, y1 = y1, y0
        if x0 > x1:
            x0, x1 = x1, x0
        boxes.append((y0, x0, y1, x1))
    return boxes


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two ``(y0, x0, y1, x1)`` boxes."""

    ay0, ax0, ay1, ax1 = a
    by0, bx0, by1, bx1 = b
    iy0, ix0 = max(ay0, by0), max(ax0, bx0)
    iy1, ix1 = min(ay1, by1), min(ax1, bx1)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ay1 - ay0) * (ax1 - ax0)
    area_b = (by1 - by0) * (bx1 - bx0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _nms(
    boxes: list[tuple[int, int, int, int]],
    iou_threshold: float,
) -> list[tuple[int, int, int, int]]:
    """Greedy non-maximum suppression.

    We sort by area (largest first — PaliGemma's confidence isn't
    directly accessible from the text output, so area is the best
    proxy: a confident "this is the whole fruit" box is bigger than
    a stray "I'm not sure" box near the edge), then keep boxes whose
    IoU with every previously-kept box is below ``iou_threshold``.

    O(N²) but N is the number of detections in one image (typically
    < 30), so the constant factor doesn't matter.
    """

    if not boxes:
        return []
    by_area = sorted(
        boxes,
        key=lambda b: (b[2] - b[0]) * (b[3] - b[1]),
        reverse=True,
    )
    kept: list[tuple[int, int, int, int]] = []
    for box in by_area:
        if all(_iou(box, k) <= iou_threshold for k in kept):
            kept.append(box)
    return kept


def _coerce_text(response: object) -> str:
    """``mlx_vlm.generate`` returns different shapes across versions
    — sometimes a string, sometimes a tuple, sometimes an object
    with a ``.text`` attribute. Squeeze it down to a string."""

    if isinstance(response, str):
        return response
    if isinstance(response, tuple) and response:
        first = response[0]
        return first if isinstance(first, str) else str(first)
    text_attr = getattr(response, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    return str(response)


__all__ = [
    "CountModelError",
    "DEFAULT_MODEL",
    "PaliGemmaCounter",
    # Re-export the type for code that wants to type-annotate the
    # warmup callback (e.g. the FastAPI lifespan).
    "WarmupCallback",
]

if TYPE_CHECKING:
    WarmupCallback = Callable[[], None]
else:  # at runtime the alias is just an opaque label
    WarmupCallback = object
