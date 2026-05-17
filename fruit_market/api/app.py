"""FastAPI application bootstrap."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fruit_market.api import phone_webhook, routes, stripe_webhook
from fruit_market.services import make_services

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.services = make_services()
    yield


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
