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
