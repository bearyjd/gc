"""Tests for gc_cli.client — GCClient methods and response normalizers.

Uses synthetic fixtures in tests/fixtures/ to pin the fallback-chain behavior
documented in CLAUDE.md as "shape unconfirmed" for /me/teams and /clips, plus
the "confirmed" envelope shape for /teams/{id}/schedule.
"""
import json
from pathlib import Path
from unittest.mock import patch

from gc_cli.client import (
    GCClient,
    _normalize_clip,
    _normalize_event,
    _normalize_team,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# _normalize_team
# ---------------------------------------------------------------------------

def test_normalize_team_standard_keys():
    raw = {"id": "t1", "name": "Team A", "sport": "baseball",
           "season_name": "Spring", "season_year": "2026"}
    result = _normalize_team(raw)
    assert result == {"id": "t1", "name": "Team A", "sport": "baseball",
                       "season": "Spring 2026"}


def test_normalize_team_alternate_keys():
    raw = {"teamId": "t2", "teamName": "Team B", "sport": "softball",
           "season": "Fall 2025"}
    result = _normalize_team(raw)
    assert result == {"id": "t2", "name": "Team B", "sport": "softball",
                       "season": "Fall 2025"}


def test_normalize_team_missing_fields_defaults():
    result = _normalize_team({})
    assert result == {"id": "", "name": "Unknown", "sport": "", "season": ""}


# ---------------------------------------------------------------------------
# _normalize_event
# ---------------------------------------------------------------------------

def test_normalize_event_full_game_envelope():
    raw = {
        "event": {
            "id": "e1",
            "event_type": "game",
            "start": {"datetime": "2026-04-15T18:30:00Z"},
            "timezone": "America/New_York",
            "location": {"name": "Field 3", "address": ["123 Main St", "Springfield"]},
            "title": "vs Team X",
            "notes": "Bring extra gloves",
        },
        "pregame_data": {"opponent_name": "Team X", "home_away": "home"},
    }
    result = _normalize_event(raw)
    assert result["id"] == "e1"
    assert result["type"] == "game"
    assert result["opponent"] == "Team X"
    assert result["home_away"] == "home"
    assert result["location_name"] == "Field 3"
    assert result["location_address"] == "123 Main St, Springfield"
    assert result["notes"] == "Bring extra gloves"
    assert result["date"] == "2026-04-15"


def test_normalize_event_practice_no_pregame_data():
    raw = {
        "event": {
            "id": "e2",
            "event_type": "practice",
            "start": {"datetime": "2026-04-17T22:00:00Z"},
            "timezone": "America/New_York",
        }
    }
    result = _normalize_event(raw)
    assert result["id"] == "e2"
    assert result["type"] == "practice"
    assert result["opponent"] == ""
    assert result["home_away"] == ""


def test_normalize_event_missing_fields_no_crash():
    result = _normalize_event({})
    assert result["id"] == ""
    assert result["date"] == ""
    assert result["opponent"] == ""


# ---------------------------------------------------------------------------
# _normalize_clip
# ---------------------------------------------------------------------------

def test_normalize_clip_standard_keys():
    raw = {"id": "c1", "title": "Great catch", "url": "https://x/c1.mp4", "eventId": "e1"}
    result = _normalize_clip(raw)
    assert result == {"id": "c1", "title": "Great catch", "url": "https://x/c1.mp4",
                       "event_id": "e1"}


def test_normalize_clip_alternate_keys():
    raw = {"clipId": "c2", "name": "Home run", "clipUrl": "https://x/c2.mp4",
           "event_id": "e2"}
    result = _normalize_clip(raw)
    assert result == {"id": "c2", "title": "Home run", "url": "https://x/c2.mp4",
                       "event_id": "e2"}


def test_normalize_clip_missing_fields_defaults():
    result = _normalize_clip({})
    assert result == {"id": "", "title": "Untitled", "url": "", "event_id": ""}


# ---------------------------------------------------------------------------
# GCClient.get_my_teams
# ---------------------------------------------------------------------------

def test_get_my_teams_teams_key_envelope():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=_load_fixture("teams_response_teams_key.json")):
        teams = client.get_my_teams()
    assert len(teams) == 2
    assert teams[0] == {"id": "team-aaa-111", "name": "Team A", "sport": "baseball",
                         "season": "Spring 2026"}
    assert teams[1] == {"id": "team-bbb-222", "name": "Team B", "sport": "softball",
                         "season": "Fall 2025"}


def test_get_my_teams_data_key_envelope():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=_load_fixture("teams_response_data_key.json")):
        teams = client.get_my_teams()
    assert len(teams) == 1
    assert teams[0]["id"] == "team-ccc-333"
    assert teams[0]["name"] == "Team C"


def test_get_my_teams_bare_list():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=_load_fixture("teams_response_bare_list.json")):
        teams = client.get_my_teams()
    assert len(teams) == 1
    assert teams[0]["id"] == "team-ddd-444"
    assert teams[0]["name"] == "Team D"


def test_get_my_teams_empty_envelope_returns_empty_list():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value={}):
        teams = client.get_my_teams()
    assert teams == []


def test_get_my_teams_skips_non_dict_entries():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value={"teams": [{"id": "t1", "name": "T1"}, "garbage", None]}):
        teams = client.get_my_teams()
    assert len(teams) == 1
    assert teams[0]["id"] == "t1"


# ---------------------------------------------------------------------------
# GCClient.get_schedule
# ---------------------------------------------------------------------------

def test_get_schedule_confirmed_envelope_shape():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=_load_fixture("schedule_response.json")):
        events = client.get_schedule("team-aaa-111")
    assert len(events) == 2
    assert events[0]["id"] == "event-aaa"
    assert events[0]["type"] == "game"
    assert events[0]["opponent"] == "Team X"
    assert events[1]["id"] == "event-bbb"
    assert events[1]["type"] == "practice"


def test_get_schedule_calls_correct_path():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value={"schedule": []}) as mock_get:
        client.get_schedule("team-xyz")
    mock_get.assert_called_once_with("/teams/team-xyz/schedule")


# ---------------------------------------------------------------------------
# GCClient.get_clips
# ---------------------------------------------------------------------------

def test_get_clips_clips_key_envelope():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=_load_fixture("clips_response.json")):
        clips = client.get_clips("team-aaa-111")
    assert len(clips) == 2
    assert clips[0] == {"id": "clip-111", "title": "Great catch",
                         "url": "https://example.com/clip-111.mp4", "event_id": "event-aaa"}
    assert clips[1] == {"id": "clip-222", "title": "Home run",
                         "url": "https://example.com/clip-222.mp4", "event_id": "event-bbb"}


def test_get_clips_data_key_envelope():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value={"data": [{"id": "c9", "title": "T9"}]}):
        clips = client.get_clips("team-aaa-111")
    assert len(clips) == 1
    assert clips[0]["id"] == "c9"


def test_get_clips_bare_list():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value=[{"id": "c10", "title": "T10"}]):
        clips = client.get_clips("team-aaa-111")
    assert len(clips) == 1
    assert clips[0]["id"] == "c10"


def test_get_clips_calls_correct_path_and_params():
    client = GCClient(session=None, verbose=False)
    with patch.object(client, "_get", return_value={"clips": []}) as mock_get:
        client.get_clips("team-xyz")
    mock_get.assert_called_once_with("/clips", params={"kind": "event", "teamId": "team-xyz"})
