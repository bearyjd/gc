"""
gc — GameChanger team schedule & clips CLI scraper.

Usage:
    gc teams                                    — list teams for the authenticated user
    gc schedule  [--team ID] [--json]           — upcoming schedule for a team
    gc summary   [--team ID] [--json]           — schedule + clips in one shot
    gc sync      [--team ID] [--calendar ID]    — sync schedule to Google Calendar via gog
                 [--dry-run] [--visible]
"""

import argparse
import json
import os
import sys
from datetime import datetime

import requests

from gc_cli.client import (
    GCClient,
    _load_env,
    _load_teams,
    ENV_PATH,
    GC_DIR,
    TEAMS_PATH,
)
from gc_cli.session import get_session
from gc_cli.sync import sync_team, SyncResult


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _get_calendar_id() -> str | None:
    """Resolve GC_CALENDAR_ID from env or ~/.gc/.env."""
    cal_id = os.environ.get("GC_CALENDAR_ID")
    if cal_id:
        return cal_id
    env = _load_env()
    return env.get("GC_CALENDAR_ID")


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
        print(f"{t.get('name',''):<30} {t.get('id',''):<25} {t.get('sport','')}")
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
        date = ev.get("date", "")[:13]
        time = ev.get("time", "")[:7]
        etype = ev.get("type", "")[:11]
        title = ev.get("opponent", ev.get("title", ""))[:35]
        print(f"{date:<14} {time:<8} {etype:<12} {title}")
    print()


def output_summary(summary: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps({**summary, "timestamp": datetime.now().isoformat()}, indent=2))
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
            print(f"  {c.get('title', 'Untitled')}")
    print()


def output_sync_result(result: SyncResult, dry_run: bool) -> None:
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Sync complete:", file=sys.stderr)
    print(f"  Created:   {len(result.created)}", file=sys.stderr)
    print(f"  Updated:   {len(result.updated)}", file=sys.stderr)
    print(f"  Cancelled: {len(result.cancelled)}", file=sys.stderr)
    if result.errors:
        print(f"  Errors:    {len(result.errors)}", file=sys.stderr)
        for err in result.errors:
            print(f"    - {err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _resolve_team_id(args: argparse.Namespace, client: GCClient) -> str:
    if hasattr(args, "team") and args.team:
        return args.team

    teams = _load_teams()
    if teams:
        tid = teams[0].get("id", "")
        if tid:
            return tid

    api_teams = client.get_my_teams()
    if not api_teams:
        raise RuntimeError(
            "No teams found. Run 'gc teams' to list available teams, "
            "then save to ~/.gc/teams.json"
        )
    return api_teams[0].get("id", "")


def cmd_teams(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    teams = client.get_my_teams()
    output_teams(teams, args.json)

    if teams and not TEAMS_PATH.exists() and not args.json:
        print("Tip: save teams for cron use:")
        print(f"  gc teams --json > {TEAMS_PATH}")


def cmd_schedule(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    events = client.get_schedule(team_id)
    output_schedule(events, args.json)


def cmd_summary(args: argparse.Namespace) -> None:
    session = get_session(verbose=not args.json, visible=getattr(args, "visible", False))
    client = GCClient(session, verbose=not args.json)
    team_id = _resolve_team_id(args, client)
    summary = client.get_team_summary(team_id)
    output_summary(summary, args.json)


def cmd_sync(args: argparse.Namespace) -> None:
    calendar_id = args.calendar or _get_calendar_id()
    if not calendar_id:
        print(
            "Error: GC_CALENDAR_ID not set.\n"
            "Set it via env var or add to ~/.gc/.env:\n"
            '  GC_CALENDAR_ID="your-calendar-id@group.calendar.google.com"',
            file=sys.stderr,
        )
        sys.exit(1)

    session = get_session(verbose=True, visible=args.visible)
    client = GCClient(session, verbose=True)
    team_id = _resolve_team_id(args, client)

    print(f"  Fetching schedule for team {team_id}...", file=sys.stderr)
    events = client.get_schedule(team_id)
    print(f"  {len(events)} events fetched", file=sys.stderr)

    result = sync_team(events, calendar_id, GC_DIR, dry_run=args.dry_run)
    output_sync_result(result, args.dry_run)

    if result.errors:
        sys.exit(1)


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

    # gc sync
    sp_sync = subparsers.add_parser("sync", help="Sync schedule to Google Calendar via gog")
    sp_sync.add_argument("--team", type=str, help="Team ID (default: first in ~/.gc/teams.json)")
    sp_sync.add_argument("--calendar", type=str, help="Google Calendar ID (default: GC_CALENDAR_ID)")
    sp_sync.add_argument("--dry-run", action="store_true", help="Show planned changes without calling gog")
    sp_sync.add_argument("--visible", action="store_true", help="Run Playwright in headed mode (debug Cloudflare)")
    sp_sync.set_defaults(func=cmd_sync)

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
