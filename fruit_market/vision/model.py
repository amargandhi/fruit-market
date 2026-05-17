"""PaliGemma 2 wrapper for ``count <noun>`` queries.

PaliGemma 2 (mix-224) has a native task prefix ``count {noun}\\n``
that returns an integer count of the target object in the image
(~0.5 s per call warm on Apple Silicon with MLX-VLM). We exploit
that directly: one short generate per poll cycle,
``max_new_tokens=8``, extract the first integer from the response.

The prompt is intentionally minimal: ``<image>count {noun}\\n``,
nothing else. PaliGemma 2's processor expects an explicit
``<image>`` token at the start of the prompt — without it newer
transformers builds emit a warning and infer the image count from
the inputs, which can drop to a slower path. The explicit token is
also what the prior validated build used on the same model family.

Three reliability knobs are tuned into this wrapper:

* **Greedy decoding** (``temperature=0.0``). Small VLMs are noisier
  without it; for a fixed image and prompt we want the same integer
  every time so a wrong answer is at least *stable* and debuggable.
* **No active-product hints**. We never tell the model "the stall
  sells bananas" — that biases identification, so PaliGemma will
  cheerfully count bananas in an empty frame.
* **Background warmup**. ``warmup_async()`` kicks the (slow) weight
  load onto a daemon thread so the FastAPI lifespan returns quickly
  and the watcher pays the cold-start cost off the request path.

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

    # ─── introspection ────────────────────────────────────────────

    def status(self) -> dict[str, object]:
        """Dashboard-facing snapshot. Safe to call frequently."""

        warmup_in_flight = (
            self._warmup_thread is not None and self._warmup_thread.is_alive()
        )
        return {
            "backend": "paligemma_mlx",
            "model_id": self._model_id,
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
