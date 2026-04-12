# gc — GameChanger Team Schedule & Clips CLI

CLI scraper for GameChanger team data. Pulls schedules, game events, and clips. Designed to be called by an OpenClaw cron agent via `--json`.

## Setup

```bash
pip install -e '.[browser]'
playwright install chromium
```

Configure credentials:

```bash
mkdir -p ~/.gc && chmod 700 ~/.gc

cat > ~/.gc/.env << 'EOF'
GC_EMAIL="you@example.com"
GC_PASSWORD="yourpassword"
GC_CALENDAR_ID="your-calendar-id@group.calendar.google.com"
# GOG_ACCOUNT="you@gmail.com"
# GC_TOKEN="your-bearer-token"  # Alternative if MFA is required (skips Playwright)
EOF
chmod 600 ~/.gc/.env
```

Then discover and save your teams:

```bash
gc teams --json > ~/.gc/teams.json
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GC_EMAIL` | yes* | — | GameChanger account email |
| `GC_PASSWORD` | yes* | — | GameChanger account password |
| `GC_TOKEN` | alt* | — | Bearer token (skips Playwright; use if MFA is required) |
| `GC_CALENDAR_ID` | for sync | — | Google Calendar ID (e.g. `abc@group.calendar.google.com`) |
| `GOG_ACCOUNT` | for sync | — | Google account for `gog` CLI |

\* Set either `GC_EMAIL`+`GC_PASSWORD` (Playwright login) **or** `GC_TOKEN` (manual token).

Config priority: env vars > `~/.gc/.env`

## Usage

```
gc teams                                    # list teams
gc schedule  [--team ID] [--json]           # upcoming schedule
gc summary   [--team ID] [--json]           # schedule + clips in one shot
gc sync      [--team ID] [--calendar ID]    # sync schedule to Google Calendar
             [--dry-run] [--visible]
```

All commands accept `--json` for machine-readable output.

If `--team` is omitted, uses the first team from `~/.gc/teams.json`.

## Examples

```bash
# List all your teams
gc teams

# Schedule for a specific team
gc schedule --team abc123

# JSON dump (what the cron agent runs)
gc summary --json

# Save teams list for cron
gc teams --json > ~/.gc/teams.json

# Sync schedule to Google Calendar
gc sync

# Sync with dry-run (see what would change without calling gog)
gc sync --dry-run

# Sync a specific team to a specific calendar
gc sync --team abc123 --calendar your-id@group.calendar.google.com
```

## JSON Output

```bash
# Schedule
gc schedule --json
# → [{date, time, type, opponent, location, ...}]

# Summary (schedule + clips)
gc summary --json
# → {team_id, timestamp, schedule: [...], clips: [...]}
```

## How it works

- Logs in via headless Chromium (Playwright) using `GC_EMAIL` + `GC_PASSWORD`
- Session cached in `~/.gc/sessions/` for 60 minutes (keyed by email hash)
- Calls GameChanger's REST API (`https://api.team-manager.gc.com`)
- Key endpoints: `/me/teams`, `/teams/{id}/schedule`, `/clips?kind=event&teamId={id}`
- `gc sync` diffs events against `~/.gc/sync-state.json` and calls `gog` CLI for Google Calendar ops
- All status/debug output goes to stderr; `--json` output is clean on stdout

## File layout

```
~/.gc/
  .env              # GC_EMAIL, GC_PASSWORD, GC_CALENDAR_ID (0600)
  teams.json        # [{id, name, sport, ...}] — team IDs to track
  sync-state.json   # GC event ID → {gcal_event_id, fingerprint, ...}
  sessions/         # cached login sessions (auto-managed)
```

## Cron (Multiple Teams)

The included cron script iterates over teams in `~/.gc/teams.json` and dumps schedule JSON per team.

### 1. Save your teams

```bash
gc teams --json > ~/.gc/teams.json
```

### 2. Test it

```bash
./cron/gc-cron.sh
ls /tmp/gc/
# teamname-schedule.json  teamname-summary.json
```

### 3. Add to crontab

```bash
# Daily at 6am — scrape all teams
0 6 * * * /path/to/gc-scrape/cron/gc-cron.sh 2>/tmp/gc-cron.log

# Custom output directory
0 6 * * * OUTPUT_DIR=/data/gc /path/to/gc-scrape/cron/gc-cron.sh 2>/tmp/gc-cron.log
```

### Single team (ad-hoc)

```bash
gc schedule --team abc123 --json
```

## OpenClaw Integration

Use `gc` as an OpenClaw agent skill for automated schedule reports.

### 1. Install on LXC / remote server

```bash
ssh root@<LXC_IP> 'bash -s' < install-lxc.sh
```

This installs the `gc` CLI and clones the repo for the cron script. Idempotent — safe to re-run.

After install:
1. Create `~/.gc/.env` with `GC_EMAIL`, `GC_PASSWORD`, and `GC_CALENDAR_ID` on the server
2. Run `gc teams --json > ~/.gc/teams.json`

### 2. Create schedule in OpenClaw app

- **Name:** GameChanger Schedule Sync
- **Cron:** `0 6 * * *` (daily at 6am)
- **Prompt:** "Check today's GameChanger schedule and notify me of any games or practices via Signal."

## school-dashboard Integration

This tool outputs to `/tmp/gc/` which is read by `school-sync.sh` and fed into the `school-state` update pipeline alongside IXL and Schoology data.
