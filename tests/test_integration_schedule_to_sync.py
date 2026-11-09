"""Offline pipeline test: raw schedule JSON -> GCClient.get_schedule -> sync_team.

Roadmap item 4. Zero network, zero gog, zero credentials:
  * GCClient._get is patched to return the raw fixture,
  * sync_team runs with dry_run=True (so no gog lookup / subprocess), or with
    gc_cli.sync._run_gog patched for the stateful cases.

Only the schedule envelope shape ({"event": {...}, "pregame_data": {...}},
documented as confirmed in _normalize_event) is used. /me/teams and /clips
shapes are unconfirmed and deliberately NOT exercised here.
"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from gc_cli.client import GCClient
from gc_cli.sync import SyncResult, load_state, sync_team

FIXTURES = Path(__file__).parent / "fixtures"
CAL = "cal@group.calendar.google.com"
TEAM = "team-1"


@pytest.fixture
def raw_schedule() -> list[dict]:
    """The fixture's raw schedule list (deep copy so tests may mutate it)."""
    return copy.deepcopy(json.loads((FIXTURES / "schedule_response.json").read_text())["schedule"])


def fetch_events(raw, envelope=None):
    """Run raw JSON through the real GCClient.get_schedule with _get mocked."""
    client = GCClient(requests.Session(), verbose=False)
    payload = {"schedule": raw} if envelope is None else envelope
    with patch.object(GCClient, "_get", return_value=payload) as g:
        events = client.get_schedule(TEAM)
    g.assert_called_once_with(f"/teams/{TEAM}/schedule")
    return events


def test_fixture_flows_to_dry_run_sync(raw_schedule, tmp_gc_dir):
    events = fetch_events(raw_schedule)
    assert [e["id"] for e in events] == ["event-aaa", "event-bbb"]
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM,
                       team_name="Team One", child="Kid")
    assert isinstance(result, SyncResult)
    assert len(result.created) == 2
    assert result.updated == [] and result.cancelled == [] and result.errors == []
    # dry run must not write state
    assert load_state(tmp_gc_dir, team_id=TEAM) == {}


def test_normalized_fields_reach_the_sync_summary(raw_schedule, tmp_gc_dir):
    events = fetch_events(raw_schedule)
    game = events[0]
    # 18:30Z in America/New_York (EDT) -> 14:30 local
    assert (game["date"], game["time"]) == ("2026-04-15", "14:30")
    assert game["opponent"] == "Team X" and game["home_away"] == "home"
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM,
                       team_name="Team One", child="Kid")
    assert any("Team X" in s for s in result.created)


def test_pregame_data_null_instead_of_absent(raw_schedule, tmp_gc_dir):
    raw_schedule[0]["pregame_data"] = None
    events = fetch_events(raw_schedule)
    assert events[0]["opponent"] == "vs Team X"  # falls back to event.title
    assert events[0]["home_away"] == ""
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM)
    assert len(result.created) == 2 and result.errors == []


def test_event_missing_start_datetime_does_not_crash_pipeline(raw_schedule, tmp_gc_dir):
    del raw_schedule[1]["event"]["start"]
    events = fetch_events(raw_schedule)
    assert events[1]["date"] == "" and events[1]["time"] == ""
    # Pins current behavior: the dateless event is still handed to sync and
    # does not raise. (Whether it *should* be created is a separate question.)
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM)
    assert result.errors == []
    assert len(result.created) == 2


def test_event_without_id_is_dropped_by_sync(raw_schedule, tmp_gc_dir):
    del raw_schedule[1]["event"]["id"]
    events = fetch_events(raw_schedule)
    assert events[1]["id"] == ""
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM)
    assert len(result.created) == 1


def test_bare_list_and_events_key_envelopes_are_equivalent(raw_schedule, tmp_gc_dir):
    a = fetch_events(raw_schedule, envelope=raw_schedule)
    b = fetch_events(raw_schedule, envelope={"events": raw_schedule})
    c = fetch_events(raw_schedule)
    assert a == b == c


def test_api_failure_yields_empty_schedule_and_noop_sync(tmp_gc_dir):
    client = GCClient(requests.Session(), verbose=False)
    with patch.object(GCClient, "_get", return_value=[]):
        events = client.get_schedule(TEAM)
    assert events == []
    result = sync_team(events, CAL, tmp_gc_dir, dry_run=True, team_id=TEAM)
    assert (result.created, result.updated, result.cancelled, result.errors) == ([], [], [], [])


# --- stateful (non-dry-run) pipeline with gog mocked ------------------------

def _fake_gog_factory():
    calls = []

    def fake(args, *a, **kw):
        calls.append(list(args))
        return True, "id: gcal-%d" % len(calls)

    return fake, calls


def test_full_cycle_create_then_noop_then_update_then_cancel(raw_schedule, tmp_gc_dir):
    fake, calls = _fake_gog_factory()
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", side_effect=fake):
        # 1. first run creates both events and persists state
        r1 = sync_team(fetch_events(raw_schedule), CAL, tmp_gc_dir, team_id=TEAM)
        assert len(r1.created) == 2 and r1.errors == []
        assert len(calls) == 2
        assert set(load_state(tmp_gc_dir, team_id=TEAM)) == {"event-aaa", "event-bbb"}

        # 2. identical re-run is a no-op (fingerprints match)
        r2 = sync_team(fetch_events(raw_schedule), CAL, tmp_gc_dir, team_id=TEAM)
        assert (r2.created, r2.updated, r2.cancelled) == ([], [], [])
        assert len(calls) == 2

        # 3. changing the start time of one event updates only that one
        raw_schedule[0]["event"]["start"]["datetime"] = "2026-04-15T19:30:00Z"
        r3 = sync_team(fetch_events(raw_schedule), CAL, tmp_gc_dir, team_id=TEAM)
        assert len(r3.updated) == 1 and r3.created == [] and r3.cancelled == []
        assert len(calls) == 3

        # 4. dropping an event from the feed soft-cancels it
        r4 = sync_team(fetch_events(raw_schedule[:1]), CAL, tmp_gc_dir, team_id=TEAM)
        assert len(r4.cancelled) == 1
        assert load_state(tmp_gc_dir, team_id=TEAM)["event-bbb"]["cancelled"] is True


def test_gog_failure_surfaces_in_result_errors(raw_schedule, tmp_gc_dir):
    with patch("gc_cli.sync.shutil.which", return_value="/usr/bin/gog"), \
         patch("gc_cli.sync._run_gog", return_value=(False, "boom")):
        result = sync_team(fetch_events(raw_schedule), CAL, tmp_gc_dir, team_id=TEAM)
    assert len(result.errors) == 2 and result.created == []
    assert load_state(tmp_gc_dir, team_id=TEAM) == {}
