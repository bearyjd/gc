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
CONTEXT_PATH = SESSION_DIR / "playwright_context.json"


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
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None


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


def _capture_gc_token_from_page(page) -> str | None:  # type: ignore[no-untyped-def]
    """Intercept gc-token from API request headers by navigating to home."""
    captured: list[str] = []

    def handle_response(response) -> None:  # type: ignore[no-untyped-def]
        if "api.team-manager.gc.com" not in response.url:
            return
        try:
            h = response.request.all_headers()
            t = h.get("gc-token", "")
            if t and t.startswith("eyJ") and len(t) > 200:
                captured.append(t)
        except Exception:
            pass

    page.on("response", handle_response)
    page.goto("https://web.gc.com/home", timeout=30000)
    page.wait_for_timeout(5000)
    page.remove_listener("response", handle_response)
    return captured[0] if captured else None


def _try_context_login(verbose: bool = False) -> requests.Session | None:
    """Return a session from a saved Playwright context (no OTP needed).

    Restores browser cookies, navigates to trigger API calls, captures
    gc-token from request headers. Updates the saved context on success.
    Returns None if the context is missing, stale, or produces 401.
    """
    if not CONTEXT_PATH.exists():
        return None

    from playwright.sync_api import sync_playwright  # lazy import

    if verbose:
        print("  Trying saved browser session...", file=sys.stderr)

    gc_token: str | None = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(CONTEXT_PATH))
            page = context.new_page()

            gc_token = _capture_gc_token_from_page(page)

            # Refresh saved context (keeps cookies current)
            SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            context.storage_state(path=str(CONTEXT_PATH))
            CONTEXT_PATH.chmod(0o600)
            browser.close()
    except Exception as e:
        if verbose:
            print(f"  Saved session error: {e}", file=sys.stderr)
        return None

    if not gc_token:
        return None

    session = requests.Session()
    session.headers.update({
        "gc-token": gc_token,
        "gc-app-name": "web",
        "Accept": "application/json",
    })

    # Verify the token is accepted
    try:
        resp = session.get(
            "https://api.team-manager.gc.com/me/teams", timeout=15
        )
        if resp.status_code == 200:
            if verbose:
                print("  Saved session valid", file=sys.stderr)
            return session
    except Exception:
        pass

    return None


def _playwright_login(
    email: str, password: str, visible: bool = False
) -> requests.Session:
    """Run Playwright headless login. Saves browser context for future reuse.

    GameChanger requires an OTP on new-device logins. If an OTP page appears,
    this raises RuntimeError with instructions to set GC_TOKEN instead.
    """
    from playwright.sync_api import sync_playwright  # lazy import

    gc_token: str | None = None

    def handle_response(response) -> None:  # type: ignore[no-untyped-def]
        nonlocal gc_token
        if "api.team-manager.gc.com" not in response.url:
            return
        try:
            h = response.request.all_headers()
            t = h.get("gc-token", "")
            if t and t.startswith("eyJ") and len(t) > 200 and not gc_token:
                gc_token = t
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible)
        context = browser.new_context()
        page = context.new_page()
        page.on("response", handle_response)

        try:
            page.goto("https://web.gc.com/", timeout=30000)
            page.fill('input[type="email"]', email)
            page.click('button:has-text("Continue")')
            page.wait_for_url("**/login**", timeout=15000)
            page.wait_for_timeout(500)

            page.fill('input[name="password"]', password)
            page.click('button:has-text("Sign in")')
            page.wait_for_timeout(8000)

            # Check whether OTP is blocking completion
            visible_text = page.evaluate("() => document.body.innerText")
            if "sent a code" in visible_text.lower() or (
                "code" in visible_text.lower() and "sign in" in visible_text.lower()
            ):
                browser.close()
                raise RuntimeError(
                    "GameChanger requires a one-time verification code.\n"
                    "Set GC_TOKEN in ~/.gc/.env to skip Playwright login:\n"
                    "  1. Open web.gc.com in your browser and log in\n"
                    "  2. DevTools → Network → any api.team-manager.gc.com request\n"
                    "  3. Copy the 'gc-token' request header value\n"
                    "  4. Add to ~/.gc/.env:  GC_TOKEN=\"<paste here>\""
                )
        except RuntimeError:
            raise
        except Exception as e:
            browser.close()
            hint = " Try --visible to debug." if not visible else ""
            raise RuntimeError(f"GameChanger login failed: {e}.{hint}") from e

        # Wait a bit more for authenticated API calls to fire
        if not gc_token:
            page.wait_for_timeout(4000)

        # Save browser context so future calls skip OTP
        SESSION_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        context.storage_state(path=str(CONTEXT_PATH))
        CONTEXT_PATH.chmod(0o600)

        raw_cookies = context.cookies()
        browser.close()

    if not gc_token:
        raise RuntimeError(
            "Logged in but could not capture gc-token from API responses.\n"
            "Set GC_TOKEN manually (see instructions above)."
        )

    session = requests.Session()
    session.headers.update({
        "gc-token": gc_token,
        "gc-app-name": "web",
        "Accept": "application/json",
    })
    for cookie in raw_cookies:
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain", "")
        )
    return session


def _token_from_env() -> str | None:
    """Return GC_TOKEN from env or ~/.gc/.env if set."""
    token = os.environ.get("GC_TOKEN")
    if token:
        return token
    env_path = GC_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "GC_TOKEN":
                return v.strip().strip('"').strip("'")
    return None


def get_session(verbose: bool = True, visible: bool = False) -> requests.Session:
    """Return an authenticated requests.Session.

    Auth priority:
      1. GC_TOKEN env var or ~/.gc/.env → direct Bearer token (no Playwright)
      2. Cached Playwright session (60-min TTL)
      3. Fresh Playwright login at web.gc.com

    Use GC_TOKEN if GameChanger requires an OTP code on new device logins.
    """
    token = _token_from_env()
    if token:
        if verbose:
            print("  Using GC_TOKEN from env", file=sys.stderr)
        session = requests.Session()
        session.headers.update({
            "gc-token": token,
            "gc-app-name": "web",
            "Accept": "application/json",
        })
        return session

    email, password = _get_credentials()

    cached = _load_cached_session(email)
    if cached:
        if verbose:
            print("  Using cached session", file=sys.stderr)
        return cached

    # Try restoring from saved browser context (skips OTP)
    context_session = _try_context_login(verbose=verbose)
    if context_session:
        return context_session

    if verbose:
        print("  Logging in via Playwright...", file=sys.stderr)

    session = _playwright_login(email, password, visible=visible)

    if verbose:
        print("  Login successful, browser context saved", file=sys.stderr)

    return session
