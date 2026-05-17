"""Pytest fixtures shared across the test suite.

Both tracks should use these — adding new project-wide fixtures
goes through Phase 1's owners (we don't want a merge conflict
mid-build on conftest.py). Track-specific fixtures go in
``tests/<track>/conftest.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from fruit_market.services import Services, make_services
from fruit_market.services._stubs import (
    StubCatalogService,
    StubInventoryService,
    StubOrdersService,
    StubPricingService,
    StubTeachService,
    make_stub_services,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_event_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets a fresh on-disk event store.

    Without this, the default ``./.fruitmarket/events.db`` would be
    shared across tests (and across runs), so any test that boots the
    FastAPI lifespan accumulates state from every previous test.
    Setting ``FM_EVENT_STORE_PATH`` per test pins the DB to a
    pytest-managed temp dir that's cleaned up after the test.
    """

    monkeypatch.setenv("FM_EVENT_STORE_PATH", str(tmp_path / "events.db"))


@pytest.fixture(autouse=True)
def _disable_vision_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract tests boot the FastAPI lifespan; we don't want them
    to open the USB camera or download PaliGemma weights. Vision
    integration is exercised by the live smoke script
    (``scripts/smoke_vision.py``) and the watcher unit tests."""

    monkeypatch.setenv("FM_VISION_ENABLED", "0")


@pytest.fixture
def services() -> Iterator[Services]:
    """Fresh in-memory services bundle for every test.

    The factory ``make_services()`` returns stubs in Phase 1 and
    real implementations after Track A merges. Tests that depend
    on persistence should use a track-A-specific fixture; tests
    that exercise behavior should use this one.
    """

    yield make_services()


@pytest.fixture
def stub_services() -> Iterator[Services]:
    """Always the in-memory stub bundle, regardless of which
    backend ``make_services`` currently points at. Use this for
    tests that need to peek at the stub-specific internals."""

    yield make_stub_services()


@pytest.fixture
def make_default_services() -> Iterator[type]:
    """A reference to ``make_services`` for tests that want to
    invoke it themselves (e.g. to verify it returns a Services
    instance)."""

    yield make_services  # type: ignore[misc]


__all__ = [
    "services",
    "stub_services",
    "make_default_services",
    "StubCatalogService",
    "StubInventoryService",
    "StubOrdersService",
    "StubPricingService",
    "StubTeachService",
]
