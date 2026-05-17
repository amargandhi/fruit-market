"""FastAPI application bootstrap."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fruit_market.api import phone_webhook, routes, stripe_webhook
from fruit_market.restock import RestockSettings, build_restock_runtime
from fruit_market.services import make_services
from fruit_market.services.factory import make_real_services
from fruit_market.state.store import open_default_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fruit_market.services.protocols import Services


logger = logging.getLogger(__name__)
load_dotenv()


# ─── Default fruit seeded at boot ──────────────────────────────────
#
# The watcher counts whatever's in the catalog. To make the demo
# "just work" — no teach step required — we seed two default fruits
# at boot if they're not already present. The user can teach more,
# but apples + bananas always exist so PaliGemma has something to
# count the moment the kiosk loads.
#
# Each tuple is (name, dollars, initial_count). Initial count is
# zero — the model will overwrite it with the real count on the
# first watcher tick.
_DEFAULT_FRUIT: list[tuple[str, float, int]] = [
    ("apple", 1.00, 0),
    ("banana", 0.75, 0),
]


def _ensure_default_fruit(services: Services) -> None:
    """Idempotently seed apple + banana into the catalog at boot.

    Looks each fruit up by name; if it doesn't exist, proposes +
    confirms a teach so the ItemTaught event lands in the store.
    Safe to call on every boot — existing fruit are skipped.
    """

    existing = {item.name.lower() for item in services.catalog.list_items()}
    for name, dollars, count in _DEFAULT_FRUIT:
        if name.lower() in existing:
            continue
        # ``parse_transcript`` understands "$X.YZ" + "N of them";
        # we go through the teach path so the events look exactly
        # like a human teach (one ItemTaught + one ActiveItemSet).
        transcript = f"These are {name}, ${dollars:.2f}, {count} of them"
        proposal = services.teach.propose(transcript)
        services.teach.confirm(proposal.id)
        logger.info("seeded default catalog item: %s @ $%.2f", name, dollars)


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

    Video AND inference run from the moment the app boots — there
    is no "start" gate. ``app.state.demo_active`` is a semantic
    flag only: it means "operator has confirmed the shelf and is
    ready to take phone orders." The Pico READY button flips it
    to True. The model never stops counting, the camera never
    stops streaming, and inventory keeps reconciling regardless.
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

    # Seed apple + banana so the watcher has something to count
    # from boot — no manual teach required. Idempotent.
    _ensure_default_fruit(services)
    # "Ready for phone orders" flag. NOT a video/inference gate —
    # both run from boot. Operator confirms the shelf via the
    # Pico READY button, which flips this to True.
    app.state.demo_active = (
        os.environ.get("FM_DEMO_AUTOSTART", "0") == "1"
    )

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

            # No `gate=` kwarg → the watcher's default is always-open,
            # so PaliGemma starts counting the moment the model
            # finishes warmup. The kiosk shows fresh counts without
            # the operator having to "start" anything.
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
