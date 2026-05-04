"""Tests for gc_cli.session — cache logic and credential loading."""
import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

# We import only the pure functions — no Playwright triggered at import time
from gc_cli.session import (
    SESSION_TTL_MINUTES,
    _fetch_gc_otp,
    _get_credentials,
    _is_user_token,
    _load_cached_session,
    _save_session,
    _session_path,
    get_session,
)


def _jwt(payload: dict) -> str:
    """Build an unsigned JWT-shaped string with the given payload (test helper)."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = "x" * 200  # padding so length > 200 (matches old size heuristic for safety)
    return f"{header}.{body}.{sig}"


_USER_JWT = _jwt({"type": "user", "email": "u@x.com", "iat": 1, "exp": 9999999999})
_CLIENT_JWT = _jwt({"type": "client", "sid": "s", "cid": "c", "iat": 1, "exp": 600})


# ---------------------------------------------------------------------------
# _is_user_token
# ---------------------------------------------------------------------------

def test_is_user_token_accepts_user_type():
    """Real user JWT (type=user) is accepted."""
    assert _is_user_token(_USER_JWT) is True


def test_is_user_token_rejects_client_type():
    """Short-lived bootstrap JWT (type=client) is rejected — root cause of past 401 loop."""
    assert _is_user_token(_CLIENT_JWT) is False


def test_is_user_token_rejects_garbage():
    """Non-JWT strings, empty input, and malformed tokens are all rejected."""
    assert _is_user_token("") is False
    assert _is_user_token("not-a-jwt") is False
    assert _is_user_token("eyJ-only-one-segment") is False
    assert _is_user_token("eyJ.not-base64.sig") is False


def test_is_user_token_rejects_jwt_without_type_field():
    """A JWT missing the ``type`` claim is treated as non-user (defensive)."""
    no_type = _jwt({"email": "u@x.com", "iat": 1, "exp": 9999999999})
    assert _is_user_token(no_type) is False


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
    fresh_session.headers.update({"gc-token": "fresh-token"})

    with patch("gc_cli.session._try_context_login", return_value=None), \
         patch("gc_cli.session._playwright_login", return_value=fresh_session):
        result = get_session(verbose=False)

    assert result.headers.get("gc-token") == "fresh-token"


# ---------------------------------------------------------------------------
# _fetch_gc_otp
# ---------------------------------------------------------------------------

def _gog_result(subjects: list[str]) -> MagicMock:
    """Build a mock subprocess.CompletedProcess with gog gmail JSON output."""
    messages = [{"subject": s} for s in subjects]
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps(messages)
    return result


def test_fetch_gc_otp_returns_code_from_subject():
    """Returns 6-digit code found in gog output subject on first try."""
    with patch("subprocess.run", return_value=_gog_result(["Your GC code is 123456"])):
        code = _fetch_gc_otp(timeout_sec=30)
    assert code == "123456"


def test_fetch_gc_otp_retries_and_returns_code_on_second_attempt():
    """Retries every 5s and returns code when it eventually arrives."""
    results = [
        _gog_result([]),                                    # first poll: no email yet
        _gog_result(["GameChanger verification: 654321"]),  # second poll: code arrives
    ]
    with patch("subprocess.run", side_effect=results), \
         patch("time.sleep") as mock_sleep, \
         patch("time.monotonic", side_effect=[0.0, 0.0, 0.0, 60.0]):
        code = _fetch_gc_otp(timeout_sec=120)

    assert code == "654321"
    mock_sleep.assert_called_with(5)


def test_fetch_gc_otp_raises_runtime_error_after_timeout():
    """Raises RuntimeError(containing timeout duration) when code never arrives."""
    with patch("subprocess.run", return_value=_gog_result([])), \
         patch("time.sleep"), \
         patch("time.monotonic", side_effect=[0.0, 0.0, 31.0]):
        with pytest.raises(RuntimeError, match="GC OTP not received within 30"):
            _fetch_gc_otp(timeout_sec=30)


def test_fetch_gc_otp_includes_account_flag_when_gog_account_set(monkeypatch):
    """Passes --account <GOG_ACCOUNT> when that env var is set."""
    monkeypatch.setenv("GOG_ACCOUNT", "myacct@gmail.com")

    with patch("subprocess.run", return_value=_gog_result(["code 999888"])) as mock_run:
        _fetch_gc_otp(timeout_sec=30)

    cmd = mock_run.call_args[0][0]
    assert "--account" in cmd
    assert "myacct@gmail.com" in cmd


def test_fetch_gc_otp_omits_account_flag_when_env_not_set(monkeypatch):
    """Does not include --account when GOG_ACCOUNT is absent."""
    monkeypatch.delenv("GOG_ACCOUNT", raising=False)

    with patch("subprocess.run", return_value=_gog_result(["code 111222"])) as mock_run:
        _fetch_gc_otp(timeout_sec=30)

    cmd = mock_run.call_args[0][0]
    assert "--account" not in cmd


def test_fetch_gc_otp_raises_runtime_error_if_gog_missing():
    """Raises RuntimeError with 'gog' in the message if gog is not on PATH."""
    with patch("subprocess.run", side_effect=FileNotFoundError("gog")):
        with pytest.raises(RuntimeError, match="gog"):
            _fetch_gc_otp(timeout_sec=30)


# ---------------------------------------------------------------------------
# _playwright_login — headless OTP auto-entry
# ---------------------------------------------------------------------------

def _make_playwright_mocks(
    *,
    otp_field_found: bool = False,
    gc_token: str = _USER_JWT,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Return (mock_pw_cm, mock_browser, mock_context, mock_page)."""
    mock_page = MagicMock()

    if otp_field_found:
        # URL stays on verify until OTP submitted, then moves to home
        urls = ["https://web.gc.com/login/verify/otp"] * 3 + ["https://web.gc.com/home"]
        type(mock_page).url = PropertyMock(side_effect=urls)
    else:
        type(mock_page).url = PropertyMock(return_value="https://web.gc.com/home")

    # wait_for_selector: succeed for email/password, fail for OTP when not found
    def _wfs(selector, **kwargs):
        if "email" in selector or "password" in selector:
            return MagicMock()
        if otp_field_found:
            return MagicMock()
        raise Exception("timeout")

    mock_page.wait_for_selector.side_effect = _wfs

    # Simulate the response handler being invoked with a real-looking API response
    mock_api_response = MagicMock()
    mock_api_response.url = "https://api.team-manager.gc.com/me/teams"
    mock_api_response.request.all_headers.return_value = {
        "gc-token": gc_token,
        "gc-device-id": "dev-abc",
    }

    def capture_handler(event: str, handler) -> None:  # type: ignore[no-untyped-def]
        if event == "response":
            handler(mock_api_response)

    mock_page.on.side_effect = capture_handler

    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.cookies.return_value = []
    # Simulate storage_state writing the file so CONTEXT_PATH.chmod() doesn't fail
    mock_context.storage_state.side_effect = lambda path=None, **kw: (
        Path(path).write_text("{}") if path else None
    )

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    mock_pw_cm = MagicMock()
    mock_pw_cm.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_cm.__exit__ = MagicMock(return_value=False)

    return mock_pw_cm, mock_browser, mock_context, mock_page


def test_playwright_login_headless_calls_fetch_otp_when_otp_field_detected(tmp_gc_dir, monkeypatch):
    """Headless _playwright_login calls _fetch_gc_otp when OTP input is present on page."""
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setattr("gc_cli.session.CONTEXT_PATH", tmp_gc_dir / "sessions" / "ctx.json")

    mock_pw_cm, _, _, mock_page = _make_playwright_mocks(otp_field_found=True)

    # sync_playwright is a lazy import inside _playwright_login; patch where it's imported from
    with patch("playwright.sync_api.sync_playwright", return_value=mock_pw_cm), \
         patch("gc_cli.session._fetch_gc_otp", return_value="123456") as mock_otp:
        from gc_cli.session import _playwright_login
        _playwright_login("user@gc.com", "pass", visible=False)

    mock_otp.assert_called_once()


def test_playwright_login_headless_fills_otp_code_into_page(tmp_gc_dir, monkeypatch):
    """Headless _playwright_login types the fetched OTP code into the OTP input field."""
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setattr("gc_cli.session.CONTEXT_PATH", tmp_gc_dir / "sessions" / "ctx.json")

    mock_pw_cm, _, _, mock_page = _make_playwright_mocks(otp_field_found=True)

    with patch("playwright.sync_api.sync_playwright", return_value=mock_pw_cm), \
         patch("gc_cli.session._fetch_gc_otp", return_value="987654"):
        from gc_cli.session import _playwright_login
        _playwright_login("user@gc.com", "pass", visible=False)

    # Verify that page.fill was called with "987654" for some selector
    fill_calls = [str(c) for c in mock_page.fill.call_args_list]
    assert any("987654" in c for c in fill_calls), (
        f"Expected '987654' to be typed into OTP field. fill calls: {fill_calls}"
    )


def test_playwright_login_headless_skips_otp_when_no_otp_field(tmp_gc_dir, monkeypatch):
    """Headless _playwright_login does NOT call _fetch_gc_otp when no OTP field is present."""
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setattr("gc_cli.session.CONTEXT_PATH", tmp_gc_dir / "sessions" / "ctx.json")

    mock_pw_cm, _, _, _ = _make_playwright_mocks(otp_field_found=False)

    with patch("playwright.sync_api.sync_playwright", return_value=mock_pw_cm), \
         patch("gc_cli.session._fetch_gc_otp") as mock_otp:
        from gc_cli.session import _playwright_login
        _playwright_login("user@gc.com", "pass", visible=False)

    mock_otp.assert_not_called()
