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
