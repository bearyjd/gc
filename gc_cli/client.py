"""
GameChanger API client.

Accepts an injected requests.Session (from session.py).
Response parsing uses defensive fallbacks until real API shapes are confirmed.
"""

import json
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config paths (shared with session.py and cli.py)
# ---------------------------------------------------------------------------

GC_DIR = Path.home() / ".gc"
ENV_PATH = GC_DIR / ".env"
TEAMS_PATH = GC_DIR / "teams.json"

BASE_URL = "https://api.team-manager.gc.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Print to stderr (keeps stdout clean for --json)."""
    print(msg, file=sys.stderr)


def _ensure_dir() -> None:
    GC_DIR.mkdir(mode=0o700, exist_ok=True)


def _load_env() -> dict[str, str]:
    """Load key=value pairs from ~/.gc/.env (bash-style, quotes stripped)."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _load_teams() -> list[dict]:
    """Load tracked teams from ~/.gc/teams.json."""
    if not TEAMS_PATH.exists():
        return []
    return json.loads(TEAMS_PATH.read_text())


def _save_teams(teams: list[dict]) -> None:
    _ensure_dir()
    TEAMS_PATH.write_text(json.dumps(teams, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Normalizers — update these once real API responses are inspected
# ---------------------------------------------------------------------------

def _normalize_team(raw: dict) -> dict:
    """Normalize a raw team object to {id, name, sport, season}."""
    season_year = raw.get("season_year", "")
    season_name = raw.get("season_name", "")
    if season_year or season_name:
        season = f"{season_name} {season_year}".strip()
    else:
        season = raw.get("season", "")
    return {
        "id": raw.get("id") or raw.get("teamId", ""),
        "name": raw.get("name") or raw.get("teamName", "Unknown"),
        "sport": raw.get("sport", ""),
        "season": season,
    }


def _normalize_event(raw: dict) -> dict:
    """Normalize a raw schedule event to {id, date, time, type, opponent, location, home_away}.

    API returns each list item as {"event": {...}, "pregame_data": {...}}.
    pregame_data is only present for games (has opponent_name and home_away).
    """
    ev = raw.get("event", raw)
    pregame = raw.get("pregame_data") or {}

    # Parse UTC datetime → local date/time using event timezone
    start = ev.get("start") or {}
    dt_str = start.get("datetime", "")
    timezone_str = ev.get("timezone", "UTC")
    date = ""
    time_str = ""
    if dt_str:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(ZoneInfo(timezone_str))
            date = dt_local.strftime("%Y-%m-%d")
            time_str = dt_local.strftime("%H:%M")
        except Exception:
            date = dt_str[:10]
            time_str = dt_str[11:16]

    # Location: name + address lines
    loc = ev.get("location") or {}
    loc_name = loc.get("name", "")
    loc_addr = loc.get("address") or []
    if loc_addr:
        location = f"{loc_name}, {', '.join(loc_addr)}" if loc_name else ", ".join(loc_addr)
    else:
        location = loc_name

    return {
        "id": ev.get("id", ""),
        "date": date,
        "time": time_str,
        "timezone": timezone_str,
        "type": ev.get("event_type", ""),
        "opponent": pregame.get("opponent_name", "") or ev.get("title", ""),
        "location": location,
        "home_away": pregame.get("home_away", ""),
    }


def _normalize_clip(raw: dict) -> dict:
    """Normalize a raw clip to {id, title, url, event_id}."""
    return {
        "id": raw.get("id") or raw.get("clipId", ""),
        "title": raw.get("title") or raw.get("name", "Untitled"),
        "url": raw.get("url") or raw.get("clipUrl", ""),
        "event_id": raw.get("eventId") or raw.get("event_id", ""),
    }


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class GCClient:
    """Thin wrapper around the GameChanger REST API."""

    def __init__(self, session: requests.Session, verbose: bool = True):
        self.session = session
        self.verbose = verbose

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{BASE_URL}{path}"
        if self.verbose:
            _log(f"  GET {path}")
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            _log(f"  WARN: HTTP error on {path}: {e}")
            return []
        except (requests.RequestException, ValueError) as e:
            _log(f"  WARN: Request failed on {path}: {e}")
            return []

    # --- Teams -----------------------------------------------------------

    def get_my_teams(self) -> list[dict]:
        """GET /me/teams — list teams the authenticated user belongs to."""
        data = self._get("/me/teams")
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("teams", data.get("data", []))
        return [_normalize_team(t) for t in raw if isinstance(t, dict)]

    # --- Schedule --------------------------------------------------------

    def get_schedule(self, team_id: str) -> list[dict]:
        """GET /teams/{team_id}/schedule — upcoming games & practices."""
        data = self._get(f"/teams/{team_id}/schedule")
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("events", data.get("schedule", data.get("data", [])))
        return [_normalize_event(e) for e in raw if isinstance(e, dict)]

    # --- Clips -----------------------------------------------------------

    def get_clips(self, team_id: str) -> list[dict]:
        """GET /clips?kind=event&teamId={team_id} — game clips / highlights."""
        data = self._get("/clips", params={"kind": "event", "teamId": team_id})
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("clips", data.get("data", []))
        return [_normalize_clip(c) for c in raw if isinstance(c, dict)]

    # --- Summary ---------------------------------------------------------

    def get_team_summary(self, team_id: str) -> dict:
        """Fetch schedule + clips for one team."""
        return {
            "team_id": team_id,
            "schedule": self.get_schedule(team_id),
            "clips": self.get_clips(team_id),
        }
