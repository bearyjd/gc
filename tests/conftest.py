"""Shared test fixtures for gc test suite."""
import json
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
    """Pre-built requests.Session with a fake bearer token. No Playwright."""
    session = requests.Session()
    session.headers.update({
        "Authorization": "Bearer test-token-abc123",
        "Accept": "application/json",
    })
    return session
