"""
GameChanger → Google Calendar sync via gog CLI.

Public surface:
  load_state(gc_dir)                          → dict
  save_state(state, gc_dir)
  event_fingerprint(event)                    → str (16-char hex)
  sync_team(events, calendar_id, gc_dir, dry_run) → SyncResult
"""

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

SYNC_STATE_FILENAME = "sync-state.json"

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

def load_state(gc_dir: Path) -> dict:
    """Read ~/.gc/sync-state.json. Returns {} if missing or unreadable."""
    path = gc_dir / SYNC_STATE_FILENAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, gc_dir: Path) -> None:
    """Write state dict to ~/.gc/sync-state.json (mode 0600)."""
    path = gc_dir / SYNC_STATE_FILENAME
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
    ])
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _event_summary(event: dict) -> str:
    etype = event.get("type", "Event")
    opponent = event.get("opponent", "").strip()
    return f"{etype}: {opponent}" if opponent else etype


def _event_color(event_type: str) -> str:
    t = event_type.lower()
    if "practice" in t:
        return COLOR_PRACTICE
    return COLOR_GAME  # default to blue for games and unknowns


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


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def sync_team(
    events: list[dict],
    calendar_id: str,
    gc_dir: Path,
    dry_run: bool = False,
) -> SyncResult:
    """Diff events against sync-state.json and call gog for each change.

    - new events     → gog calendar create
    - changed events → gog calendar update
    - removed events → gog calendar update with [CANCELLED] prefix + grey color
    """
    if not dry_run and not shutil.which("gog"):
        raise RuntimeError(
            "gog not found on PATH. Is it installed on this machine?\n"
            "See: https://gogcli.sh"
        )

    state = load_state(gc_dir)
    result = SyncResult()
    incoming = {e["id"]: e for e in events if e.get("id")}

    # --- new and changed events ------------------------------------------
    for gc_id, event in incoming.items():
        fp = event_fingerprint(event)
        summary = _event_summary(event)
        start_iso, end_iso = _iso_times(event)
        color = _event_color(event.get("type", ""))

        if gc_id not in state:
            # New
            print(f"  + CREATE  {summary} ({start_iso})", file=sys.stderr)
            if not dry_run:
                ok, out = _run_gog([
                    "calendar", "create", calendar_id,
                    "--summary", summary,
                    "--from", start_iso,
                    "--to", end_iso,
                    "--event-color", color,
                    "--no-input",
                ])
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

        elif state[gc_id]["fingerprint"] != fp:
            # Changed
            gcal_event_id = state[gc_id]["gcal_event_id"]
            print(f"  ~ UPDATE  {summary} ({start_iso})", file=sys.stderr)
            if not dry_run:
                ok, out = _run_gog([
                    "calendar", "update", calendar_id, gcal_event_id,
                    "--summary", summary,
                    "--from", start_iso,
                    "--to", end_iso,
                    "--event-color", color,
                    "--no-input",
                ])
                if ok:
                    state[gc_id]["fingerprint"] = fp
                    state[gc_id]["summary"] = summary
                    result.updated.append(summary)
                else:
                    print(f"  ERROR update {summary}: {out}", file=sys.stderr)
                    result.errors.append(f"update {summary}: {out}")

        # else: unchanged — no gog call

    # --- removed events --------------------------------------------------
    removed_ids = set(state.keys()) - set(incoming.keys())
    for gc_id in removed_ids:
        entry = state[gc_id]
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
                del state[gc_id]
                result.cancelled.append(gcal_event_id)
            else:
                print(f"  ERROR cancel {gcal_event_id}: {out}", file=sys.stderr)
                result.errors.append(f"cancel {gcal_event_id}: {out}")
        else:
            result.cancelled.append(gcal_event_id)

    if not dry_run:
        save_state(state, gc_dir)

    return result
