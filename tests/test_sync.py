"""Tests for gc_cli.sync — fingerprint, state, diff logic, and new helpers."""
import json
from unittest.mock import patch

import pytest

from gc_cli.sync import (
    _event_description,
    _event_title,
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
    "location_name": "Home Field",
    "location_address": "123 Main St",
    "home_away": "home",
    "game_type": "",
    "notes": "",
    "sport": "baseball",
}

PRACTICE_EVENT = {
    "id": "evt_002",
    "date": "2026-05-12",
    "time": "16:00",
    "type": "Practice",
    "opponent": "",
    "location": "Practice Field",
    "location_name": "Practice Field",
    "location_address": "",
    "home_away": "",
    "game_type": "",
    "notes": "",
    "sport": "baseball",
}

AWAY_GAME_EVENT = {
    "id": "evt_003",
    "date": "2026-05-14",
    "time": "11:00",
    "type": "Game",
    "opponent": "Rockets",
    "location": "Away Stadium",
    "location_name": "Away Stadium",
    "location_address": "456 Oak Ave",
    "home_away": "away",
    "game_type": "",
    "notes": "",
    "sport": "soccer",
}

NO_OPPONENT_GAME = {
    "id": "evt_004",
    "date": "2026-05-16",
    "time": "09:00",
    "type": "Game",
    "opponent": "",
    "location": "",
    "location_name": "",
    "location_address": "",
    "home_away": "",
    "game_type": "",
    "notes": "",
    "sport": "",
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


def test_fingerprint_changes_when_location_changes():
    modified = {**GAME_EVENT, "location": "Away Field"}
    assert event_fingerprint(GAME_EVENT) != event_fingerprint(modified)


def test_fingerprint_changes_when_notes_change():
    modified = {**GAME_EVENT, "notes": "Bring sunscreen"}
    assert event_fingerprint(GAME_EVENT) != event_fingerprint(modified)


def test_fingerprint_changes_when_home_away_changes():
    modified = {**GAME_EVENT, "home_away": "away"}
    assert event_fingerprint(GAME_EVENT) != event_fingerprint(modified)


# ---------------------------------------------------------------------------
# _event_title
# ---------------------------------------------------------------------------

def test_event_title_game_home_with_child():
    title = _event_title(GAME_EVENT, team_name="Tigers", child="Alex")
    assert title == "⚾ Alex — Game vs Eagles (Home)"


def test_event_title_game_away_with_child():
    title = _event_title(AWAY_GAME_EVENT, team_name="Tigers", child="Maya")
    assert title == "⚽ Maya — Game @ Rockets (Away)"


def test_event_title_practice_with_child():
    title = _event_title(PRACTICE_EVENT, team_name="Tigers", child="Alex")
    assert title == "⚾ Alex — Practice"


def test_event_title_no_opponent_game():
    title = _event_title(NO_OPPONENT_GAME, team_name="Tigers", child="Alex")
    assert "Game" in title
    assert "Alex" in title


def test_event_title_no_child_falls_back_to_team_name():
    title = _event_title(GAME_EVENT, team_name="Tigers", child=None)
    assert "Tigers" in title
    assert "Eagles" in title


def test_event_title_no_child_no_team():
    title = _event_title(GAME_EVENT, team_name=None, child=None)
    # Still produces a meaningful title with the type label
    assert "Game" in title or "Eagles" in title


def test_event_title_practice_no_sport_emoji():
    event = {**PRACTICE_EVENT, "sport": ""}
    title = _event_title(event, team_name="Tigers", child="Alex")
    # No emoji when sport is unknown
    assert title.startswith("Alex") or title.startswith("Tigers")


def test_event_title_unknown_sport_no_emoji():
    event = {**GAME_EVENT, "sport": "curling"}
    title = _event_title(event, team_name="Tigers", child="Alex")
    assert not title.startswith("⚾")
    assert "Alex" in title


# ---------------------------------------------------------------------------
# _event_description
# ---------------------------------------------------------------------------

def test_event_description_includes_child():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "Child: Alex" in desc


def test_event_description_includes_team_and_sport():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "Team: Tigers (baseball)" in desc


def test_event_description_includes_opponent_with_home_away():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "Opponent: Eagles (Home)" in desc


def test_event_description_includes_location():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "Location: Home Field" in desc


def test_event_description_includes_address():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "Address: 123 Main St" in desc


def test_event_description_omits_empty_fields():
    event = {**PRACTICE_EVENT, "notes": "", "location_address": ""}
    desc = _event_description(event, team_name="Tigers", child="Alex")
    assert "Address:" not in desc
    assert "Notes:" not in desc
    assert "Opponent:" not in desc


def test_event_description_includes_gc_event_id_footer():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child="Alex")
    assert "GameChanger event id: evt_001" in desc


def test_event_description_no_child_omits_child_line():
    desc = _event_description(GAME_EVENT, team_name="Tigers", child=None)
    assert "Child:" not in desc


def test_event_description_notes_included_when_present():
    event = {**GAME_EVENT, "notes": "Bring cleats"}
    desc = _event_description(event, team_name="Tigers", child="Alex")
    assert "Notes: Bring cleats" in desc


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
            "summary": "⚾ Alex — Game vs Eagles (Home)",
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
# _load_teams deduplication (client.py)
# ---------------------------------------------------------------------------

def test_load_teams_deduplicates_by_id(tmp_gc_dir, monkeypatch):
    from gc_cli.client import _load_teams
    teams_data = [
        {"id": "team_1", "name": "Tigers", "sport": "baseball"},
        {"id": "team_1", "name": "Tigers Duplicate", "sport": "baseball"},
        {"id": "team_2", "name": "Eagles", "sport": "soccer"},
    ]
    teams_file = tmp_gc_dir / "teams.json"
    teams_file.write_text(json.dumps(teams_data))
    monkeypatch.setattr("gc_cli.client.TEAMS_PATH", teams_file)

    result = _load_teams()
    assert len(result) == 2
    ids = [t["id"] for t in result]
    assert ids == ["team_1", "team_2"]
    # First occurrence kept
    assert result[0]["name"] == "Tigers"


# ---------------------------------------------------------------------------
# sync_team — new events
# ---------------------------------------------------------------------------

def test_sync_creates_new_event(tmp_gc_dir):
    gog_response = json.dumps({"id": "gcal_new_001"})

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, gog_response)):
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    assert len(result.created) == 1
    assert "Eagles" in result.created[0]  # title contains opponent
    assert result.errors == []

    state = load_state(tmp_gc_dir)
    assert "evt_001" in state
    assert state["evt_001"]["gcal_event_id"] == "gcal_new_001"


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


def test_sync_create_passes_private_prop_gc_event_id(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_3"}')) as mock_gog:
        sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir, team_id="team_abc")

    args = mock_gog.call_args[0][0]
    assert "--private-prop" in args
    props = [args[i + 1] for i, a in enumerate(args) if a == "--private-prop"]
    assert any("gc_event_id=evt_001" in p for p in props)
    assert any("gc_team_id=team_abc" in p for p in props)


def test_sync_create_passes_reminders_for_game(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_4"}')) as mock_gog:
        sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    args = mock_gog.call_args[0][0]
    reminder_values = [args[i + 1] for i, a in enumerate(args) if a == "--reminder"]
    assert "popup:1d" in reminder_values
    assert "popup:1h" in reminder_values


def test_sync_create_passes_only_1h_reminder_for_practice(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_5"}')) as mock_gog:
        sync_team([PRACTICE_EVENT], "cal_primary", tmp_gc_dir)

    args = mock_gog.call_args[0][0]
    reminder_values = [args[i + 1] for i, a in enumerate(args) if a == "--reminder"]
    assert "popup:1h" in reminder_values
    assert "popup:1d" not in reminder_values


def test_sync_create_passes_description(tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, '{"id":"gcal_6"}')) as mock_gog:
        sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir, team_name="Tigers", child="Alex")

    args = mock_gog.call_args[0][0]
    assert "--description" in args
    desc = args[args.index("--description") + 1]
    assert "Eagles" in desc or "GameChanger" in desc


# ---------------------------------------------------------------------------
# sync_team — changed events
# ---------------------------------------------------------------------------

def test_sync_updates_changed_event(tmp_gc_dir):
    state = {
        "evt_001": {
            "gcal_event_id": "gcal_existing",
            "calendar_id": "cal_primary",
            "fingerprint": "old_fingerprint_x",  # deliberately wrong
            "summary": "⚾ Alex — Game vs Eagles (Home)",
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, "")) as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    assert len(result.updated) == 1
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
            "summary": "⚾ Alex — Game vs Eagles (Home)",
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
# sync_team — soft-cancel
# ---------------------------------------------------------------------------

def test_sync_cancel_marks_cancelled_true_in_state(tmp_gc_dir):
    """Soft-cancel: removed event gets cancelled=True, state entry is kept."""
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

    # State entry retained with cancelled=True (soft-cancel)
    final_state = load_state(tmp_gc_dir)
    assert "evt_removed" in final_state
    assert final_state["evt_removed"]["cancelled"] is True


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


def test_sync_soft_cancelled_event_resurfaces_as_update(tmp_gc_dir):
    """When a soft-cancelled event reappears, it should be un-cancelled via UPDATE."""
    state = {
        "evt_001": {
            "gcal_event_id": "gcal_existing",
            "calendar_id": "cal_primary",
            "fingerprint": "old_fp",
            "summary": "Game: Eagles",
            "cancelled": True,
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(True, "")) as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir)

    # Should be an update (uncancel), not a create
    assert len(result.updated) == 1
    assert result.created == []

    args = mock_gog.call_args[0][0]
    assert "update" in args
    assert "gcal_existing" in args

    # State should no longer have cancelled=True
    final_state = load_state(tmp_gc_dir)
    assert not final_state["evt_001"].get("cancelled", False)


def test_sync_skips_already_cancelled_in_removed_ids(tmp_gc_dir):
    """Already-soft-cancelled entries should not trigger a second gog cancel call."""
    state = {
        "evt_removed": {
            "gcal_event_id": "gcal_gone",
            "calendar_id": "cal_primary",
            "fingerprint": "fp",
            "summary": "Game: Ravens",
            "cancelled": True,
        }
    }
    save_state(state, tmp_gc_dir)

    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog") as mock_gog:
        result = sync_team([], "cal_primary", tmp_gc_dir)

    mock_gog.assert_not_called()
    assert result.cancelled == []


# ---------------------------------------------------------------------------
# sync_team — dry run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_call_gog(tmp_gc_dir):
    with patch("gc_cli.sync._run_gog") as mock_gog:
        result = sync_team([GAME_EVENT], "cal_primary", tmp_gc_dir, dry_run=True)

    mock_gog.assert_not_called()
    assert len(result.created) == 1
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
