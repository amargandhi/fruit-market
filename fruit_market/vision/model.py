"""PaliGemma 2 wrapper for ``count <noun>`` queries.

PaliGemma 2 (mix-224) has a native task prefix ``count {noun}\\n``
that returns an integer count of the target object in the image
(~0.5 s per call on Apple Silicon with MLX-VLM). We exploit that
directly: one short generate per poll cycle, ``max_new_tokens=8``,
extract the first integer from the response.

Two reliability knobs are tuned into this wrapper:

* **Greedy decoding** (``temperature=0.0``). Small VLMs are noisier
  without it; for a fixed image and prompt we want the same integer
  every time so a wrong answer is at least *stable* and debuggable.
* **No active-product hints**. We never tell the model "the stall
  sells bananas" — that biases identification, so PaliGemma will
  cheerfully count bananas in an empty frame. The prompt is exactly
  ``count {noun}\\n`` and nothing else.

This module is lazy-imported so the rest of the codebase stays
testable on Linux CI where ``mlx-vlm`` isn't installed.
"""

from __future__ import annotations

import io
import os
import re
import threading

# Default model. PaliGemma 2 3B mix-224 lands a ``count {noun}\n``
# response in well under a second on M-series Macs and fits in
# 16 GB unified memory at bf16. Override via env.
DEFAULT_MODEL = "mlx-community/paligemma2-3b-mix-224-bf16"
COUNT_PROMPT = "count {noun}\n"
COUNT_MAX_TOKENS = 8
# Greedy decoding (temperature=0) makes the model's output stable
# for a fixed image. A wrong answer should be reproducibly wrong so
# we can debug it; we don't want randomness masking systematic
# misreads.
COUNT_TEMPERATURE = 0.0


_INT_RE = re.compile(r"(\d+)")


class CountModelError(RuntimeError):
    """Model couldn't return a parseable integer count."""


class PaliGemmaCounter:
    """Lazy-loaded ``count {noun}`` model.

    First call triggers the weight download (cached under the HF
    cache dir) and the MLX load. Subsequent calls are warm.
    Thread-safe: a single counter can be shared between the
    watcher thread and a debug endpoint.
    """

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or os.environ.get("FM_VISION_MODEL", DEFAULT_MODEL)
        self._lock = threading.Lock()
        # MLX model + processor are loaded on demand.
        self._model: object | None = None
        self._processor: object | None = None
        self._config: object | None = None
        self._last_raw_response: str | None = None

    def warmup(self) -> None:
        """Load the model now instead of on the first ``count()``
        call. Used by the FastAPI lifespan so the first customer
        call doesn't pay the cold start."""

        with self._lock:
            self._load_locked()

    def _load_locked(self) -> None:
        if self._model is not None:
            return
        # Imports are inside the lock so a Linux CI process that
        # never calls count() doesn't fail at import time.
        from mlx_vlm import load  # noqa: PLC0415
        from mlx_vlm.utils import load_config  # noqa: PLC0415

        model, processor = load(self._model_id)
        config = load_config(self._model_id)
        self._model = model
        self._processor = processor
        self._config = config

    def count(self, image_bytes: bytes, noun: str) -> int:
        """Return the model's integer count of ``noun`` in the image.

        Raises :class:`CountModelError` if the model returns no
        integer. The watcher catches this and treats the cycle as
        a no-op rather than crashing the loop.
        """

        if not noun.strip():
            raise ValueError("noun must be non-empty")

        with self._lock:
            self._load_locked()
            assert self._model is not None
            assert self._processor is not None
            from mlx_vlm import generate  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            prompt = COUNT_PROMPT.format(noun=noun.strip())
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

    @property
    def last_raw_response(self) -> str | None:
        """The most recent raw model output. Handy for the kiosk's
        debug panel and for diagnosing misreads."""

        return self._last_raw_response

    @property
    def model_id(self) -> str:
        return self._model_id


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
