"""Pytest fixtures shared across the test suite.

Both tracks should use these — adding new project-wide fixtures
goes through Phase 1's owners (we don't want a merge conflict
mid-build on conftest.py). Track-specific fixtures go in
``tests/<track>/conftest.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from fruit_market.services import Services, make_services
from fruit_market.services._stubs import (
    StubCatalogService,
    StubInventoryService,
    StubOrdersService,
    StubPricingService,
    StubTeachService,
    make_stub_services,
)


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
