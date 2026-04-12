"""Tests for gc_cli.session — cache logic and credential loading."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# We import only the pure functions — no Playwright triggered at import time
from gc_cli.session import (
    SESSION_TTL_MINUTES,
    _get_credentials,
    _load_cached_session,
    _save_session,
    _session_path,
    get_session,
)


# ---------------------------------------------------------------------------
# _session_path
# ---------------------------------------------------------------------------

def test_session_path_is_deterministic(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    p1 = _session_path("user@example.com")
    p2 = _session_path("user@example.com")
    assert p1 == p2


def test_session_path_differs_by_email(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    assert _session_path("a@example.com") != _session_path("b@example.com")


# ---------------------------------------------------------------------------
# _get_credentials
# ---------------------------------------------------------------------------

def test_get_credentials_from_env(monkeypatch):
    monkeypatch.setenv("GC_EMAIL", "env@example.com")
    monkeypatch.setenv("GC_PASSWORD", "envpass")
    email, password = _get_credentials()
    assert email == "env@example.com"
    assert password == "envpass"


def test_get_credentials_from_env_file(tmp_gc_dir, monkeypatch):
    monkeypatch.delenv("GC_EMAIL", raising=False)
    monkeypatch.delenv("GC_PASSWORD", raising=False)
    monkeypatch.setattr("gc_cli.session.GC_DIR", tmp_gc_dir)
    env_file = tmp_gc_dir / ".env"
    env_file.write_text('GC_EMAIL="file@example.com"\nGC_PASSWORD="filepass"\n')
    email, password = _get_credentials()
    assert email == "file@example.com"
    assert password == "filepass"


def test_get_credentials_raises_if_missing(tmp_gc_dir, monkeypatch):
    monkeypatch.delenv("GC_EMAIL", raising=False)
    monkeypatch.delenv("GC_PASSWORD", raising=False)
    monkeypatch.setattr("gc_cli.session.GC_DIR", tmp_gc_dir)
    with pytest.raises(RuntimeError, match="GC_EMAIL and GC_PASSWORD not found"):
        _get_credentials()


# ---------------------------------------------------------------------------
# Cache load/save
# ---------------------------------------------------------------------------

def test_load_cached_session_returns_none_when_missing(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    assert _load_cached_session("nobody@example.com") is None


def test_save_and_load_session_roundtrip(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer saved-token"})
    cookies = [{"name": "gc_session", "value": "abc123", "domain": ".gc.com"}]

    _save_session("user@example.com", session, cookies)
    loaded = _load_cached_session("user@example.com")

    assert loaded is not None
    assert loaded.headers.get("Authorization") == "Bearer saved-token"


def test_load_cached_session_returns_none_when_expired(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    session = requests.Session()
    _save_session("user@example.com", session, [])

    # Manually back-date the cached_at timestamp
    path = _session_path("user@example.com")
    data = json.loads(path.read_text())
    expired = (datetime.now() - timedelta(minutes=SESSION_TTL_MINUTES + 1)).isoformat()
    data["cached_at"] = expired
    path.write_text(json.dumps(data))

    assert _load_cached_session("user@example.com") is None


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

def test_get_session_uses_cache_without_calling_playwright(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.GC_DIR", tmp_gc_dir)
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setenv("GC_EMAIL", "user@example.com")
    monkeypatch.setenv("GC_PASSWORD", "secret")

    # Pre-populate cache
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer cached-token"})
    _save_session("user@example.com", session, [])

    with patch("gc_cli.session._playwright_login") as mock_login:
        result = get_session(verbose=False)
        mock_login.assert_not_called()

    assert result.headers.get("Authorization") == "Bearer cached-token"


def test_get_session_runs_playwright_when_cache_missing(tmp_gc_dir, monkeypatch):
    monkeypatch.setattr("gc_cli.session.GC_DIR", tmp_gc_dir)
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setenv("GC_EMAIL", "user@example.com")
    monkeypatch.setenv("GC_PASSWORD", "secret")

    fresh_session = requests.Session()
    fresh_session.headers.update({"Authorization": "Bearer fresh-token"})

    with patch("gc_cli.session._playwright_login", return_value=(fresh_session, [])):
        result = get_session(verbose=False)

    assert result.headers.get("Authorization") == "Bearer fresh-token"
    # Session should now be cached
    assert _load_cached_session("user@example.com") is not None
