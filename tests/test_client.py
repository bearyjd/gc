"""Tests for gc_cli.client — focused on auto-retry-on-401 self-healing."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from gc_cli.client import GCClient


def _resp(status: int, body) -> MagicMock:
    """Build a mock requests.Response."""
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    else:
        r.raise_for_status.return_value = None
    r.json.return_value = body
    return r


def _mock_session(get_responses, headers=None) -> MagicMock:
    """Build a mock requests.Session whose .get() returns each response in turn."""
    s = MagicMock(spec=requests.Session)
    s.get.side_effect = list(get_responses)
    s.headers = dict(headers or {})
    return s


def test_get_returns_data_on_200_without_refresh():
    """Happy path: 200 response → no refresh attempted."""
    session = _mock_session([_resp(200, [{"id": "t1"}])])
    client = GCClient(session, verbose=False)
    with patch("gc_cli.session._try_context_login") as mock_refresh:
        result = client._get("/me/teams")
    assert result == [{"id": "t1"}]
    mock_refresh.assert_not_called()


def test_get_retries_once_on_401_and_returns_fresh_data():
    """401 → refresh succeeds → retry on the new session returns fresh data."""
    original = _mock_session([_resp(401, None)])
    fresh = _mock_session(
        [_resp(200, [{"id": "t1"}])],
        headers={"gc-token": "fresh-user-token"},
    )
    client = GCClient(original, verbose=False)
    with patch("gc_cli.session._try_context_login", return_value=fresh) as mock_refresh, \
         patch("gc_cli.session._update_env_token") as mock_persist:
        result = client._get("/me/teams")
    assert result == [{"id": "t1"}]
    mock_refresh.assert_called_once()
    mock_persist.assert_called_once_with("fresh-user-token", None)
    original.get.assert_called_once()  # 401
    fresh.get.assert_called_once()     # successful retry
    assert client.session is fresh


def test_get_persists_device_id_when_present():
    """When refresh returns a session with gc-device-id, it persists too."""
    original = _mock_session([_resp(401, None)])
    fresh = _mock_session(
        [_resp(200, [])],
        headers={"gc-token": "tok", "gc-device-id": "dev-xyz"},
    )
    client = GCClient(original, verbose=False)
    with patch("gc_cli.session._try_context_login", return_value=fresh), \
         patch("gc_cli.session._update_env_token") as mock_persist:
        client._get("/me/teams")
    mock_persist.assert_called_once_with("tok", "dev-xyz")


def test_get_returns_empty_list_when_refresh_fails():
    """401 → refresh returns None (e.g. OTP unavailable) → empty list, no crash."""
    session = _mock_session([_resp(401, None)])
    client = GCClient(session, verbose=False)
    with patch("gc_cli.session._try_context_login", return_value=None):
        result = client._get("/me/teams")
    assert result == []


def test_get_does_not_loop_when_retry_also_401():
    """If the fresh session ALSO 401s, give up rather than refresh forever."""
    original = _mock_session([_resp(401, None)])
    fresh = _mock_session(
        [_resp(401, None)],
        headers={"gc-token": "still-bad"},
    )
    client = GCClient(original, verbose=False)
    with patch("gc_cli.session._try_context_login", return_value=fresh) as mock_refresh, \
         patch("gc_cli.session._update_env_token"):
        result = client._get("/me/teams")
    assert result == []
    # Refresh attempted exactly once for this _get call
    mock_refresh.assert_called_once()
    original.get.assert_called_once()
    fresh.get.assert_called_once()


def test_refresh_guard_prevents_second_attempt_within_same_client():
    """First _get triggers a refresh; second _get on same client (still 401)
    must NOT trigger a second refresh — that would launch Playwright twice
    per command for nothing."""
    original = _mock_session([_resp(401, None)])
    fresh = _mock_session(
        [_resp(200, []), _resp(401, None)],  # retry succeeds, next call 401s
        headers={"gc-token": "tok"},
    )
    client = GCClient(original, verbose=False)
    with patch("gc_cli.session._try_context_login", return_value=fresh) as mock_refresh, \
         patch("gc_cli.session._update_env_token"):
        client._get("/me/teams")
        client._get("/me/user")  # 401 here would otherwise re-trigger refresh
    mock_refresh.assert_called_once()
