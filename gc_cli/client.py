"""
GameChanger API client.

SCAFFOLD — API response parsing is stubbed until tested with real credentials.
The endpoints and auth pattern are correct; the response-parsing logic needs
to be filled in once we can inspect actual API responses.
"""

import json
import os
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config paths
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
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get_token() -> str:
    """Resolve GC_TOKEN from env var or ~/.gc/.env."""
    token = os.environ.get("GC_TOKEN")
    if token:
        return token
    file_env = _load_env()
    token = file_env.get("GC_TOKEN")
    if token:
        return token
    raise RuntimeError(
        "GC_TOKEN not found. Set it via:\n"
        "  export GC_TOKEN='your-token'\n"
        "or create ~/.gc/.env with:\n"
        "  GC_TOKEN=\"your-token\""
    )


def _load_teams() -> list[dict]:
    """Load tracked teams from ~/.gc/teams.json."""
    if not TEAMS_PATH.exists():
        return []
    return json.loads(TEAMS_PATH.read_text())


def _save_teams(teams: list[dict]) -> None:
    _ensure_dir()
    TEAMS_PATH.write_text(json.dumps(teams, indent=2) + "\n")


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class GCClient:
    """Thin wrapper around the GameChanger REST API."""

    def __init__(self, verbose: bool = True):
        self.token = _get_token()
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{BASE_URL}{path}"
        if self.verbose:
            _log(f"  GET {path}")
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # --- Teams -----------------------------------------------------------

    def get_my_teams(self) -> list[dict]:
        """GET /me/teams — list teams the authenticated user belongs to.

        STUB: Returns raw API response. Parsing TBD once we see the
        actual response shape.
        """
        data = self._get("/me/teams")
        # TODO: parse into normalized [{id, name, sport, season, ...}]
        if isinstance(data, list):
            return data
        # Some APIs wrap in {"teams": [...]}
        return data.get("teams", data.get("data", [data]))

    # --- Schedule --------------------------------------------------------

    def get_schedule(self, team_id: str) -> list[dict]:
        """GET /teams/{team_id}/schedule — upcoming games & practices.

        STUB: Returns raw API response. Parsing TBD.
        """
        data = self._get(f"/teams/{team_id}/schedule")
        if isinstance(data, list):
            return data
        return data.get("events", data.get("schedule", data.get("data", [data])))

    # --- Clips -----------------------------------------------------------

    def get_clips(self, team_id: str) -> list[dict]:
        """GET /clips?kind=event&teamId={team_id} — game clips / highlights.

        STUB: Returns raw API response. Parsing TBD.
        """
        data = self._get("/clips", params={"kind": "event", "teamId": team_id})
        if isinstance(data, list):
            return data
        return data.get("clips", data.get("data", [data]))

    # --- Summary (convenience) ------------------------------------------

    def get_team_summary(self, team_id: str) -> dict:
        """Fetch schedule + clips for one team. Returns combined dict."""
        schedule = self.get_schedule(team_id)
        clips = self.get_clips(team_id)
        return {
            "team_id": team_id,
            "schedule": schedule,
            "clips": clips,
        }
