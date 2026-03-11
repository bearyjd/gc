"""
gc — GameChanger team schedule & clips CLI scraper.

SCAFFOLD — API response parsing is stubbed until tested with real credentials.
Auth is via GC_TOKEN extracted from a browser session.

Usage:
    gc teams                           — list teams for the authenticated user
    gc schedule  [--team ID] [--json]  — upcoming schedule for a team
    gc summary   [--team ID] [--json]  — schedule + clips in one shot
"""

import argparse
import json
import sys
from datetime import datetime

import requests

from gc_cli.client import (
    GCClient,
    _ensure_dir,
    _load_teams,
    _save_teams,
    ENV_PATH,
    GC_DIR,
    TEAMS_PATH,
)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def output_teams(teams: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(teams, indent=2))
        return
    if not teams:
        print("No teams found.")
        return
    print(f"\n{'Name':<30} {'ID':<25} {'Sport'}")
    print("-" * 65)
    for t in teams:
        name = t.get("name", t.get("teamName", "Unknown"))
        tid = t.get("id", t.get("teamId", ""))
        sport = t.get("sport", "")
        print(f"{name:<30} {tid:<25} {sport}")
    print()


def output_schedule(events: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(events, indent=2))
        return
    if not events:
        print("No upcoming events found.")
        return
    print(f"\n{'Date':<14} {'Time':<8} {'Type':<12} {'Opponent / Title'}")
    print("-" * 55)
    for ev in events:
        date = ev.get("date", ev.get("start_date", ""))[:13]
        time = ev.get("time", ev.get("start_time", ""))[:7]
        etype = ev.get("type", ev.get("event_type", ""))[:11]
        title = ev.get("opponent", ev.get("title", ev.get("name", "")))[:35]
        print(f"{date:<14} {time:<8} {etype:<12} {title}")
    print()


def output_summary(summary: dict, as_json: bool) -> None:
    if as_json:
        summary["timestamp"] = datetime.now().isoformat()
        print(json.dumps(summary, indent=2))
        return
    team_id = summary.get("team_id", "unknown")
    schedule = summary.get("schedule", [])
    clips = summary.get("clips", [])

    print(f"\n{'=' * 60}")
    print(f"  GameChanger Summary — Team: {team_id}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")

    print(f"\n--- Schedule ({len(schedule)} events) ---")
    output_schedule(schedule, False)

    print(f"\n--- Clips ({len(clips)} clips) ---")
    if not clips:
        print("No clips found.")
    else:
        for c in clips[:10]:
            title = c.get("title", c.get("name", "Untitled"))
            print(f"  {title}")
    print()


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _resolve_team_id(args: argparse.Namespace, client: GCClient) -> str:
    """Get team_id from --team flag, or first team in ~/.gc/teams.json."""
    if hasattr(args, "team") and args.team:
        return args.team

    teams = _load_teams()
    if teams:
        tid = teams[0].get("id", teams[0].get("teamId", ""))
        if tid:
            return tid

    # Fall back to fetching from API
    api_teams = client.get_my_teams()
    if not api_teams:
        raise RuntimeError(
            "No teams found. Use 'gc teams' to list available teams, "
            "then save to ~/.gc/teams.json"
        )
    return api_teams[0].get("id", api_teams[0].get("teamId", ""))


def cmd_teams(args: argparse.Namespace) -> None:
    """List teams for the authenticated user."""
    client = GCClient(verbose=not args.json)
    teams = client.get_my_teams()
    output_teams(teams, args.json)

    # Offer to save if teams.json doesn't exist
    if teams and not TEAMS_PATH.exists() and not args.json:
        print("Tip: save teams for cron use:")
        print(f"  gc teams --json > {TEAMS_PATH}")


def cmd_schedule(args: argparse.Namespace) -> None:
    """Show schedule for a team."""
    client = GCClient(verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    events = client.get_schedule(team_id)
    output_schedule(events, args.json)


def cmd_summary(args: argparse.Namespace) -> None:
    """Full summary: schedule + clips for a team."""
    client = GCClient(verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    summary = client.get_team_summary(team_id)
    output_summary(summary, args.json)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gc",
        description="GameChanger team schedule & clips CLI scraper",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # gc teams
    sp_teams = subparsers.add_parser("teams", help="List your teams")
    sp_teams.add_argument("--json", action="store_true", help="JSON output")
    sp_teams.set_defaults(func=cmd_teams)

    # gc schedule
    sp_sched = subparsers.add_parser("schedule", help="Upcoming schedule")
    sp_sched.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sched.add_argument("--json", action="store_true", help="JSON output")
    sp_sched.set_defaults(func=cmd_schedule)

    # gc summary
    sp_sum = subparsers.add_parser("summary", help="Schedule + clips (all data)")
    sp_sum.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sum.add_argument("--json", action="store_true", help="JSON output")
    sp_sum.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
