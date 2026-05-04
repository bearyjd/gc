"""
GameChanger session manager.

Handles Playwright login and session caching. Modeled on ixl_cli/session.py.
Playwright is a lazy import — only loaded when a real login is needed.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from gc_cli.client import GC_DIR


def _is_user_token(token: str) -> bool:
    """Return True iff token is a JWT with payload field ``type == "user"``.

    GameChanger's SPA uses two JWT shapes on api.team-manager.gc.com:
    a short-lived ``type: client`` device token (10-min TTL) issued during
    bootstrap, and a ``type: user`` token issued post-login. Capturing the
    client token by mistake breaks /me/teams (401) — so the capture path
    must filter to user tokens only.
    """
    if not token or not token.startswith("eyJ"):
        return False
    parts = token.split(".")
    if len(parts) < 2:
        return False
    try:
        # JWT payload is base64url; pad to a multiple of 4 for stdlib decode.
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded).get("type") == "user"
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False

SESSION_DIR = GC_DIR / "sessions"
SESSION_TTL_MINUTES = 60
CONTEXT_PATH = SESSION_DIR / "playwright_context.json"

# Required for headless Chromium running as root inside containers (LXC/Docker).
# --no-sandbox: root cannot use the Chromium sandbox.
# --disable-blink-features=AutomationControlled: suppresses bot-detection header.
# --disable-dev-shm-usage: avoids /dev/shm exhaustion in constrained environments.
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
]


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


def _capture_gc_headers_from_page(page) -> tuple[str | None, str | None]:  # type: ignore[no-untyped-def]
    """Intercept gc-token and gc-device-id from API request headers.

    Returns (gc_token, gc_device_id). Either may be None if not observed.
    """
    captured_token: list[str] = []
    captured_device: list[str] = []

    def handle_response(response) -> None:  # type: ignore[no-untyped-def]
        if "api.team-manager.gc.com" not in response.url:
            return
        try:
            h = response.request.all_headers()
            t = h.get("gc-token", "")
            if _is_user_token(t) and not captured_token:
                captured_token.append(t)
            d = h.get("gc-device-id", "")
            if d and not captured_device:
                captured_device.append(d)
        except Exception:
            pass

    page.on("response", handle_response)
    page.goto("https://web.gc.com/home", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(15000)
    page.remove_listener("response", handle_response)

    # Fallback: read gc-token from browser storage if network interception missed it
    if not captured_token:
        try:
            raw = page.evaluate("""() => {
                for (const store of [localStorage, sessionStorage]) {
                    for (let i = 0; i < store.length; i++) {
                        const val = store.getItem(store.key(i));
                        if (val && val.startsWith('eyJ') && val.length > 200)
                            return val;
                        try {
                            const s = JSON.stringify(JSON.parse(val));
                            const m = s.match(/"(eyJ[A-Za-z0-9._-]{200,})"/);
                            if (m) return m[1];
                        } catch (e) {}
                    }
                }
                return null;
            }""")
            if isinstance(raw, str) and _is_user_token(raw):
                captured_token.append(raw)
        except Exception:
            pass

    return (
        captured_token[0] if captured_token else None,
        captured_device[0] if captured_device else None,
    )


def _make_session(gc_token: str, gc_device_id: str | None = None) -> requests.Session:
    """Build an authenticated requests.Session with the correct GC headers."""
    session = requests.Session()
    headers: dict[str, str] = {
        "gc-token": gc_token,
        "gc-app-name": "web",
        "Accept": "application/json",
    }
    if gc_device_id:
        headers["gc-device-id"] = gc_device_id
    session.headers.update(headers)
    return session


def _fetch_gc_otp(timeout_sec: int = 120) -> str:
    """Poll Gmail via gog for a GameChanger OTP email.

    Calls ``gog gmail search`` every 5 s until a 6-digit code is found in an
    email subject from gamechanger-noreply@info.gc.com or timeout_sec elapses.

    Passes ``--account <GOG_ACCOUNT>`` when that env var is set (mirrors
    existing _run_gog convention). Sets GOG_KEYRING_PASSWORD="" so the
    subprocess does not block waiting for a keyring prompt on headless systems.

    Returns the 6-digit code as a string.
    Raises RuntimeError on timeout or if gog is not installed.
    """
    account = os.environ.get("GOG_ACCOUNT")
    if account:
        cmd = [
            "gog", "--account", account,
            "gmail", "search",
            "from:gamechanger-noreply@info.gc.com newer_than:5m",
            "-j", "--results-only", "--no-input",
        ]
    else:
        cmd = [
            "gog", "gmail", "search",
            "from:gamechanger-noreply@info.gc.com newer_than:5m",
            "-j", "--results-only", "--no-input",
        ]

    env = os.environ.copy()
    env.setdefault("GOG_KEYRING_PASSWORD", "")

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, env=env
            )
        except FileNotFoundError:
            raise RuntimeError(
                "gog is not installed or not on PATH. "
                "Install gog to enable auto-OTP fetching from Gmail."
            )

        if result.returncode == 0:
            try:
                messages = json.loads(result.stdout)
                for msg in messages:
                    subject = msg.get("subject", "")
                    match = re.search(r"\b(\d{6})\b", subject)
                    if match:
                        return match.group(1)
            except (json.JSONDecodeError, AttributeError):
                pass

        time.sleep(5)

    raise RuntimeError(f"GC OTP not received within {timeout_sec}s")


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
    gc_device_id: str | None = None
    captured_token: list[str] = []
    captured_device: list[str] = []

    def _handle(response) -> None:  # type: ignore[no-untyped-def]
        if "api.team-manager.gc.com" not in response.url:
            return
        try:
            h = response.request.all_headers()
            t = h.get("gc-token", "")
            if _is_user_token(t) and not captured_token:
                captured_token.append(t)
            d = h.get("gc-device-id", "")
            if d and not captured_device:
                captured_device.append(d)
        except Exception:
            pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
            context = browser.new_context(storage_state=str(CONTEXT_PATH))
            page = context.new_page()
            # Handler active from the start so re-auth API calls are also captured
            page.on("response", _handle)

            # Navigate to home and detect if the JWT has expired (login form in-place)
            page.goto("https://web.gc.com/home", timeout=60000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector('input[type="email"]', timeout=25000)
                needs_reauth = True
            except Exception:
                needs_reauth = False  # login form never appeared → session still valid

            if needs_reauth:
                # JWT expired but device cookie saved → re-auth with email+password (no OTP)
                if verbose:
                    print("  Session expired — re-authenticating (no OTP)...", file=sys.stderr)
                try:
                    email, password = _get_credentials()
                    page.fill('input[type="email"]', email)
                    page.click('button:has-text("Continue")')
                    page.wait_for_selector('input[name="password"]', timeout=10000)
                    page.fill('input[name="password"]', password)
                    page.click('button:has-text("Sign in")')
                    # Wait for login form to clear (auth completed in-place)
                    page.wait_for_selector(
                        'input[type="email"]', state="detached", timeout=60000
                    )
                    if verbose:
                        print("  Re-authenticated (no OTP)", file=sys.stderr)
                except Exception as e:
                    if verbose:
                        print(f"  Re-auth failed: {e}", file=sys.stderr)
                    browser.close()
                    return None

                # Navigate to /teams to force authenticated API calls after re-auth
                page.goto("https://web.gc.com/teams", timeout=30000, wait_until="domcontentloaded")

            # Wait for post-auth API calls and attempt localStorage fallback
            page.wait_for_timeout(12000)

            # Fallback: read gc-token from browser storage if network interception missed it
            if not captured_token:
                try:
                    raw = page.evaluate("""() => {
                        for (const store of [localStorage, sessionStorage]) {
                            for (let i = 0; i < store.length; i++) {
                                const val = store.getItem(store.key(i));
                                if (val && val.startsWith('eyJ') && val.length > 200)
                                    return val;
                                try {
                                    const s = JSON.stringify(JSON.parse(val));
                                    const m = s.match(/"(eyJ[A-Za-z0-9._-]{200,})"/);
                                    if (m) return m[1];
                                } catch (e) {}
                            }
                        }
                        return null;
                    }""")
                    if isinstance(raw, str) and _is_user_token(raw):
                        captured_token.append(raw)
                except Exception:
                    pass

            gc_token = captured_token[0] if captured_token else None
            gc_device_id = captured_device[0] if captured_device else None

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

    session = _make_session(gc_token, gc_device_id)

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
    gc_device_id: str | None = None

    def handle_response(response) -> None:  # type: ignore[no-untyped-def]
        nonlocal gc_token, gc_device_id
        if "api.team-manager.gc.com" not in response.url:
            return
        try:
            h = response.request.all_headers()
            t = h.get("gc-token", "")
            if _is_user_token(t) and not gc_token:
                gc_token = t
            d = h.get("gc-device-id", "")
            if d and not gc_device_id:
                gc_device_id = d
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not visible, args=_CHROMIUM_ARGS)
        context = browser.new_context()
        page = context.new_page()
        page.on("response", handle_response)

        try:
            page.goto("https://web.gc.com/", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_selector('input[type="email"]', timeout=30000)
            page.fill('input[type="email"]', email)
            page.click('button:has-text("Continue")')
            page.wait_for_selector('input[name="password"]', timeout=15000)

            page.fill('input[name="password"]', password)
            page.click('button:has-text("Sign in")')

            if visible:
                # Visible mode: wait for the user to complete login + OTP,
                # then navigate to /home to force authenticated API calls.
                #
                # We do NOT try to intercept tokens during the login/OTP flow
                # because GC's SPA may not call api.team-manager.gc.com until
                # the home page loads.
                print(
                    "  Browser open — log in and enter the OTP code when prompted.",
                    file=sys.stderr,
                )
                # Wait up to 5 min for GC to redirect away from the login/OTP page
                try:
                    page.wait_for_url(
                        lambda url: "login" not in url and "verify" not in url,
                        timeout=300_000,
                    )
                except Exception:
                    pass  # timed out — try to proceed anyway

                # Navigate to home to trigger authenticated API calls
                try:
                    current_url = page.url
                except Exception:
                    current_url = ""

                if current_url and "login" not in current_url:
                    print("  Login complete — capturing token...", file=sys.stderr)
                    page.goto("https://web.gc.com/teams", timeout=60_000, wait_until="domcontentloaded")
                    page.wait_for_timeout(5_000)

                    # Fallback: read token directly from browser storage if
                    # network interception didn't capture it from headers
                    if not gc_token:
                        try:
                            raw = page.evaluate("""() => {
                                for (const store of [localStorage, sessionStorage]) {
                                    for (let i = 0; i < store.length; i++) {
                                        const val = store.getItem(store.key(i));
                                        if (val && val.startsWith('eyJ') && val.length > 200)
                                            return val;
                                        try {
                                            const s = JSON.stringify(JSON.parse(val));
                                            const m = s.match(/"(eyJ[A-Za-z0-9._-]{200,})"/);
                                            if (m) return m[1];
                                        } catch (e) {}
                                    }
                                }
                                return null;
                            }""")
                            if isinstance(raw, str) and _is_user_token(raw):
                                gc_token = raw
                        except Exception:
                            pass
            else:
                # Headless auto-OTP: poll for OTP field, fetch code from Gmail,
                # submit — no human interaction required.
                _OTP_SELECTOR = (
                    'input[autocomplete="one-time-code"], '
                    'input[type="tel"], '
                    'input[inputmode="numeric"]'
                )
                _deadline = time.monotonic() + 300  # 5-minute window
                while time.monotonic() < _deadline:
                    _cur_url = page.url
                    if "login" not in _cur_url and "verify" not in _cur_url:
                        break  # login completed without OTP
                    try:
                        page.wait_for_selector(_OTP_SELECTOR, timeout=10000)
                    except Exception:
                        pass  # OTP field not present yet — keep polling
                    else:
                        # OTP input detected — fetch code from Gmail and fill it
                        code = _fetch_gc_otp(timeout_sec=120)
                        page.fill(_OTP_SELECTOR, code)
                        page.click('button[type="submit"]')
                        try:
                            page.wait_for_url(
                                lambda url: "login" not in url and "verify" not in url,
                                timeout=60_000,
                            )
                        except Exception:
                            pass
                        break
                    page.wait_for_timeout(5000)

                # Navigate to /teams to trigger authenticated API calls
                page.goto("https://web.gc.com/teams", timeout=60_000, wait_until="domcontentloaded")
                page.wait_for_timeout(15_000)

                # Fallback: read gc-token from browser storage if network interception missed it
                if not gc_token:
                    try:
                        raw = page.evaluate("""() => {
                            for (const store of [localStorage, sessionStorage]) {
                                for (let i = 0; i < store.length; i++) {
                                    const val = store.getItem(store.key(i));
                                    if (val && val.startsWith('eyJ') && val.length > 200)
                                        return val;
                                    try {
                                        const s = JSON.stringify(JSON.parse(val));
                                        const m = s.match(/"(eyJ[A-Za-z0-9._-]{200,})"/);
                                        if (m) return m[1];
                                    } catch (e) {}
                                }
                            }
                            return null;
                        }""")
                        if isinstance(raw, str) and _is_user_token(raw):
                            gc_token = raw
                    except Exception:
                        pass
        except RuntimeError:
            raise
        except Exception as e:
            browser.close()
            hint = " Try --visible to debug." if not visible else ""
            raise RuntimeError(f"GameChanger login failed: {e}.{hint}") from e

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

    session = _make_session(gc_token, gc_device_id)
    for cookie in raw_cookies:
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain", "")
        )
    return session


def _update_env_token(token: str, device_id: str | None = None) -> None:
    """Write a fresh gc-token (and optionally gc-device-id) into ~/.gc/.env.

    Preserves all other lines. Creates the file if it doesn't exist.
    """
    env_path = GC_DIR / ".env"
    GC_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    def _set(key: str, value: str, rows: list[str]) -> list[str]:
        """Replace existing key=value or append if not found."""
        new_line = f'{key}="{value}"'
        for i, row in enumerate(rows):
            stripped = row.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                rows[i] = new_line
                return rows
        rows.append(new_line)
        return rows

    lines = _set("GC_TOKEN", token, lines)
    if device_id:
        lines = _set("GC_DEVICE_ID", device_id, lines)

    env_path.write_text("\n".join(lines) + "\n")
    env_path.chmod(0o600)


def _token_from_env() -> tuple[str | None, str | None]:
    """Return (GC_TOKEN, GC_DEVICE_ID) from env or ~/.gc/.env."""
    token = os.environ.get("GC_TOKEN")
    device_id = os.environ.get("GC_DEVICE_ID")

    if not token or not device_id:
        env_path = GC_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if k.strip() == "GC_TOKEN" and not token:
                    token = v
                elif k.strip() == "GC_DEVICE_ID" and not device_id:
                    device_id = v

    return token, device_id


def get_session(verbose: bool = True, visible: bool = False) -> requests.Session:
    """Return an authenticated requests.Session.

    Auth priority:
      1. GC_TOKEN env var or ~/.gc/.env → direct token (no Playwright)
      2. Saved Playwright browser context (skips OTP)
      3. Fresh Playwright login at web.gc.com

    GC_DEVICE_ID is optional but recommended alongside GC_TOKEN — GameChanger
    uses it for device recognition and may skip OTP when it is present.
    """
    token, device_id = _token_from_env()
    if token:
        if verbose:
            print("  Using GC_TOKEN from env", file=sys.stderr)
        return _make_session(token, device_id)

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

    # Persist token to ~/.gc/.env so subsequent runs skip Playwright
    fresh_token = session.headers.get("gc-token", "")
    if fresh_token:
        _update_env_token(fresh_token, session.headers.get("gc-device-id") or None)
        if verbose:
            print("  Token written to ~/.gc/.env", file=sys.stderr)

    return session
