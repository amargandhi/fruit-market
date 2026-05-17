"""FastAPI application bootstrap."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fruit_market.api import phone_webhook, routes, stripe_webhook
from fruit_market.restock import RestockSettings, build_restock_runtime
from fruit_market.services import make_services
from fruit_market.services.factory import make_real_services
from fruit_market.state.store import open_default_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot the services bundle and (optionally) the vision pipeline.

    The vision pipeline is opt-out via ``FM_VISION_ENABLED=0`` —
    contract tests run with it disabled so they don't try to open
    the USB camera or download PaliGemma weights. In production /
    demo the flag is unset, so the watcher starts and the model
    warmup runs in a background daemon thread; FastAPI starts
    serving requests immediately while the model loads (~5 s) so
    the kiosk doesn't appear frozen.
    """

    event_store = None
    restock = None
    if os.environ.get("FRUITMARKET_USE_STUBS") == "1":
        services = make_services()
    else:
        event_store = open_default_store()
        services = make_real_services(store=event_store)
    app.state.services = services
    app.state.event_store = event_store
    app.state.restock = None
    app.state.vision = None

    if event_store is not None:
        restock = build_restock_runtime(
            settings=RestockSettings.from_env(),
            store=event_store,
            services=services,
        )
        app.state.restock = restock
        if restock is not None:
            restock.start()
            logger.info("restock worker started")

    if os.environ.get("FM_VISION_ENABLED", "1") != "0":
        try:
            from fruit_market.vision import build_default_vision

            app.state.vision = await build_default_vision(services)
            logger.info("vision pipeline started")
        except Exception as exc:  # noqa: BLE001
            # Don't fail the app if the vision deps are missing
            # (Linux CI, dev without `--extra vision`, etc.) — just
            # log it and keep going. The Pico bridge will report
            # health.camera = unknown until vision comes up.
            logger.warning("vision pipeline disabled: %s", exc)

    try:
        yield
    finally:
        if app.state.vision is not None:
            await app.state.vision.shutdown()
        if restock is not None:
            restock.stop()
        if event_store is not None:
            event_store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Fruit Market", lifespan=lifespan)
    app.include_router(routes.router)
    app.include_router(phone_webhook.router)
    app.include_router(stripe_webhook.router)

    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    if (ui_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="kiosk")
    return app


app = create_app()
