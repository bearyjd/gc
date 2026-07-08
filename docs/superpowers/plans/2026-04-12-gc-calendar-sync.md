# gc Calendar Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual token auth with Playwright login, complete API response parsing, and add a `gc sync` command that pushes the GameChanger schedule to Google Calendar via `gog`.

**Architecture:** `session.py` (new) handles Playwright login and cookie cache; `client.py` accepts an injected `requests.Session`; `sync.py` (new) owns the diff loop and `gog` subprocess calls; `cli.py` wires them together and adds the `gc sync` subcommand.

**Tech Stack:** Python 3.10+, `playwright` (lazy import), `requests`, `pytest`, `gog` CLI (already on `192.168.1.14`)

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `gc_cli/session.py` | Playwright login, 60-min cookie cache in `~/.gc/sessions/` |
| Modify | `gc_cli/client.py` | Accept injected `requests.Session`; remove `GC_TOKEN` logic; normalize responses |
| Create | `gc_cli/sync.py` | `event_fingerprint`, `load_state/save_state`, `sync_team` diff loop, `gog` subprocess |
| Modify | `gc_cli/cli.py` | Call `get_session()` in all commands; add `gc sync` subcommand |
| Create | `tests/conftest.py` | `mock_session` and `tmp_gc_dir` fixtures |
| Create | `tests/test_session.py` | Session cache hit/miss/expiry, credential loading |
| Create | `tests/test_sync.py` | Fingerprint, state round-trip, diff logic (new/changed/removed), dry-run |
| Create | `tests/e2e/test_sync_e2e.sh` | Live end-to-end on `192.168.1.14` |
| Create | `.github/workflows/ci.yml` | Run pytest + autoincrement patch version on green `main` |
| Modify | `pyproject.toml` | Add `[dev]` and `[browser]` optional extras |
| Modify | `.gitignore` | Add `.pytest_cache/`, `tests/e2e/.env`, `tests/e2e/output/`, `*.log` |
| Modify | `.env.example` | Replace `GC_TOKEN` with `GC_EMAIL`, `GC_PASSWORD`, `GC_CALENDAR_ID`, `GOG_ACCOUNT` |
| Modify | `README.md` | Update Setup, Usage, and Environment sections |

---

## Task 1: Repo bootstrap & CI infrastructure

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1.1: Update `pyproject.toml` with optional extras**

Replace the `[project]` block's dependencies and add optional extras:

```toml
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "gc"
version = "0.1.0"
description = "GameChanger team schedule & clips CLI scraper"
license = "MIT"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.28",
]

[project.optional-dependencies]
browser = ["playwright>=1.40"]
dev = ["pytest>=7", "pytest-cov"]

[project.scripts]
gc = "gc_cli.cli:main"

[tool.setuptools.packages.find]
include = ["gc_cli*"]
```

- [ ] **Step 1.2: Update `.gitignore`**

Append to the existing `.gitignore`:

```gitignore
.pytest_cache/
tests/e2e/.env
tests/e2e/output/
*.log
```

- [ ] **Step 1.3: Create GitHub repo and push**

```bash
gh repo create bearyjd/gc --public --description "GameChanger team schedule & clips CLI scraper" --source . --remote origin --push
```

- [ ] **Step 1.4: Create `.github/workflows/ci.yml`**

```bash
mkdir -p .github/workflows
```

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Run tests
        run: pytest tests/ -v --ignore=tests/e2e

      - name: Bump patch version
        if: |
          github.ref == 'refs/heads/main' &&
          github.event_name == 'push' &&
          github.actor != 'github-actions[bot]'
        run: |
          NEW_VERSION=$(python3 - <<'EOF'
          import re, pathlib
          p = pathlib.Path("pyproject.toml")
          text = p.read_text()
          m = re.search(r'version = "(\d+\.\d+\.)(\d+)"', text)
          new_patch = int(m.group(2)) + 1
          new_version = m.group(1) + str(new_patch)
          p.write_text(text[:m.start()] + f'version = "{new_version}"' + text[m.end():])
          print(new_version, end="")
          EOF
          )
          echo "NEW_VERSION=$NEW_VERSION" >> "$GITHUB_ENV"

      - name: Commit and tag version bump
        if: |
          github.ref == 'refs/heads/main' &&
          github.event_name == 'push' &&
          github.actor != 'github-actions[bot]'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add pyproject.toml
          git commit -m "chore: bump version to $NEW_VERSION"
          git tag "v$NEW_VERSION"
          git push --follow-tags
```

- [ ] **Step 1.5: Commit**

```bash
git add pyproject.toml .gitignore .github/
git commit -m "chore: repo bootstrap — CI, gitignore, pyproject extras"
git push
```

---

## Task 2: Test fixtures

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 2.1: Create `tests/__init__.py`**

```python
```
(empty file)

- [ ] **Step 2.2: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 2.3: Verify fixtures are importable**

```bash
pip install -e ".[dev]"
python -c "from tests.conftest import *; print('ok')"
```

Expected: `ok`

- [ ] **Step 2.4: Commit**

```bash
git add tests/
git commit -m "test: add conftest fixtures (tmp_gc_dir, mock_session)"
```

---

## Task 3: `session.py` — Playwright login & cache (TDD)

**Files:**
- Create: `tests/test_session.py`
- Create: `gc_cli/session.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/test_session.py`:

```python
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
```

- [ ] **Step 3.2: Run tests to confirm they fail**

```bash
pytest tests/test_session.py -v
```

Expected: `ModuleNotFoundError: No module named 'gc_cli.session'`

- [ ] **Step 3.3: Create `gc_cli/session.py`**

```python
"""
GameChanger session manager.

Handles Playwright login and session caching. Modeled on ixl_cli/session.py.
Playwright is a lazy import — only loaded when a real login is needed.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from gc_cli.client import GC_DIR

SESSION_DIR = GC_DIR / "sessions"
SESSION_TTL_MINUTES = 60


def _get_credentials() -> tuple[str, str]:
    """Load GC_EMAIL and GC_PASSWORD from env or ~/.gc/.env."""
    email = os.environ.get("GC_EMAIL")
    password = os.environ.get("GC_PASSWORD")

    if not email or not password:
        env_path = GC_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == "GC_EMAIL":
                    email = v
                elif k.strip() == "GC_PASSWORD":
                    password = v

    if not email or not password:
        raise RuntimeError(
            "GC_EMAIL and GC_PASSWORD not found.\n"
            "Set them via env vars or create ~/.gc/.env with:\n"
            '  GC_EMAIL="you@example.com"\n'
            '  GC_PASSWORD="yourpassword"'
        )
    return email, password


def _session_path(email: str) -> Path:
    key = hashlib.sha256(email.encode()).hexdigest()[:12]
    return SESSION_DIR / f"{key}.json"


def _load_cached_session(email: str) -> requests.Session | None:
    """Return a valid cached session, or None if missing/expired."""
    path = _session_path(email)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    cached_at = datetime.fromisoformat(data["cached_at"])
    if datetime.now() - cached_at > timedelta(minutes=SESSION_TTL_MINUTES):
        return None

    session = requests.Session()
    session.headers.update(data.get("headers", {}))
    for cookie in data.get("cookies", []):
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain", "")
        )
    return session


def _save_session(email: str, session: requests.Session, cookies: list[dict]) -> None:
    """Persist session headers and cookies to disk."""
    SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _session_path(email)
    data = {
        "cached_at": datetime.now().isoformat(),
        "headers": dict(session.headers),
        "cookies": cookies,
    }
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def _playwright_login(
    email: str, password: str, visible: bool = False
) -> tuple[requests.Session, list[dict]]:
    """Run Playwright headless login. Returns (session, raw_cookies)."""
    from playwright.sync_api import sync_playwright  # lazy import

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto("https://app.gc.com/login", timeout=30000)
            page.fill('input[type="email"]', email)
            page.fill('input[type="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_url("**/home**", timeout=30000)
        except Exception as e:
            browser.close()
            hint = " Try --visible to debug Cloudflare." if not visible else ""
            raise RuntimeError(f"GameChanger login failed: {e}.{hint}") from e

        raw_cookies = context.cookies()
        for cookie in raw_cookies:
            session.cookies.set(
                cookie["name"], cookie["value"], domain=cookie.get("domain", "")
            )

        # Extract bearer token from localStorage if available
        token = page.evaluate(
            "() => localStorage.getItem('authToken') || "
            "localStorage.getItem('auth_token') || "
            "localStorage.getItem('token')"
        )
        if token:
            session.headers.update({"Authorization": f"Bearer {token}"})

        browser.close()

    return session, raw_cookies


def get_session(verbose: bool = True, visible: bool = False) -> requests.Session:
    """Return an authenticated requests.Session. Uses cache if TTL valid."""
    email, _ = _get_credentials()

    cached = _load_cached_session(email)
    if cached:
        if verbose:
            print("  Using cached session", file=sys.stderr)
        return cached

    if verbose:
        print("  Logging in via Playwright...", file=sys.stderr)

    _, password = _get_credentials()
    session, raw_cookies = _playwright_login(email, password, visible=visible)
    _save_session(email, session, raw_cookies)

    if verbose:
        print("  Login successful, session cached", file=sys.stderr)

    return session
```

- [ ] **Step 3.4: Run tests to confirm they pass**

```bash
pytest tests/test_session.py -v
```

Expected: all 10 tests PASS

- [ ] **Step 3.5: Commit**

```bash
git add gc_cli/session.py tests/test_session.py
git commit -m "feat: add session.py — Playwright login with 60-min cache"
```

---

## Task 4: Update `client.py` to accept injected session

**Files:**
- Modify: `gc_cli/client.py`

The response parsing stubs stay defensive for now. A "discover" step captures real API shapes.

- [ ] **Step 4.1: Rewrite `GCClient.__init__` to accept a `requests.Session`**

Replace `gc_cli/client.py` entirely:

```python
"""
GameChanger API client.

Accepts an injected requests.Session (from session.py).
Response parsing uses defensive fallbacks until real API shapes are confirmed.
"""

import json
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config paths (shared with session.py and cli.py)
# ---------------------------------------------------------------------------

GC_DIR = Path.home() / ".gc"
ENV_PATH = GC_DIR / ".env"
TEAMS_PATH = GC_DIR / "teams.json"

BASE_URL = "https://api.team-manager.gc.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Print to stderr (keeps stdout clean for --json)."""
    print(msg, file=sys.stderr)


def _ensure_dir() -> None:
    GC_DIR.mkdir(mode=0o700, exist_ok=True)


def _load_env() -> dict[str, str]:
    """Load key=value pairs from ~/.gc/.env (bash-style, quotes stripped)."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _load_teams() -> list[dict]:
    """Load tracked teams from ~/.gc/teams.json."""
    if not TEAMS_PATH.exists():
        return []
    return json.loads(TEAMS_PATH.read_text())


def _save_teams(teams: list[dict]) -> None:
    _ensure_dir()
    TEAMS_PATH.write_text(json.dumps(teams, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Normalizers — update these once real API responses are inspected
# ---------------------------------------------------------------------------

def _normalize_team(raw: dict) -> dict:
    """Normalize a raw team object to {id, name, sport, season}."""
    return {
        "id": raw.get("id") or raw.get("teamId", ""),
        "name": raw.get("name") or raw.get("teamName", "Unknown"),
        "sport": raw.get("sport", ""),
        "season": raw.get("season", ""),
    }


def _normalize_event(raw: dict) -> dict:
    """Normalize a raw schedule event to {id, date, time, type, opponent, location, home_away}."""
    return {
        "id": raw.get("id") or raw.get("eventId", ""),
        "date": raw.get("date") or raw.get("start_date", ""),
        "time": raw.get("time") or raw.get("start_time", ""),
        "type": raw.get("type") or raw.get("event_type", ""),
        "opponent": raw.get("opponent") or raw.get("title") or raw.get("name", ""),
        "location": raw.get("location") or raw.get("venue", ""),
        "home_away": raw.get("home_away") or raw.get("homeAway", ""),
    }


def _normalize_clip(raw: dict) -> dict:
    """Normalize a raw clip to {id, title, url, event_id}."""
    return {
        "id": raw.get("id") or raw.get("clipId", ""),
        "title": raw.get("title") or raw.get("name", "Untitled"),
        "url": raw.get("url") or raw.get("clipUrl", ""),
        "event_id": raw.get("eventId") or raw.get("event_id", ""),
    }


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class GCClient:
    """Thin wrapper around the GameChanger REST API."""

    def __init__(self, session: requests.Session, verbose: bool = True):
        self.session = session
        self.verbose = verbose

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{BASE_URL}{path}"
        if self.verbose:
            _log(f"  GET {path}")
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            _log(f"  WARN: HTTP error on {path}: {e}")
            return []
        except (requests.RequestException, ValueError) as e:
            _log(f"  WARN: Request failed on {path}: {e}")
            return []

    # --- Teams -----------------------------------------------------------

    def get_my_teams(self) -> list[dict]:
        """GET /me/teams — list teams the authenticated user belongs to."""
        data = self._get("/me/teams")
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("teams", data.get("data", []))
        return [_normalize_team(t) for t in raw if isinstance(t, dict)]

    # --- Schedule --------------------------------------------------------

    def get_schedule(self, team_id: str) -> list[dict]:
        """GET /teams/{team_id}/schedule — upcoming games & practices."""
        data = self._get(f"/teams/{team_id}/schedule")
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("events", data.get("schedule", data.get("data", [])))
        return [_normalize_event(e) for e in raw if isinstance(e, dict)]

    # --- Clips -----------------------------------------------------------

    def get_clips(self, team_id: str) -> list[dict]:
        """GET /clips?kind=event&teamId={team_id} — game clips / highlights."""
        data = self._get("/clips", params={"kind": "event", "teamId": team_id})
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("clips", data.get("data", []))
        return [_normalize_clip(c) for c in raw if isinstance(c, dict)]

    # --- Summary ---------------------------------------------------------

    def get_team_summary(self, team_id: str) -> dict:
        """Fetch schedule + clips for one team."""
        return {
            "team_id": team_id,
            "schedule": self.get_schedule(team_id),
            "clips": self.get_clips(team_id),
        }
```

- [ ] **Step 4.2: Verify no import errors**

```bash
python -c "from gc_cli.client import GCClient; print('ok')"
```

Expected: `ok`

- [ ] **Step 4.3: Discover real API response shapes (manual, run on `.14`)**

Run with real credentials to capture what the API actually returns:

```bash
# On 192.168.1.14, with GC_EMAIL/GC_PASSWORD set in ~/.gc/.env:
gc teams --json 2>/tmp/gc-debug.log | python3 -m json.tool | head -40
gc schedule --json 2>>/tmp/gc-debug.log | python3 -m json.tool | head -60
```

Inspect the raw field names from the output. If field names differ from what `_normalize_*` expects (e.g., the API returns `startDate` instead of `date`), update the normalizers in `client.py` to match. The defensive fallback chain in each normalizer handles multiple naming conventions already — add any new ones discovered here.

- [ ] **Step 4.4: Commit**

```bash
git add gc_cli/client.py
git commit -m "refactor: client.py accepts injected session, normalize response shapes"
```

---

## Task 5: `sync.py` — diff loop and `gog` subprocess (TDD)

**Files:**
- Create: `tests/test_sync.py`
- Create: `gc_cli/sync.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/test_sync.py`:

```python
"""Tests for gc_cli.sync — fingerprint, state, and diff logic."""
import json
from pathlib import Path
from unittest.mock import call, patch

import pytest

from gc_cli.sync import (
    SyncResult,
    event_fingerprint,
    load_state,
    save_state,
    sync_team,
)

GAME_EVENT = {
    "id": "evt_001",
    "date": "2026-05-10",
    "time": "10:00",
    "type": "Game",
    "opponent": "Eagles",
    "location": "Home Field",
    "home_away": "home",
}

PRACTICE_EVENT = {
    "id": "evt_002",
    "date": "2026-05-12",
    "time": "16:00",
    "type": "Practice",
    "opponent": "",
    "location": "Practice Field",
    "home_away": "",
}


# ---------------------------------------------------------------------------
# event_fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_deterministic():
    assert event_fingerprint(GAME_EVENT) == event_fingerprint(GAME_EVENT)


def test_fingerprint_is_16_chars():
    assert len(event_fingerprint(GAME_EVENT)) == 16


def test_fingerprint_changes_when_opponent_changes():
    modified = {**GAME_EVENT, "opponent": "Ravens"}
    assert event_fingerprint(GAME_EVENT) != event_fingerprint(modified)


def test_fingerprint_changes_when_date_changes():
    modified = {**GAME_EVENT, "date": "2026-05-11"}
    assert event_fingerprint(GAME_EVENT) != event_fingerprint(modified)


# ---------------------------------------------------------------------------
# load_state / save_state
# ---------------------------------------------------------------------------

def test_load_state_returns_empty_dict_when_file_missing(tmp_gc_dir):
    assert load_state(tmp_gc_dir) == {}


def test_save_and_load_state_roundtrip(tmp_gc_dir):
    state = {
        "evt_001": {
            "gcal_event_id": "gcal_abc",
            "calendar_id": "cal_primary",
            "fingerprint": "abc12345abcd1234",
            "summary": "Game: Eagles",
        }
    }
    save_state(state, tmp_gc_dir)
    loaded = load_state(tmp_gc_dir)
    assert loaded == state


def test_save_state_file_is_readable(tmp_gc_dir):
    save_state({"k": "v"}, tmp_gc_dir)
    raw = (tmp_gc_dir / "sync-state.json").read_text()
    assert json.loads(raw) == {"k": "v"}


# ---------------------------------------------------------------------------
# sync_team — new events
# ---------------------------------------------------------------------------

def test_sync_creates_new_event(tmp_gc_dir):
    gog_response = json.dumps({"id": "gcal_new_001"})

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, gog_response)):
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    assert "Game: Eagles" in result.created
    assert result.errors == []

    state = load_state(tmp_gc_dir)
    assert "evt_001" in state
    assert state["evt_001"]["gcal_event_id"] == "gcal_new_001"
    assert state["evt_001"]["summary"] == "Game: Eagles"


def test_sync_create_uses_blue_for_games(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_1"}')) as mock_gog:
        sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    args = mock_gog.call_args[0][0]
    assert "--event-color" in args
    assert args[args.index("--event-color") + 1] == "9"  # blue for game


def test_sync_create_uses_green_for_practices(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_2"}')) as mock_gog:
        sync_team([PRACTICE_EVENT], "cal_primary", tmp_gc_dir)

    args = mock_gog.call_args[0][0]
    assert args[args.index("--event-color") + 1] == "10"  # green for practice


# ---------------------------------------------------------------------------
# sync_team — changed events
# ---------------------------------------------------------------------------

def test_sync_updates_changed_event(tmp_gc_dir):
    state = {
        "evt_001": {
            "gcal_event_id": "gcal_existing",
            "calendar_id": "cal_primary",
            "fingerprint": "old_fingerprint_x",  # deliberately wrong
            "summary": "Game: Eagles",
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, "")) as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    assert "Game: Eagles" in result.updated
    assert result.errors == []

    # update command should reference existing gcal event ID
    args = mock_gog.call_args[0][0]
    assert "update" in args
    assert "gcal_existing" in args


def test_sync_skips_unchanged_event(tmp_gc_dir):
    fp = event_fingerprint(GAME_EVENT)
    state = {
        "evt_001": {
            "gcal_event_id": "gcal_existing",
            "calendar_id": "cal_primary",
            "fingerprint": fp,
            "summary": "Game: Eagles",
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog") as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    mock_gog.assert_not_called()
    assert result.created == []
    assert result.updated == []


# ---------------------------------------------------------------------------
# sync_team — removed events (cancelled)
# ---------------------------------------------------------------------------

def test_sync_cancels_removed_event(tmp_gc_dir):
    state = {
        "evt_removed": {
            "gcal_event_id": "gcal_gone",
            "calendar_id": "cal_primary",
            "fingerprint": "fp",
            "summary": "Game: Ravens",
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, "")):
        result = sync_team([], "cal_primary", tmp_gc_dir)

    assert "gcal_gone" in result.cancelled
    # Removed from state after cancel
    assert load_state(tmp_gc_dir) == {}


def test_sync_cancel_sets_grey_color_and_cancelled_prefix(tmp_gc_dir):
    state = {
        "evt_removed": {
            "gcal_event_id": "gcal_gone",
            "calendar_id": "cal_primary",
            "fingerprint": "fp",
            "summary": "Game: Ravens",
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, "")) as mock_gog:
        sync_team([], "cal_primary", tmp_gc_dir)

    args = mock_gog.call_args[0][0]
    summary_idx = args.index("--summary")
    assert args[summary_idx + 1].startswith("[CANCELLED]")
    assert args[args.index("--event-color") + 1] == "8"  # graphite


# ---------------------------------------------------------------------------
# sync_team — dry run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_call_gog(tmp_gc_dir):
    with patch("gc_cli.sync._run_gog") as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir, dry_run=True)

    mock_gog.assert_not_called()
    assert "Game: Eagles" in result.created
    # State file NOT written in dry run
    assert not (tmp_gc_dir / "sync-state.json").exists()


# ---------------------------------------------------------------------------
# sync_team — error handling
# ---------------------------------------------------------------------------

def test_fails_fast_if_gog_not_on_path(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="gog not found"):
            sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)


def test_continues_on_gog_error_for_one_event(tmp_gc_dir):
    events = [
        GAME_EVENT,
        {**PRACTICE_EVENT, "id": "evt_002", "opponent": ""},
    ]
    call_count = {"n": 0}

    def gog_fails_first(args):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (False, "API rate limit")
        return (True, '{"id":"gcal_002"}')

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", side_effect=gog_fails_first):
        result = sync_team(events, "cal_primary", tmp_gc_dir)

    assert len(result.errors) == 1
    assert len(result.created) == 1
```

- [ ] **Step 5.2: Run tests to confirm they fail**

```bash
pytest tests/test_sync.py -v
```

Expected: `ModuleNotFoundError: No module named 'gc_cli.sync'`

- [ ] **Step 5.3: Create `gc_cli/sync.py`**

```python
"""
GameChanger → Google Calendar sync via gog CLI.

Public surface:
  load_state(gc_dir)                          → dict
  save_state(state, gc_dir)
  event_fingerprint(event)                    → str (16-char hex)
  sync_team(events, calendar_id, gc_dir, dry_run) → SyncResult
"""

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_STATE_FILENAME = "sync-state.json"

# gog calendar color IDs (from `gog calendar colors`)
COLOR_GAME = "9"       # #5484ed blue
COLOR_PRACTICE = "10"  # #51b749 green
COLOR_CANCELLED = "8"  # #e1e1e1 graphite

# Default event durations in minutes when end time is absent
DURATION_DEFAULTS: dict[str, int] = {
    "game": 120,
    "practice": 90,
    "default": 60,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state(gc_dir: Path) -> dict:
    """Read ~/.gc/sync-state.json. Returns {} if missing or unreadable."""
    path = gc_dir / SYNC_STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, gc_dir: Path) -> None:
    """Write state dict to ~/.gc/sync-state.json (mode 0600)."""
    path = gc_dir / SYNC_STATE_FILENAME
    path.write_text(json.dumps(state, indent=2) + "\n")
    path.chmod(0o600)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def event_fingerprint(event: dict) -> str:
    """16-char hex digest of the fields that matter for change detection."""
    key = "".join([
        event.get("date", ""),
        event.get("time", ""),
        event.get("opponent", ""),
        event.get("type", ""),
        event.get("location", ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _event_summary(event: dict) -> str:
    etype = event.get("type", "Event")
    opponent = event.get("opponent", "").strip()
    return f"{etype}: {opponent}" if opponent else etype


def _event_color(event_type: str) -> str:
    t = event_type.lower()
    if "practice" in t:
        return COLOR_PRACTICE
    return COLOR_GAME  # default to blue for games and unknowns


def _iso_times(event: dict) -> tuple[str, str]:
    """Return (start_iso, end_iso) with duration defaults if end is absent."""
    date_str = event.get("date", "")
    time_str = event.get("time", "00:00") or "00:00"
    etype = event.get("type", "").lower()

    start: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            start = datetime.strptime(f"{date_str} {time_str}".strip(), fmt)
            break
        except ValueError:
            continue

    if start is None:
        # Fallback: midnight today
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    duration_key = (
        "practice" if "practice" in etype else
        "game" if "game" in etype else
        "default"
    )
    end = start + timedelta(minutes=DURATION_DEFAULTS[duration_key])
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# gog subprocess
# ---------------------------------------------------------------------------

def _run_gog(args: list[str]) -> tuple[bool, str]:
    """Run `gog <args>`. Returns (success, stdout-or-stderr)."""
    result = subprocess.run(
        ["gog"] + args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout.strip()


def _parse_gcal_event_id(gog_output: str) -> str:
    """Extract Google Calendar event ID from gog create output.

    gog typically returns JSON with an "id" field.
    Falls back to raw output if parsing fails.
    NOTE: Verify actual output format with:
        gog calendar create <test_cal_id> --summary "test" --from <iso> --to <iso> --no-input
    on 192.168.1.14 and adjust this function if needed.
    """
    import re

    try:
        data = json.loads(gog_output)
        return str(data.get("id") or data.get("eventId") or gog_output.strip())
    except (json.JSONDecodeError, AttributeError):
        pass

    m = re.search(r'"id":\s*"([^"]+)"', gog_output)
    if m:
        return m.group(1)

    # Last resort: return raw trimmed output
    return gog_output.strip()


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync_team(
    events: list[dict],
    calendar_id: str,
    gc_dir: Path,
    dry_run: bool = False,
) -> SyncResult:
    """Diff events against sync-state.json and call gog for each change.

    - new events     → gog calendar create
    - changed events → gog calendar update
    - removed events → gog calendar update with [CANCELLED] prefix + grey color
    """
    if not dry_run and not shutil.which("gog"):
        raise RuntimeError(
            "gog not found on PATH. Is it installed on this machine?\n"
            "See: https://gogcli.sh"
        )

    state = load_state(gc_dir)
    result = SyncResult()
    incoming = {e["id"]: e for e in events if e.get("id")}

    # --- new and changed events ------------------------------------------
    for gc_id, event in incoming.items():
        fp = event_fingerprint(event)
        summary = _event_summary(event)
        start_iso, end_iso = _iso_times(event)
        color = _event_color(event.get("type", ""))

        if gc_id not in state:
            # New
            print(f"  + CREATE  {summary} ({start_iso})", file=sys.stderr)
            if not dry_run:
                ok, out = _run_gog([
                    "calendar", "create", calendar_id,
                    "--summary", summary,
                    "--from", start_iso,
                    "--to", end_iso,
                    "--event-color", color,
                    "--no-input",
                ])
                if ok:
                    gcal_id = _parse_gcal_event_id(out)
                    state[gc_id] = {
                        "gcal_event_id": gcal_id,
                        "calendar_id": calendar_id,
                        "fingerprint": fp,
                        "summary": summary,
                    }
                    result.created.append(summary)
                else:
                    print(f"  ERROR create {summary}: {out}", file=sys.stderr)
                    result.errors.append(f"create {summary}: {out}")
            else:
                result.created.append(summary)

        elif state[gc_id]["fingerprint"] != fp:
            # Changed
            gcal_event_id = state[gc_id]["gcal_event_id"]
            print(f"  ~ UPDATE  {summary} ({start_iso})", file=sys.stderr)
            if not dry_run:
                ok, out = _run_gog([
                    "calendar", "update", calendar_id, gcal_event_id,
                    "--summary", summary,
                    "--from", start_iso,
                    "--to", end_iso,
                    "--event-color", color,
                    "--no-input",
                ])
                if ok:
                    state[gc_id]["fingerprint"] = fp
                    state[gc_id]["summary"] = summary
                    result.updated.append(summary)
                else:
                    print(f"  ERROR update {summary}: {out}", file=sys.stderr)
                    result.errors.append(f"update {summary}: {out}")

        # else: unchanged — no gog call

    # --- removed events --------------------------------------------------
    removed_ids = set(state.keys()) - set(incoming.keys())
    for gc_id in removed_ids:
        entry = state[gc_id]
        gcal_event_id = entry["gcal_event_id"]
        original_summary = entry.get("summary", "Event")
        cancelled_summary = f"[CANCELLED] {original_summary}"
        print(f"  x CANCEL  {original_summary} (gcal:{gcal_event_id})", file=sys.stderr)
        if not dry_run:
            ok, out = _run_gog([
                "calendar", "update", calendar_id, gcal_event_id,
                "--summary", cancelled_summary,
                "--event-color", COLOR_CANCELLED,
                "--no-input",
            ])
            if ok:
                del state[gc_id]
                result.cancelled.append(gcal_event_id)
            else:
                print(f"  ERROR cancel {gcal_event_id}: {out}", file=sys.stderr)
                result.errors.append(f"cancel {gcal_event_id}: {out}")
        else:
            result.cancelled.append(gcal_event_id)

    if not dry_run:
        save_state(state, gc_dir)

    return result
```

- [ ] **Step 5.4: Run tests to confirm they pass**

```bash
pytest tests/test_sync.py -v
```

Expected: all 16 tests PASS

- [ ] **Step 5.5: Commit**

```bash
git add gc_cli/sync.py tests/test_sync.py
git commit -m "feat: add sync.py — gog diff loop with fingerprint-based change detection"
```

---

## Task 6: Update `cli.py` — wire session + add `gc sync`

**Files:**
- Modify: `gc_cli/cli.py`

- [ ] **Step 6.1: Replace `gc_cli/cli.py`**

```python
"""
gc — GameChanger team schedule & clips CLI scraper.

Usage:
    gc teams                                    — list teams for the authenticated user
    gc schedule  [--team ID] [--json]           — upcoming schedule for a team
    gc summary   [--team ID] [--json]           — schedule + clips in one shot
    gc sync      [--team ID] [--calendar ID]    — sync schedule to Google Calendar via gog
                 [--dry-run] [--visible]
"""

import argparse
import json
import os
import sys
from datetime import datetime

import requests

from gc_cli.client import (
    GCClient,
    _ensure_dir,
    _load_env,
    _load_teams,
    _save_teams,
    ENV_PATH,
    GC_DIR,
    TEAMS_PATH,
)
from gc_cli.session import get_session
from gc_cli.sync import sync_team, SyncResult


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_calendar_id() -> str | None:
    """Resolve GC_CALENDAR_ID from env or ~/.gc/.env."""
    cal_id = os.environ.get("GC_CALENDAR_ID")
    if cal_id:
        return cal_id
    env = _load_env()
    return env.get("GC_CALENDAR_ID")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def output_teams(teams: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(teams, indent=2))
        return
    if not teams:
        print("No teams found.")
        return
    print(f"\n{'Name':<30} {'ID':<25} {'Sport'}")
    print("-" * 65)
    for t in teams:
        print(f"{t.get('name',''):<30} {t.get('id',''):<25} {t.get('sport','')}")
    print()


def output_schedule(events: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(events, indent=2))
        return
    if not events:
        print("No upcoming events found.")
        return
    print(f"\n{'Date':<14} {'Time':<8} {'Type':<12} {'Opponent / Title'}")
    print("-" * 55)
    for ev in events:
        date = ev.get("date", "")[:13]
        time = ev.get("time", "")[:7]
        etype = ev.get("type", "")[:11]
        title = ev.get("opponent", ev.get("title", ""))[:35]
        print(f"{date:<14} {time:<8} {etype:<12} {title}")
    print()


def output_summary(summary: dict, as_json: bool) -> None:
    if as_json:
        summary["timestamp"] = datetime.now().isoformat()
        print(json.dumps(summary, indent=2))
        return
    team_id = summary.get("team_id", "unknown")
    schedule = summary.get("schedule", [])
    clips = summary.get("clips", [])

    print(f"\n{'=' * 60}")
    print(f"  GameChanger Summary — Team: {team_id}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")

    print(f"\n--- Schedule ({len(schedule)} events) ---")
    output_schedule(schedule, False)

    print(f"\n--- Clips ({len(clips)} clips) ---")
    if not clips:
        print("No clips found.")
    else:
        for c in clips[:10]:
            print(f"  {c.get('title', 'Untitled')}")
    print()


def output_sync_result(result: SyncResult, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Sync complete:", file=sys.stderr)
    print(f"  Created:   {len(result.created)}", file=sys.stderr)
    print(f"  Updated:   {len(result.updated)}", file=sys.stderr)
    print(f"  Cancelled: {len(result.cancelled)}", file=sys.stderr)
    if result.errors:
        print(f"  Errors:    {len(result.errors)}", file=sys.stderr)
        for err in result.errors:
            print(f"    - {err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _resolve_team_id(args: argparse.Namespace, client: GCClient) -> str:
    if hasattr(args, "team") and args.team:
        return args.team

    teams = _load_teams()
    if teams:
        tid = teams[0].get("id", "")
        if tid:
            return tid

    api_teams = client.get_my_teams()
    if not api_teams:
        raise RuntimeError(
            "No teams found. Run 'gc teams' to list available teams, "
            "then save to ~/.gc/teams.json"
        )
    return api_teams[0].get("id", "")


def cmd_teams(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    teams = client.get_my_teams()
    output_teams(teams, args.json)

    if teams and not TEAMS_PATH.exists() and not args.json:
        print("Tip: save teams for cron use:")
        print(f"  gc teams --json > {TEAMS_PATH}")


def cmd_schedule(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    events = client.get_schedule(team_id)
    output_schedule(events, args.json)


def cmd_summary(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    summary = client.get_team_summary(team_id)
    output_summary(summary, args.json)


def cmd_sync(args: argparse.Namespace) -> None:
    calendar_id = args.calendar or _get_calendar_id()
    if not calendar_id:
        print(
            "Error: GC_CALENDAR_ID not set.\n"
            "Set it via env var or add to ~/.gc/.env:\n"
            '  GC_CALENDAR_ID="your-calendar-id@group.calendar.google.com"',
            file=sys.stderr,
        )
        sys.exit(1)

    session = get_session(verbose=True, visible=args.visible)
    client = GCClient(session, verbose=True)
    team_id = _resolve_team_id(args, client)

    print(f"  Fetching schedule for team {team_id}...", file=sys.stderr)
    events = client.get_schedule(team_id)
    print(f"  {len(events)} events fetched", file=sys.stderr)

    result = sync_team(events, calendar_id, GC_DIR, dry_run=args.dry_run)
    output_sync_result(result, args.dry_run)

    if result.errors:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gc",
        description="GameChanger team schedule & clips CLI scraper",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # gc teams
    sp_teams = subparsers.add_parser("teams", help="List your teams")
    sp_teams.add_argument("--json", action="store_true", help="JSON output")
    sp_teams.set_defaults(func=cmd_teams)

    # gc schedule
    sp_sched = subparsers.add_parser("schedule", help="Upcoming schedule")
    sp_sched.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sched.add_argument("--json", action="store_true", help="JSON output")
    sp_sched.set_defaults(func=cmd_schedule)

    # gc summary
    sp_sum = subparsers.add_parser("summary", help="Schedule + clips (all data)")
    sp_sum.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sum.add_argument("--json", action="store_true", help="JSON output")
    sp_sum.set_defaults(func=cmd_summary)

    # gc sync
    sp_sync = subparsers.add_parser("sync", help="Sync schedule to Google Calendar via gog")
    sp_sync.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sync.add_argument("--calendar", type=str, help="Google Calendar ID (default: GC_CALENDAR_ID)")
    sp_sync.add_argument("--dry-run", action="store_true", help="Show planned changes without calling gog")
    sp_sync.add_argument("--visible", action="store_true", help="Run Playwright in headed mode (debug Cloudflare)")
    sp_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6.2: Verify import**

```bash
python -c "from gc_cli.cli import main; print('ok')"
```

Expected: `ok`

- [ ] **Step 6.3: Run full test suite**

```bash
pytest tests/ -v --ignore=tests/e2e
```

Expected: all tests PASS

- [ ] **Step 6.4: Commit**

```bash
git add gc_cli/cli.py
git commit -m "feat: cli.py — wire Playwright session into all commands, add gc sync"
```

---

## Task 7: Update `.env.example` and `README.md`

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 7.1: Replace `.env.example`**

```bash
# GameChanger credentials
# Extract from your account: https://app.gc.com
GC_EMAIL="you@example.com"
GC_PASSWORD="yourpassword"

# Google Calendar ID to sync schedule into
# Find in Google Calendar settings → "Calendar ID"
GC_CALENDAR_ID="your-calendar-id@group.calendar.google.com"

# Optional: gog account override (defaults to gog's configured default)
# GOG_ACCOUNT="you@gmail.com"
```

- [ ] **Step 7.2: Update `README.md` Setup section**

Replace the Setup section with:

```markdown
## Setup

```bash
pip install -e .
pip install -e ".[browser]"
playwright install chromium
```

Configure credentials:

```bash
mkdir -p ~/.gc && chmod 700 ~/.gc
cat > ~/.gc/.env << 'EOF'
GC_EMAIL="you@example.com"
GC_PASSWORD="yourpassword"
GC_CALENDAR_ID="your-calendar-id@group.calendar.google.com"
EOF
chmod 600 ~/.gc/.env
```

Then discover and save your teams:

```bash
gc teams --json > ~/.gc/teams.json
```

| Variable | Required | Description |
|---|---|---|
| `GC_EMAIL` | yes | GameChanger account email |
| `GC_PASSWORD` | yes | GameChanger account password |
| `GC_CALENDAR_ID` | yes (for `gc sync`) | Google Calendar ID to sync into |
| `GOG_ACCOUNT` | no | Override gog account for calendar writes |

Config priority: env vars > `~/.gc/.env`
```

- [ ] **Step 7.3: Update `README.md` Usage section**

Replace the Usage section with:

```markdown
## Usage

```
gc teams                                          # list teams
gc schedule  [--team ID] [--json]                 # upcoming schedule
gc summary   [--team ID] [--json]                 # schedule + clips in one shot
gc sync      [--team ID] [--calendar ID]          # sync to Google Calendar
             [--dry-run] [--visible]
```

All commands accept `--json` for machine-readable output.
If `--team` is omitted, uses the first team from `~/.gc/teams.json`.
`gc sync --dry-run` shows planned creates/updates/cancels without calling gog.
`gc sync --visible` opens a browser window to debug Cloudflare login issues.
```

- [ ] **Step 7.4: Commit**

```bash
git add .env.example README.md
git commit -m "docs: update README and .env.example for Playwright auth and gc sync"
```

---

## Task 8: E2E test on `192.168.1.14`

**Files:**
- Create: `tests/e2e/test_sync_e2e.sh`
- Create: `tests/e2e/.env.example`

- [ ] **Step 8.1: Create `tests/e2e/` directory and `.env.example`**

```bash
mkdir -p tests/e2e
```

Create `tests/e2e/.env.example` (committed — no secrets):

```bash
# Copy to tests/e2e/.env (gitignored) and fill in real values
GC_EMAIL="you@example.com"
GC_PASSWORD="yourpassword"
GC_TEST_CALENDAR_ID="your-test-calendar-id@group.calendar.google.com"
# GOG_ACCOUNT="you@gmail.com"
```

- [ ] **Step 8.2: Verify `gog calendar create` output format on `.14` (before writing test)**

SSH to `.14` and run a test create + immediate list to confirm the output shape:

```bash
ssh root@192.168.1.14

# Create a throwaway test event (use your test calendar ID)
gog calendar create "$GC_TEST_CALENDAR_ID" \
  --summary "gc e2e test event" \
  --from "$(date -d '+1 day' -Iseconds)" \
  --to "$(date -d '+1 day +2 hours' -Iseconds)" \
  --no-input

# Inspect the output — note what fields are returned (id, eventId, etc.)
# Update _parse_gcal_event_id in sync.py if the "id" field name differs
```

- [ ] **Step 8.3: Create `tests/e2e/test_sync_e2e.sh`**

```bash
#!/usr/bin/env bash
# E2E test for gc sync — run on 192.168.1.14 where gog is authenticated.
#
# Prerequisites:
#   - gc installed: pip install -e '/opt/gc'
#   - gog on PATH and authenticated
#   - tests/e2e/.env present with GC_TEST_CALENDAR_ID, GC_EMAIL, GC_PASSWORD
#
# Run:
#   ssh root@192.168.1.14 'bash /opt/gc/tests/e2e/test_sync_e2e.sh'

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example and fill in values." >&2
  exit 1
fi

# shellcheck source=/dev/null
set -a
source "$ENV_FILE"
set +a

: "${GC_TEST_CALENDAR_ID:?must be set in tests/e2e/.env}"
: "${GC_EMAIL:?must be set in tests/e2e/.env}"
: "${GC_PASSWORD:?must be set in tests/e2e/.env}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# --- Step 1: dry run should succeed without calling gog -------------------
log "Step 1: dry-run sync..."
gc sync --calendar "$GC_TEST_CALENDAR_ID" --dry-run
log "  dry-run OK"

# --- Step 2: real sync ----------------------------------------------------
log "Step 2: real sync..."
gc sync --calendar "$GC_TEST_CALENDAR_ID"
log "  sync OK"

# --- Step 3: verify events appeared in Google Calendar -------------------
log "Step 3: verifying events in Google Calendar..."
EVENT_COUNT=$(gog calendar events "$GC_TEST_CALENDAR_ID" \
  --from "$(date -I)" \
  --to "$(date -d '+60 days' -I)" \
  --json | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data) if isinstance(data, list) else len(data.get('items', [])))")

if [[ "$EVENT_COUNT" -lt 1 ]]; then
  log "FAIL: No events found in calendar after sync"
  exit 1
fi
log "  Found $EVENT_COUNT event(s) — OK"

# --- Step 4: second sync should be a no-op (all unchanged) ---------------
log "Step 4: second sync (should be no-op)..."
SECOND_OUTPUT=$(gc sync --calendar "$GC_TEST_CALENDAR_ID" 2>&1)
if echo "$SECOND_OUTPUT" | grep -q "CREATE\|UPDATE"; then
  log "FAIL: second sync created/updated events unexpectedly"
  echo "$SECOND_OUTPUT"
  exit 1
fi
log "  no-op OK"

log ""
log "E2E PASSED — $EVENT_COUNT events synced to Google Calendar"
```

```bash
chmod +x tests/e2e/test_sync_e2e.sh
```

- [ ] **Step 8.4: Commit**

```bash
git add tests/e2e/
git commit -m "test: add E2E test script for gc sync on 192.168.1.14"
```

- [ ] **Step 8.5: Deploy to `.14` and run E2E**

```bash
# Deploy latest code to LXC
ssh root@192.168.1.14 'cd /opt/gc && git pull && pip install -e . --break-system-packages -q'

# Create tests/e2e/.env on .14 (gitignored — enter manually)
ssh root@192.168.1.14 'cat > /opt/gc/tests/e2e/.env << EOF
GC_EMAIL="your-real-email"
GC_PASSWORD="your-real-password"
GC_TEST_CALENDAR_ID="your-test-calendar-id"
EOF
chmod 600 /opt/gc/tests/e2e/.env'

# Run E2E
ssh root@192.168.1.14 'bash /opt/gc/tests/e2e/test_sync_e2e.sh'
```

Expected: `E2E PASSED — N events synced to Google Calendar`

- [ ] **Step 8.6: Push all commits**

```bash
git push
```

At this point CI triggers, tests run, and the patch version bumps automatically on green `main`.

---

## Task 9: Update `school/CLAUDE.md` cron entry

**Files:**
- Modify: `../CLAUDE.md` (parent `school/CLAUDE.md`)

- [ ] **Step 9.1: Add `gc sync` to cron table in `school/CLAUDE.md`**

In the `## Cron Integration` section of `/var/home/user/Documents/vibe-code/openclaw-programs/school/CLAUDE.md`, verify the cron table includes the sync line (already added in the docs update pass):

```bash
10 6 * * * /opt/gc/cron/gc-cron.sh 2>/tmp/gc-cron.log
15 6 * * * gc sync 2>/tmp/gc-sync.log
```

If not present, add it. No commit needed for this file (it lives in the parent monorepo).

---

## Self-Review Checklist

Before marking this plan complete, verify:

- [ ] All `session.py` public functions match what `cli.py` calls (`get_session(verbose, visible)`)
- [ ] `client.py` `GCClient(session, verbose)` matches usage in `cli.py`
- [ ] `sync_team(events, calendar_id, gc_dir, dry_run)` signature matches `cmd_sync` call
- [ ] `SyncResult` fields (`created`, `updated`, `cancelled`, `errors`) match `output_sync_result`
- [ ] `_parse_gcal_event_id` verified against real `gog calendar create` output (Task 8, Step 8.2)
- [ ] `.env.example` has no real credentials
- [ ] `tests/e2e/.env` is in `.gitignore`
