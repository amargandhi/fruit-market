"""Wire camera + model + watcher into one bundle for the FastAPI app.

This is the single integration point the HTTP layer calls from its
lifespan. It returns a :class:`VisionBundle` with the watcher
already started and the model warmup kicked off in the background.

Why a separate factory: the HTTP layer (``api/app.py``) and the
camera/model/watcher modules belong to different concerns. Without
this glue, the lifespan would have to know about backend selection,
warmup ordering, env vars, and the watcher's polling parameters —
all things that live here.

Default behavior:

* Camera backend follows ``FM_CAMERA_BACKEND`` (cv2 / broker / auto).
* Model id follows ``FM_VISION_MODEL``, default
  ``mlx-community/paligemma2-3b-mix-224-bf16`` (the bf16 build of
  the same model used in the prior validated demo).
* Model warmup is **kicked off as a background task** so the FastAPI
  app starts serving requests without paying the ~4 s weight load on
  the request path. The watcher's first tick may still pay it.
* Watcher is started immediately so once the operator confirms an
  active item, counts begin landing in inventory without further
  wiring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fruit_market.vision.camera import open_camera
from fruit_market.vision.model import PaliGemmaCounter
from fruit_market.vision.watcher import VisionWatcher

if TYPE_CHECKING:
    from fruit_market.services.protocols import Services
    from fruit_market.vision.camera import CameraBackend

logger = logging.getLogger(__name__)


@dataclass
class VisionBundle:
    """Everything the HTTP layer needs to talk to the vision stack."""

    camera: CameraBackend
    model: PaliGemmaCounter
    watcher: VisionWatcher

    async def shutdown(self) -> None:
        """Stop the watcher and release the camera. Safe to call
        even if the bundle was never fully initialised."""

        try:
            await self.watcher.stop()
        except Exception:  # noqa: BLE001
            logger.exception("vision watcher stop failed")
        try:
            self.camera.close()
        except Exception:  # noqa: BLE001
            logger.exception("camera close failed")


async def build_default_vision(services: Services) -> VisionBundle:
    """Construct + warm up + start the default vision bundle.

    Intended to be called from the FastAPI lifespan::

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            services = make_services()
            vision = await build_default_vision(services)
            app.state.services = services
            app.state.vision = vision
            try:
                yield
            finally:
                await vision.shutdown()

    The function returns once the watcher is running. The model
    weight load proceeds in a daemon thread; the first ``count`` call
    blocks until it completes.
    """

    camera = open_camera()
    model = PaliGemmaCounter()
    # Kick off the model load in the background. The watcher's first
    # tick will still block on it (it has to — the count() call
    # acquires the same lock), but the lifespan returns immediately
    # so /api/state and webhooks come up while the model loads.
    model.warmup_async()

    watcher = VisionWatcher(
        catalog_active_item=services.catalog.get_active_item,
        # Multi-item mode: count every taught item each tick (up to
        # FM_VISION_MAX_ITEMS, default 4). Keeps the kiosk's per-item
        # physical_count live for everything in the catalog, not
        # just the highlighted "active" pick.
        catalog_items=services.catalog.list_items,
        inventory=services.inventory,
        camera=camera,
        model=model,
    )
    await watcher.start()

    return VisionBundle(camera=camera, model=model, watcher=watcher)
