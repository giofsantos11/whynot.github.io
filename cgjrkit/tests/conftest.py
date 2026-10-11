"""Shared test configuration.

Unit tests never touch the network: each test gets an isolated cache and
offline mode. Live tests (``-m live``) call real APIs and are skipped unless
``CGJRKIT_LIVE=1``.
"""

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch, request):
    monkeypatch.setenv("CGJRKIT_CACHE", str(tmp_path / "cache"))
    if "live" not in request.keywords:
        monkeypatch.setenv("CGJRKIT_OFFLINE", "1")
    yield


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CGJRKIT_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live API test; set CGJRKIT_LIVE=1 and allowlist the domains")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
