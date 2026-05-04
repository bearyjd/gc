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
# GC_TOKEN="eyJ..."              # Alternative if MFA is required (skips Playwright)
# GC_DEVICE_ID="a85ba962..."    # Optional alongside GC_TOKEN — helps GC recognize device
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
| `GC_TOKEN` | alt* | — | `gc-token` header value (skips Playwright; use if MFA blocks headless login) |
| `GC_DEVICE_ID` | no | — | Device ID sent with `GC_TOKEN`; helps GC recognize device, may skip OTP |
| `GC_CALENDAR_ID` | for sync | — | Google Calendar ID (e.g. `abc@group.calendar.google.com`) |
| `GOG_ACCOUNT` | for sync | — | Google account for `gog` CLI |

\* Set either `GC_EMAIL`+`GC_PASSWORD` (Playwright login) **or** `GC_TOKEN` (manual token).
Get `GC_TOKEN` and `GC_DEVICE_ID` from DevTools → Network → any `api.team-manager.gc.com` request headers.

Config priority: env vars > `~/.gc/.env`

## Usage

```
gc teams                                    # list teams
gc schedule  [--team ID] [--json]           # upcoming schedule
gc summary   [--team ID] [--json]           # schedule + clips in one shot
gc sync      [--team ID] [--calendar ID]    # sync schedule to Google Calendar
             [--dry-run] [--visible]
gc token-refresh  [--visible]               # refresh gc-token via saved browser context
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

# Refresh gc-token using saved browser context (no OTP)
gc token-refresh

# First-time token capture with browser window visible (handles OTP once)
gc token-refresh --visible

# Sync a specific team to a specific calendar
gc sync --team abc123 --calendar your-id@group.calendar.google.com
```

## JSON Output

```bash
# Schedule
gc schedule --json
# → [{id, date, time, timezone, type, opponent, location, home_away}]

# Summary (schedule + clips)
gc summary --json
# → {team_id, timestamp, schedule: [...], clips: [...]}
# clips: [{id, title, url, event_id}]
```

## How it works

- Logs in via headless Chromium (Playwright) using `GC_EMAIL` + `GC_PASSWORD`
- Browser context saved to `~/.gc/sessions/playwright_context.json` after first login — restoring it skips OTP on all subsequent runs
- If OTP is required in headless mode, automatically fetches the code from Gmail via `gog gmail search` — no human input needed
- Captures `gc-token` and `gc-device-id` from API request headers (not localStorage)
- Calls GameChanger's REST API (`https://api.team-manager.gc.com`)
- Key endpoints: `/me/teams`, `/teams/{id}/schedule`, `/clips?kind=event&teamId={id}`
- `gc sync` diffs events against `~/.gc/sync-state-{team_id}.json` (per-team) and calls `gog` CLI for Google Calendar ops
- All status/debug output goes to stderr; `--json` output is clean on stdout

## File layout

```
~/.gc/
  .env              # GC_EMAIL, GC_PASSWORD, GC_CALENDAR_ID (0600)
  teams.json        # [{id, name, sport, ...}] — team IDs to track
  sync-state-{team_id}.json  # per-team: GC event ID → {gcal_event_id, fingerprint, ...}
  sessions/
    playwright_context.json  # saved browser state — restores login without OTP (auto-managed)
    *.json                   # per-email session cache (60-min TTL)
```

## Token Keep-Alive (systemd timer)

`gc-token` is a short-lived JWT. The systemd timer refreshes it every 45 minutes
by restoring the saved Playwright browser context — the same way your real browser
silently renews its session. No OTP required after the first login.

If the saved context expires and GameChanger challenges with an OTP, the CLI
fetches the code automatically from Gmail via `gog` — the timer stays zero-touch.

### Install on LXC

```bash
# Installs gc, clones repo, and enables the timer in one shot
ssh root@<LXC_IP> 'bash -s' < install-lxc.sh
```

### First-time setup (one-time OTP)

If you have no saved browser context yet, do one headed login to handle the OTP:

```bash
gc token-refresh --visible
```

After that, the timer keeps the session alive indefinitely.

### Manual check

```bash
systemctl status gc-token-refresh.timer
systemctl list-timers gc-token-refresh.timer
journalctl -u gc-token-refresh.service -n 20
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

## Calendar Sync — Multi-Child Setup

### Adding a child name to event titles

`gc sync` can label calendar events with a child's name (e.g. "Alex — Game vs Eagles") instead of the team name. Add a `child` field to each team in `~/.gc/teams.json`:

```json
[
  {
    "id": "abc123",
    "name": "Tigers 12U",
    "sport": "baseball",
    "child": "Alex"
  },
  {
    "id": "def456",
    "name": "Lightning U10",
    "sport": "soccer",
    "child": "Maya"
  }
]
```

If `child` is absent, the team `name` is used as the label. No CLI flag changes needed.

### Event title format

```
<sport emoji> <child|team> — <type> [vs/@ <opponent>] [(Home|Away)]
```

Examples:
- `⚾ Alex — Game vs Eagles (Home)`
- `⚽ Maya — Game @ Rockets (Away)`
- `⚾ Alex — Practice`

Sport emojis: ⚾ baseball, 🥎 softball, ⚽ soccer, 🏀 basketball, 🏈 football, 🥍 lacrosse.

### Reminder policy (automatic)

| Event type | Reminders |
|------------|-----------|
| Game | 1 day before + 1 hour before |
| Practice (and all others) | 1 hour before |

No configuration needed — reminders are set automatically based on `event_type` from the GameChanger API.

### Dedup via private extended property

Every event created by `gc sync` is tagged with `gc_event_id=<id>` and `gc_team_id=<id>` as Google Calendar extended properties (`--private-prop`). This lets future tooling filter or audit GC-managed events. The per-team state file (`~/.gc/sync-state-{team_id}.json`) is the primary dedup mechanism; the private prop is a durable fallback tag on the event itself.

### One-time reset if duplicates already exist

If you ran `gc sync` before this update, your calendar may contain duplicate events created without the `gc_event_id` private property. The new code is idempotent for all **future** runs, but it cannot retroactively deduplicate events created by the old code.

To reset cleanly:

1. Open Google Calendar in a browser.
2. Navigate to the GameChanger calendar.
3. Delete all existing GC events manually (or use "Delete all events" if your calendar tool supports it).
4. Delete the per-team state files on the server:
   ```bash
   rm ~/.gc/sync-state-*.json
   ```
5. Run `gc sync` once to repopulate with the new rich format.

After this one-time reset, `gc sync` will never create duplicates — each GameChanger event ID maps to exactly one Google Calendar event, and the state file tracks it.

### Soft-cancel behaviour

When a GameChanger event disappears from the schedule (e.g. postponed), `gc sync` marks it `[CANCELLED]` in Google Calendar but **retains the state entry** with `cancelled: true`. If the event reappears later (rescheduled), the next sync run automatically un-cancels it via an UPDATE instead of creating a duplicate.

## License

MIT
