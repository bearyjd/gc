"""
GameChanger → Google Calendar sync via gog CLI.

Public surface:
  load_state(gc_dir)                          → dict
  save_state(state, gc_dir)
  event_fingerprint(event)                    → str (16-char hex)
  sync_team(events, calendar_id, gc_dir, dry_run) → SyncResult
  _event_title(event, team_name, child)       → str
  _event_description(event, team_name, child) → str
"""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_STATE_FILENAME = "sync-state.json"  # legacy single-team path
SYNC_STATE_TEAM_TEMPLATE = "sync-state-{team_id}.json"
SYNC_LOCK_TEMPLATE = "sync-state-{team_id}.lock"

# gog calendar color IDs (from `gog calendar colors`)
COLOR_GAME = "9"       # #5484ed blue
COLOR_PRACTICE = "10"  # #51b749 green
COLOR_CANCELLED = "8"  # #e1e1e1 graphite

# Default event durations in minutes when end time is absent
DURATION_DEFAULTS: dict[str, int] = {
    "game": 120,
    "practice": 90,
    "default": 60,
}

# Sport emoji map (keyed on lowercase sport name fragments)
SPORT_EMOJI: dict[str, str] = {
    "baseball": "⚾",
    "softball": "🥎",
    "soccer": "⚽",
    "basketball": "🏀",
    "football": "🏈",
    "lacrosse": "🥍",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SyncResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state(gc_dir: Path, team_id: str | None = None) -> dict:
    """Read per-team sync state. Falls back to legacy sync-state.json."""
    if team_id:
        path = gc_dir / SYNC_STATE_TEAM_TEMPLATE.format(team_id=team_id)
        if not path.exists():
            # migrate from legacy single-file state if it exists
            legacy = gc_dir / SYNC_STATE_FILENAME
            if legacy.exists():
                try:
                    return json.loads(legacy.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    # legacy path (no team_id)
    path = gc_dir / SYNC_STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, gc_dir: Path, team_id: str | None = None) -> None:
    """Write per-team sync state (mode 0600)."""
    filename = (
        SYNC_STATE_TEAM_TEMPLATE.format(team_id=team_id)
        if team_id
        else SYNC_STATE_FILENAME
    )
    path = gc_dir / filename
    path.write_text(json.dumps(state, indent=2) + "\n")
    path.chmod(0o600)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def event_fingerprint(event: dict) -> str:
    """16-char hex digest of the fields that matter for change detection."""
    key = "".join([
        event.get("date", ""),
        event.get("time", ""),
        event.get("opponent", ""),
        event.get("type", ""),
        event.get("location", ""),
        event.get("home_away", ""),
        event.get("notes", ""),
        event.get("game_type", ""),
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _sport_emoji(sport: str) -> str:
    """Return emoji for a sport name, or empty string if unknown."""
    s = sport.lower()
    for fragment, emoji in SPORT_EMOJI.items():
        if fragment in s:
            return emoji
    return ""


def _event_type_label(event: dict) -> str:
    """Return a human-readable event type label from event_type / game_type."""
    etype = (event.get("type") or "").lower()
    game_type = (event.get("game_type") or "").lower()
    opponent = (event.get("opponent") or "").strip()
    home_away = (event.get("home_away") or "").lower()

    if "practice" in etype:
        return "Practice"
    if "scrimmage" in etype or "scrimmage" in game_type:
        return "Scrimmage"
    if "tournament" in etype or "tournament" in game_type:
        return "Tournament"
    if "game" in etype or opponent:
        # Build "Game vs Eagles" or "Game @ Eagles"
        if opponent:
            connector = "vs" if "home" in home_away else "@"
            return f"Game {connector} {opponent}"
        return "Game"
    # Fallback: capitalise whatever we have
    return (event.get("type") or "Event").title()


def _split_kids(child: str) -> str:
    """Expand a CamelCase multi-kid string into 'Kid1 & Kid2 & Kid3'.

    A boundary is detected where a lowercase letter is immediately followed by
    an uppercase letter (e.g. 'PennJack' → 'Penn & Jack').
    Single names pass through unchanged.
    """
    import re
    parts = re.sub(r"([a-z])([A-Z])", r"\1\n\2", child).split("\n")
    return " & ".join(parts)


def _event_title(event: dict, team_name: str | None, child: str | None) -> str:
    """Build a rich calendar event title.

    Format: '<emoji> <child|team_name> — <type_label>[ (Home|Away)]'
    Examples:
      '⚾ Alex — Game vs Eagles (Home)'
      'Tigers — Practice'
      '⚽ Maya — Game @ Rockets (Away)'
    """
    sport = event.get("sport") or ""
    emoji = _sport_emoji(sport)

    raw_label = child if child else (team_name or "")
    label = _split_kids(raw_label) if raw_label else raw_label
    type_label = _event_type_label(event)

    # Trailing home/away parenthetical only for games with known home_away
    home_away = (event.get("home_away") or "").lower()
    etype = (event.get("type") or "").lower()
    opponent = (event.get("opponent") or "").strip()
    is_game = "game" in etype or bool(opponent)
    if is_game and home_away in ("home", "away"):
        type_label = f"{type_label} ({'Home' if home_away == 'home' else 'Away'})"

    parts = []
    if emoji:
        parts.append(emoji)
    if label:
        parts.append(label)
        parts.append("—")
    parts.append(type_label)

    return " ".join(parts)


def _event_description(event: dict, team_name: str | None, child: str | None) -> str:
    """Build a multi-line calendar event description with all 5Ws.

    Omits lines whose value is empty. Always ends with gc_event_id footer.
    """
    lines: list[str] = []

    def _add(label: str, value: str) -> None:
        v = (value or "").strip()
        if v:
            lines.append(f"{label}: {v}")

    _add("Child", child or "")
    sport = event.get("sport") or ""
    team_part = f"{team_name} ({sport})" if team_name and sport else (team_name or "")
    _add("Team", team_part)

    etype = event.get("type") or ""
    game_type = event.get("game_type") or ""
    type_val = f"{etype} / {game_type}" if etype and game_type else (etype or game_type)
    _add("Type", type_val)

    opponent = (event.get("opponent") or "").strip()
    home_away = (event.get("home_away") or "").lower()
    if opponent:
        ha_label = f" ({home_away.title()})" if home_away in ("home", "away") else ""
        _add("Opponent", f"{opponent}{ha_label}")

    _add("Location", event.get("location_name") or event.get("location") or "")
    _add("Address", event.get("location_address") or "")
    _add("Notes", event.get("notes") or "")

    gc_id = event.get("id") or ""
    footer = f"\n— GameChanger event id: {gc_id}" if gc_id else ""

    return "\n".join(lines) + footer


def _event_color(event_type: str) -> str:
    t = event_type.lower()
    if "practice" in t:
        return COLOR_PRACTICE
    return COLOR_GAME  # default to blue for games and unknowns


def _reminders_for_event(event: dict) -> list[str]:
    """Return list of --reminder flag values for this event type."""
    etype = (event.get("type") or "").lower()
    if "practice" in etype:
        return ["popup:1h"]
    # Games (and anything else) get 1d + 1h
    return ["popup:1d", "popup:1h"]


def _iso_times(event: dict) -> tuple[str, str]:
    """Return (start_rfc3339, end_rfc3339) with timezone offset included.

    gog requires RFC3339 (e.g. 2026-04-13T14:45:00-04:00). The event's
    date/time are already in local time; we attach the timezone from the
    event's 'timezone' field (default: America/New_York) to produce a
    timezone-aware datetime, then format with UTC offset.
    """
    from zoneinfo import ZoneInfo

    date_str = event.get("date", "")
    time_str = event.get("time", "00:00") or "00:00"
    tz_name = event.get("timezone", "America/New_York") or "America/New_York"
    etype = event.get("type", "").lower()

    tz = ZoneInfo(tz_name)
    start: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(f"{date_str} {time_str}".strip(), fmt)
            start = naive.replace(tzinfo=tz)
            break
        except ValueError:
            continue

    if start is None:
        # Fallback: midnight today in event timezone
        start = datetime.now(tz=tz).replace(hour=0, minute=0, second=0, microsecond=0)

    duration_key = (
        "practice" if "practice" in etype else
        "game" if "game" in etype else
        "default"
    )
    end = start + timedelta(minutes=DURATION_DEFAULTS[duration_key])
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# gog subprocess
# ---------------------------------------------------------------------------

def _run_gog(args: list[str]) -> tuple[bool, str]:
    """Run `gog <args>`. Returns (success, stdout-or-stderr).

    Automatically sets GOG_KEYRING_PASSWORD="" (required on headless systems)
    and appends --account <GOG_ACCOUNT> if that env var is set.
    """
    env = os.environ.copy()
    env.setdefault("GOG_KEYRING_PASSWORD", "")

    account = os.environ.get("GOG_ACCOUNT")
    if account:
        args = list(args) + ["--account", account]

    result = subprocess.run(
        ["gog"] + args,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, result.stdout.strip()


def _parse_gcal_event_id(gog_output: str) -> str:
    """Extract Google Calendar event ID from gog create output.

    gog typically returns JSON with an "id" field.
    Falls back to raw output if parsing fails.
    NOTE: Verify actual output format with:
        gog calendar create <test_cal_id> --summary "test" --from <iso> --to <iso> --no-input
    on 192.168.1.14 and adjust this function if needed.
    """
    import re

    try:
        data = json.loads(gog_output)
        # gog wraps the event: {"event": {"id": "..."}}
        event = data.get("event", data) if isinstance(data, dict) else data
        event_id = (event.get("id") or event.get("eventId")) if isinstance(event, dict) else None
        if event_id:
            return str(event_id)
        return gog_output.strip()
    except (json.JSONDecodeError, AttributeError):
        pass

    m = re.search(r'"id":\s*"([^"]+)"', gog_output)
    if m:
        return m.group(1)

    # Last resort: return raw trimmed output
    return gog_output.strip()


def _migrate_legacy_gcal_id(value: str) -> str | None:
    """Convert a corrupted multi-line TSV blob into the bare GCal event id.

    Returns the extracted id, or None if the value looks unrecoverable
    (caller should treat as a fresh event).
    Already-clean ids (no whitespace) pass through unchanged.
    """
    import re

    # Already clean — no whitespace characters
    if re.match(r"^[A-Za-z0-9_]+$", value):
        return value

    # TSV blob: 'id\t<event_id>\n...'
    m = re.match(r"^id\t([^\n\r]+)", value)
    if m:
        return m.group(1).strip()

    return None


def _build_gog_create_args(
    calendar_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str,
    location: str,
    color: str,
    gc_id: str,
    team_id: str | None,
    reminders: list[str],
    event: dict,
) -> list[str]:
    """Build the full argument list for `gog calendar create`."""
    args = [
        "calendar", "create", calendar_id,
        "--summary", summary,
        "--from", start_iso,
        "--to", end_iso,
        "--description", description,
        "--event-color", color,
        "--private-prop", f"gc_event_id={gc_id}",
    ]
    if team_id:
        args += ["--private-prop", f"gc_team_id={team_id}"]
    if location:
        args += ["--location", location]
    for reminder in reminders:
        args += ["--reminder", reminder]
    args += ["--json", "--no-input"]
    return args


def _build_gog_update_args(
    calendar_id: str,
    gcal_event_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str,
    location: str,
    color: str,
    gc_id: str,
    team_id: str | None,
    reminders: list[str],
    event: dict,
) -> list[str]:
    """Build the full argument list for `gog calendar update`."""
    args = [
        "calendar", "update", calendar_id, gcal_event_id,
        "--summary", summary,
        "--from", start_iso,
        "--to", end_iso,
        "--description", description,
        "--event-color", color,
        "--private-prop", f"gc_event_id={gc_id}",
    ]
    if team_id:
        args += ["--private-prop", f"gc_team_id={team_id}"]
    if location:
        args += ["--location", location]
    for reminder in reminders:
        args += ["--reminder", reminder]
    args += ["--json", "--no-input"]
    return args


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync_team(
    events: list[dict],
    calendar_id: str,
    gc_dir: Path,
    dry_run: bool = False,
    team_id: str | None = None,
    team_name: str | None = None,
    child: str | None = None,
) -> SyncResult:
    """Diff events against sync-state.json and call gog for each change.

    - new events     → gog calendar create
    - changed events → gog calendar update
    - removed events → mark cancelled=True in state (soft-cancel); gog update with [CANCELLED] prefix
    - soft-cancelled events that resurface → un-cancel and run UPDATE instead of CREATE
    """
    if not dry_run and not shutil.which("gog"):
        raise RuntimeError(
            "gog not found on PATH. Is it installed on this machine?\n"
            "See: https://gogcli.sh"
        )

    # File lock: block (don't fail) if another process holds the lock
    lock_path: Path | None = None
    lock_fh = None
    if team_id and not dry_run:
        lock_path = gc_dir / SYNC_LOCK_TEMPLATE.format(team_id=team_id)
        lock_fh = open(lock_path, "w")  # noqa: WPS515

    result = SyncResult()

    try:
        if lock_fh is not None:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)  # blocks until acquired

        state = load_state(gc_dir, team_id=team_id)

        # --- one-time migration: clean up corrupted gcal_event_id values ------
        corrupted_keys = [
            k for k, v in state.items()
            if isinstance(v, dict) and "\t" in str(v.get("gcal_event_id", ""))
            or isinstance(v, dict) and "\n" in str(v.get("gcal_event_id", ""))
        ]
        if corrupted_keys:
            state_dirty = False
            for k in corrupted_keys:
                raw = state[k].get("gcal_event_id", "")
                cleaned = _migrate_legacy_gcal_id(raw)
                if cleaned is None:
                    print(
                        f"  WARN: could not recover gcal_event_id for gc_id={k!r}"
                        f" — dropping entry, will re-create as new event",
                        file=sys.stderr,
                    )
                    del state[k]
                else:
                    state[k]["gcal_event_id"] = cleaned
                state_dirty = True
            if state_dirty and not dry_run:
                save_state(state, gc_dir, team_id=team_id)

        incoming = {e["id"]: e for e in events if e.get("id")}

        # --- new and changed events ------------------------------------------
        for gc_id, event in incoming.items():
            fp = event_fingerprint(event)
            summary = _event_title(event, team_name, child)
            description = _event_description(event, team_name, child)
            start_iso, end_iso = _iso_times(event)
            color = _event_color(event.get("type", ""))
            location = (event.get("location") or "").strip()
            reminders = _reminders_for_event(event)

            existing = state.get(gc_id)
            is_soft_cancelled = existing and existing.get("cancelled", False)

            if existing is None:
                # Truly new event
                print(f"  + CREATE  {summary} ({start_iso})", file=sys.stderr)
                if not dry_run:
                    ok, out = _run_gog(_build_gog_create_args(
                        calendar_id, summary, start_iso, end_iso,
                        description, location, color, gc_id, team_id, reminders, event,
                    ))
                    if ok:
                        gcal_id = _parse_gcal_event_id(out)
                        state[gc_id] = {
                            "gcal_event_id": gcal_id,
                            "calendar_id": calendar_id,
                            "fingerprint": fp,
                            "summary": summary,
                        }
                        result.created.append(summary)
                    else:
                        print(f"  ERROR create {summary}: {out}", file=sys.stderr)
                        result.errors.append(f"create {summary}: {out}")
                else:
                    result.created.append(summary)

            elif is_soft_cancelled:
                # Event disappeared previously but has resurfaced — un-cancel via UPDATE
                gcal_event_id = existing["gcal_event_id"]
                print(f"  ^ UNCANCEL {summary} ({start_iso})", file=sys.stderr)
                if not dry_run:
                    ok, out = _run_gog(_build_gog_update_args(
                        calendar_id, gcal_event_id, summary, start_iso, end_iso,
                        description, location, color, gc_id, team_id, reminders, event,
                    ))
                    if ok:
                        state[gc_id] = {
                            "gcal_event_id": gcal_event_id,
                            "calendar_id": calendar_id,
                            "fingerprint": fp,
                            "summary": summary,
                        }
                        result.updated.append(summary)
                    else:
                        print(f"  ERROR uncancel {summary}: {out}", file=sys.stderr)
                        result.errors.append(f"uncancel {summary}: {out}")
                else:
                    result.updated.append(summary)

            elif existing["fingerprint"] != fp:
                # Event changed
                gcal_event_id = existing["gcal_event_id"]
                print(f"  ~ UPDATE  {summary} ({start_iso})", file=sys.stderr)
                if not dry_run:
                    ok, out = _run_gog(_build_gog_update_args(
                        calendar_id, gcal_event_id, summary, start_iso, end_iso,
                        description, location, color, gc_id, team_id, reminders, event,
                    ))
                    if ok:
                        state[gc_id]["fingerprint"] = fp
                        state[gc_id]["summary"] = summary
                        result.updated.append(summary)
                    else:
                        print(f"  ERROR update {summary}: {out}", file=sys.stderr)
                        result.errors.append(f"update {summary}: {out}")

            # else: unchanged — no gog call

        # --- removed events (soft-cancel) ------------------------------------
        removed_ids = set(state.keys()) - set(incoming.keys())
        for gc_id in removed_ids:
            entry = state[gc_id]
            # Skip entries that are already soft-cancelled
            if entry.get("cancelled", False):
                continue
            gcal_event_id = entry["gcal_event_id"]
            original_summary = entry.get("summary", "Event")
            cancelled_summary = f"[CANCELLED] {original_summary}"
            print(f"  x CANCEL  {original_summary} (gcal:{gcal_event_id})", file=sys.stderr)
            if not dry_run:
                ok, out = _run_gog([
                    "calendar", "update", calendar_id, gcal_event_id,
                    "--summary", cancelled_summary,
                    "--event-color", COLOR_CANCELLED,
                    "--no-input",
                ])
                if ok:
                    state[gc_id]["cancelled"] = True
                    result.cancelled.append(gcal_event_id)
                else:
                    print(f"  ERROR cancel {gcal_event_id}: {out}", file=sys.stderr)
                    result.errors.append(f"cancel {gcal_event_id}: {out}")
            else:
                result.cancelled.append(gcal_event_id)

        if not dry_run:
            save_state(state, gc_dir, team_id=team_id)

    finally:
        if lock_fh is not None:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    return result
