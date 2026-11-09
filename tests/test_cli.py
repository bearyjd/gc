"""Tests for gc_cli.cli helpers — _parse_gc_team_map."""

from gc_cli.cli import _parse_gc_team_map


# ---------------------------------------------------------------------------
# _parse_gc_team_map
# ---------------------------------------------------------------------------

def test_parse_gc_team_map_empty_string():
    assert _parse_gc_team_map("") == {}


def test_parse_gc_team_map_valid_pairs():
    raw = "02cdb406-69c7-4e09-96e9-c8e6eda24c6c:Ford,ba97753d-1fe3-42a3-b40c-167ea63086d4:Jack"
    result = _parse_gc_team_map(raw)
    assert result["02cdb406-69c7-4e09-96e9-c8e6eda24c6c"] == "Ford"
    assert result["ba97753d-1fe3-42a3-b40c-167ea63086d4"] == "Jack"


def test_parse_gc_team_map_full_user_env():
    """Parse the actual GC_TEAM_MAP value from the user's production env."""
    raw = (
        "02cdb406-69c7-4e09-96e9-c8e6eda24c6c:Ford,"
        "10212a23-72e6-4bab-a9e3-7d3fb7399102:Ford,"
        "3566ee7d-24fb-4e67-aef5-42ffd41bb481:Penn,"
        "54ec80d7-a06e-4af3-883d-5d5726ecc230:PennJack,"
        "ba97753d-1fe3-42a3-b40c-167ea63086d4:Jack,"
        "e92faf31-63f0-4b2c-bb4f-9a99e6e1963e:Jack,"
        "f94c804e-f3d7-4228-aa55-dad6b4c75d21:Jack"
    )
    result = _parse_gc_team_map(raw)
    assert len(result) == 7
    assert result["54ec80d7-a06e-4af3-883d-5d5726ecc230"] == "PennJack"
    assert result["3566ee7d-24fb-4e67-aef5-42ffd41bb481"] == "Penn"
    assert result["ba97753d-1fe3-42a3-b40c-167ea63086d4"] == "Jack"


def test_parse_gc_team_map_malformed_pairs_skipped_silently():
    """Pairs without a colon are silently ignored."""
    raw = "validid:Child,nocolonhere,anothervalid:Kid"
    result = _parse_gc_team_map(raw)
    assert "nocolonhere" not in result
    assert result["validid"] == "Child"
    assert result["anothervalid"] == "Kid"


def test_parse_gc_team_map_empty_team_id_skipped():
    """Pairs with empty team_id (leading colon) are skipped."""
    raw = ":Child,validid:Kid"
    result = _parse_gc_team_map(raw)
    assert "" not in result
    assert result["validid"] == "Kid"


def test_parse_gc_team_map_whitespace_trimmed():
    """Leading/trailing whitespace around ids and values is stripped."""
    raw = " teamid : Child , teamid2 : Kid "
    result = _parse_gc_team_map(raw)
    assert result["teamid"] == "Child"
    assert result["teamid2"] == "Kid"


# ---------------------------------------------------------------------------
# Command layer (roadmap item 2)
#
# Everything below is offline: get_session / GCClient / sync_team and the
# session.py login helpers are patched, so no network, Playwright, gog, or
# credentials are touched. The shapes returned by the fake client are the
# *normalized* dicts that client.py already produces (see _normalize_team /
# _normalize_event / _normalize_clip) -- no new API shapes are assumed.
# ---------------------------------------------------------------------------

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from gc_cli import cli
from gc_cli.cli import (
    _resolve_team_id,
    cmd_schedule,
    cmd_summary,
    cmd_sync,
    cmd_teams,
    cmd_token_refresh,
)
from gc_cli.sync import SyncResult

TEAM = {"id": "team-1", "name": "Team One", "sport": "baseball", "season": "Spring 2026"}
EVENT = {
    "id": "event-aaa", "date": "2026-04-15", "time": "14:30", "timezone": "America/New_York",
    "type": "game", "opponent": "Team X", "location": "Field 3", "location_name": "Field 3",
    "location_address": "", "home_away": "home", "game_type": "", "notes": "", "sport": "",
}
CLIP = {"id": "c1", "title": "Big hit", "url": "http://x/c1", "event_id": "event-aaa"}


def ns(**kw) -> argparse.Namespace:
    base = dict(team=None, json=False, visible=False, calendar=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def fake_client():
    client = MagicMock(name="GCClient")
    client.get_my_teams.return_value = [TEAM]
    client.get_schedule.return_value = [EVENT]
    client.get_team_summary.return_value = {
        "team_id": "team-1", "schedule": [EVENT], "clips": [CLIP],
    }
    return client


@pytest.fixture
def patched_cli(fake_client, tmp_path, monkeypatch):
    """Patch auth, client, and ~/.gc lookups used by cli.py."""
    monkeypatch.delenv("GC_CALENDAR_ID", raising=False)
    monkeypatch.delenv("GC_TEAM_MAP", raising=False)
    with patch("gc_cli.cli.get_session") as gs, \
         patch("gc_cli.cli.GCClient", return_value=fake_client) as gcc, \
         patch("gc_cli.cli._load_teams", return_value=[]) as lt, \
         patch("gc_cli.cli._load_env", return_value={}), \
         patch("gc_cli.cli.GC_DIR", tmp_path), \
         patch("gc_cli.cli.TEAMS_PATH", tmp_path / "teams.json"):
        yield MagicMock(get_session=gs, GCClient=gcc, load_teams=lt, client=fake_client)


# --- _resolve_team_id ------------------------------------------------------

def test_resolve_team_id_explicit_team_wins(patched_cli):
    patched_cli.load_teams.return_value = [{"id": "from-file"}]
    assert _resolve_team_id(ns(team="explicit"), patched_cli.client) == "explicit"
    patched_cli.client.get_my_teams.assert_not_called()


def test_resolve_team_id_falls_back_to_teams_json(patched_cli):
    patched_cli.load_teams.return_value = [{"id": "from-file"}, {"id": "second"}]
    assert _resolve_team_id(ns(), patched_cli.client) == "from-file"
    patched_cli.client.get_my_teams.assert_not_called()


def test_resolve_team_id_falls_back_to_api(patched_cli):
    assert _resolve_team_id(ns(), patched_cli.client) == "team-1"
    patched_cli.client.get_my_teams.assert_called_once()


def test_resolve_team_id_skips_teams_json_entry_without_id(patched_cli):
    patched_cli.load_teams.return_value = [{"name": "no id"}]
    assert _resolve_team_id(ns(), patched_cli.client) == "team-1"


def test_resolve_team_id_raises_when_everything_empty(patched_cli):
    patched_cli.client.get_my_teams.return_value = []
    with pytest.raises(RuntimeError, match="No teams found"):
        _resolve_team_id(ns(), patched_cli.client)


# --- teams / schedule / summary -------------------------------------------

def test_cmd_teams_json_prints_only_json(patched_cli, capsys):
    cmd_teams(ns(json=True))
    out = capsys.readouterr().out
    assert json.loads(out) == [TEAM]
    assert "Tip:" not in out
    patched_cli.get_session.assert_called_once_with(verbose=False, visible=False)


def test_cmd_teams_table_and_save_tip(patched_cli, capsys):
    cmd_teams(ns())
    out = capsys.readouterr().out
    assert "Team One" in out and "team-1" in out
    assert "gc teams --json >" in out  # teams.json absent -> tip shown


def test_cmd_teams_no_tip_when_teams_file_exists(patched_cli, tmp_path, capsys):
    (tmp_path / "teams.json").write_text("[]")
    cmd_teams(ns())
    assert "Tip:" not in capsys.readouterr().out


def test_cmd_teams_empty(patched_cli, capsys):
    patched_cli.client.get_my_teams.return_value = []
    cmd_teams(ns())
    assert "No teams found." in capsys.readouterr().out


def test_cmd_schedule_json_uses_explicit_team(patched_cli, capsys):
    cmd_schedule(ns(team="t9", json=True))
    patched_cli.client.get_schedule.assert_called_once_with("t9")
    assert json.loads(capsys.readouterr().out) == [EVENT]


def test_cmd_schedule_table(patched_cli, capsys):
    cmd_schedule(ns())
    out = capsys.readouterr().out
    assert "2026-04-15" in out and "14:30" in out and "Team X" in out


def test_cmd_schedule_empty(patched_cli, capsys):
    patched_cli.client.get_schedule.return_value = []
    cmd_schedule(ns())
    assert "No upcoming events found." in capsys.readouterr().out


def test_cmd_summary_json_has_timestamp_and_clips(patched_cli, capsys):
    cmd_summary(ns(team="team-1", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["team_id"] == "team-1"
    assert data["clips"] == [CLIP] and data["schedule"] == [EVENT]
    assert "timestamp" in data


def test_cmd_summary_text(patched_cli, capsys):
    cmd_summary(ns(team="team-1"))
    out = capsys.readouterr().out
    assert "Schedule (1 events)" in out and "Clips (1 clips)" in out and "Big hit" in out


# --- cmd_sync --------------------------------------------------------------

def test_cmd_sync_exits_1_without_calendar_id(patched_cli, capsys):
    with patch("gc_cli.cli.sync_team") as st, pytest.raises(SystemExit) as exc:
        cmd_sync(ns(team="team-1"))
    assert exc.value.code == 1
    assert "GC_CALENDAR_ID not set" in capsys.readouterr().err
    st.assert_not_called()
    patched_cli.get_session.assert_not_called()  # fails before any auth


def test_cmd_sync_calendar_flag_beats_env(patched_cli, monkeypatch):
    monkeypatch.setenv("GC_CALENDAR_ID", "env-cal")
    with patch("gc_cli.cli.sync_team", return_value=SyncResult()) as st:
        cmd_sync(ns(team="team-1", calendar="flag-cal", dry_run=True))
    assert st.call_args.args[1] == "flag-cal"


def test_cmd_sync_uses_env_calendar_and_passes_dry_run(patched_cli, monkeypatch, tmp_path):
    monkeypatch.setenv("GC_CALENDAR_ID", "env-cal")
    with patch("gc_cli.cli.sync_team", return_value=SyncResult(created=["x"])) as st:
        cmd_sync(ns(team="team-1", dry_run=True))  # returns normally -> exit 0
    args, kwargs = st.call_args
    assert args == ([EVENT], "env-cal", tmp_path)
    assert kwargs["dry_run"] is True and kwargs["team_id"] == "team-1"


def test_cmd_sync_exits_1_when_result_has_errors(patched_cli, monkeypatch, capsys):
    monkeypatch.setenv("GC_CALENDAR_ID", "cal")
    with patch("gc_cli.cli.sync_team", return_value=SyncResult(errors=["create X: boom"])), \
         pytest.raises(SystemExit) as exc:
        cmd_sync(ns(team="team-1"))
    assert exc.value.code == 1
    assert "create X: boom" in capsys.readouterr().err


def test_cmd_sync_team_name_and_child_from_teams_json(patched_cli, monkeypatch):
    monkeypatch.setenv("GC_CALENDAR_ID", "cal")
    patched_cli.load_teams.return_value = [
        {"id": "other", "name": "Other", "child": "Nope"},
        {"id": "team-1", "name": "Team One", "child": "Kid"},
    ]
    with patch("gc_cli.cli.sync_team", return_value=SyncResult()) as st:
        cmd_sync(ns(team="team-1"))
    assert st.call_args.kwargs["team_name"] == "Team One"
    assert st.call_args.kwargs["child"] == "Kid"


def test_cmd_sync_child_falls_back_to_gc_team_map(patched_cli, monkeypatch):
    monkeypatch.setenv("GC_CALENDAR_ID", "cal")
    monkeypatch.setenv("GC_TEAM_MAP", "team-1:MapKid")
    with patch("gc_cli.cli.sync_team", return_value=SyncResult()) as st:
        cmd_sync(ns(team="team-1"))
    assert st.call_args.kwargs["child"] == "MapKid"
    assert st.call_args.kwargs["team_name"] is None


# --- cmd_token_refresh -----------------------------------------------------

@pytest.fixture
def refresh_mocks():
    """Patch every collaborator of cmd_token_refresh."""
    with patch("gc_cli.cli._try_context_login") as ctx, \
         patch("gc_cli.cli._update_env_token") as upd, \
         patch("gc_cli.cli._get_credentials", return_value=("e@x", "pw")) as creds, \
         patch("gc_cli.cli._playwright_login") as login, \
         patch("gc_cli.session._token_from_env", return_value=(None, None)) as tok, \
         patch("gc_cli.session._make_session") as mk:
        yield MagicMock(ctx=ctx, upd=upd, creds=creds, login=login, tok=tok, mk=mk)


def _sess(token="tok", device=None):
    s = MagicMock()
    s.headers = {"gc-token": token}
    if device:
        s.headers["gc-device-id"] = device
    return s


def test_token_refresh_context_login_success_returns_cleanly(refresh_mocks):
    refresh_mocks.ctx.return_value = _sess("fresh", "dev1")
    cmd_token_refresh(ns())  # no SystemExit
    refresh_mocks.upd.assert_called_once_with("fresh", "dev1")
    refresh_mocks.login.assert_not_called()
    refresh_mocks.mk.assert_not_called()


def test_token_refresh_ping_200_keeps_session(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.tok.return_value = ("envtok", None)
    refresh_mocks.mk.return_value.get.return_value = MagicMock(status_code=200)
    cmd_token_refresh(ns())
    refresh_mocks.login.assert_not_called()
    refresh_mocks.upd.assert_not_called()


def test_token_refresh_ping_5xx_does_not_burn_an_otp(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.tok.return_value = ("envtok", None)
    refresh_mocks.mk.return_value.get.return_value = MagicMock(status_code=503)
    cmd_token_refresh(ns())
    refresh_mocks.login.assert_not_called()


def test_token_refresh_ping_network_error_falls_through_to_login(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.tok.return_value = ("envtok", None)
    refresh_mocks.mk.return_value.get.side_effect = requests.ConnectionError("down")
    refresh_mocks.login.return_value = _sess("new")
    cmd_token_refresh(ns())
    refresh_mocks.login.assert_called_once()
    refresh_mocks.upd.assert_called_once_with("new", None)


@pytest.mark.parametrize("status", [401, 403])
def test_token_refresh_rejected_token_falls_through_to_full_login(refresh_mocks, status):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.tok.return_value = ("envtok", None)
    refresh_mocks.mk.return_value.get.return_value = MagicMock(status_code=status)
    refresh_mocks.login.return_value = _sess("new", "dev2")
    cmd_token_refresh(ns())
    refresh_mocks.login.assert_called_once_with("e@x", "pw", visible=False)
    refresh_mocks.upd.assert_called_once_with("new", "dev2")


def test_token_refresh_401_then_headless_login_failure_exits_1(refresh_mocks, capsys):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.tok.return_value = ("envtok", None)
    refresh_mocks.mk.return_value.get.return_value = MagicMock(status_code=401)
    refresh_mocks.login.side_effect = RuntimeError("OTP unavailable")
    with pytest.raises(SystemExit) as exc:
        cmd_token_refresh(ns())
    assert exc.value.code == 1
    assert "OTP unavailable" in capsys.readouterr().err
    refresh_mocks.upd.assert_not_called()


def test_token_refresh_no_token_no_context_goes_to_full_login(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.login.return_value = _sess("new")
    cmd_token_refresh(ns())
    refresh_mocks.mk.assert_not_called()  # no token -> no ping
    refresh_mocks.login.assert_called_once_with("e@x", "pw", visible=False)


def test_token_refresh_visible_flag_reaches_full_login(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.login.return_value = _sess("new")
    cmd_token_refresh(ns(visible=True))
    refresh_mocks.login.assert_called_once_with("e@x", "pw", visible=True)


def test_token_refresh_full_login_without_token_does_not_persist(refresh_mocks):
    refresh_mocks.ctx.return_value = None
    refresh_mocks.login.return_value = _sess("")
    cmd_token_refresh(ns())
    refresh_mocks.upd.assert_not_called()


# --- main() exit-code wiring ----------------------------------------------

def test_main_without_command_prints_help_and_exits_1(monkeypatch):
    monkeypatch.setattr("sys.argv", ["gc"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_main_runtime_error_exits_1(patched_cli, monkeypatch, capsys):
    patched_cli.client.get_my_teams.return_value = []
    monkeypatch.setattr("sys.argv", ["gc", "schedule"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "No teams found" in capsys.readouterr().err


def test_main_request_exception_exits_1(patched_cli, monkeypatch, capsys):
    patched_cli.client.get_my_teams.side_effect = requests.ConnectionError("down")
    monkeypatch.setattr("sys.argv", ["gc", "teams"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "Network error" in capsys.readouterr().err


def test_main_keyboard_interrupt_exits_130(patched_cli, monkeypatch):
    patched_cli.client.get_my_teams.side_effect = KeyboardInterrupt
    monkeypatch.setattr("sys.argv", ["gc", "teams"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 130
