# gc — GameChanger Team Schedule & Clips CLI

> **SCAFFOLD** — API response parsing is stubbed until tested with real credentials. Endpoints and auth pattern are correct; response-parsing logic will be filled in once we can inspect actual API responses.

CLI scraper for GameChanger team data. Pulls schedules, game events, and clips. Designed to be called by an OpenClaw cron agent via `--json`.

## Setup

```bash
pip install -e .
```

Configure credentials:

```bash
# Create config directory
mkdir -p ~/.gc && chmod 700 ~/.gc

# Add your token (extracted from browser DevTools > Network > Authorization header)
cat > ~/.gc/.env << 'EOF'
GC_TOKEN="your-gc-token-here"
EOF
chmod 600 ~/.gc/.env
```

Then discover and save your teams:

```bash
gc teams --json > ~/.gc/teams.json
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GC_TOKEN` | yes | — | Bearer token from browser session |

Config priority: env vars > `~/.gc/.env`

## Usage

```
gc teams                           # list teams
gc schedule  [--team ID] [--json]  # upcoming schedule
gc summary   [--team ID] [--json]  # schedule + clips in one shot
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

- Authenticates via `GC_TOKEN` Bearer header (extracted from browser session)
- Calls GameChanger's REST API (`https://api.team-manager.gc.com`)
- Key endpoints: `/me/teams`, `/teams/{id}/schedule`, `/clips?kind=event&teamId={id}`
- All status/debug output goes to stderr; `--json` output is clean on stdout
- No browser automation needed — pure HTTP API calls

## ICS Calendar Sync (Interim)

As a secondary data path while the API scaffold is being tested, GameChanger
supports ICS calendar export:

1. Open GameChanger app > Team > Schedule > Share/Export
2. Copy the ICS feed URL
3. Subscribe in your calendar app, or fetch with curl:

```bash
curl -o /tmp/gc/team-schedule.ics "https://gc-calendar-url..."
```

This gives you game dates and times without needing API auth. The CLI
will supersede this once API response parsing is complete.

## File layout

```
~/.gc/
  .env              # GC_TOKEN (0600)
  teams.json        # [{id, name, sport, ...}] — team IDs to track
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
1. Create `~/.gc/.env` with `GC_TOKEN` on the server
2. Run `gc teams --json > ~/.gc/teams.json`

### 2. Create schedule in OpenClaw app

- **Name:** GameChanger Schedule Sync
- **Cron:** `0 6 * * *` (daily at 6am)
- **Prompt:** "Check today's GameChanger schedule and notify me of any games or practices via Signal."

## school-dashboard Integration

This tool outputs to `/tmp/gc/` which is read by `school-sync.sh` and fed into the `school-state` update pipeline alongside IXL and Schoology data.
