"""Shared test fixtures for gc test suite."""
from pathlib import Path

import pytest
import requests


@pytest.fixture
def tmp_gc_dir(tmp_path: Path) -> Path:
    """Isolated ~/.gc/ substitute. Creates sessions/ subdir."""
    gc_dir = tmp_path / ".gc"
    gc_dir.mkdir(mode=0o700)
    (gc_dir / "sessions").mkdir(mode=0o700)
    return gc_dir


@pytest.fixture
def mock_session() -> requests.Session:
    """Pre-built requests.Session with fake GC auth headers. No Playwright.

    Matches the real header shape from gc_cli/session.py::_make_session()
    (gc-token, not Authorization: Bearer).
    """
    session = requests.Session()
    session.headers.update({
        "gc-token": "test-token-abc123",
        "gc-app-name": "web",
        "Accept": "application/json",
    })
    return session
