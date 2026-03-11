#!/usr/bin/env bash
# gc-cron.sh — scrape all tracked GameChanger teams and dump JSON
#
# Reads team IDs from ~/.gc/teams.json (array of objects with "id" field).
# Uses GC_TOKEN from ~/.gc/.env or environment.
#
# Outputs to $OUTPUT_DIR/{team_id}-schedule.json and {team_id}-summary.json
#
# Usage:
#   ./cron/gc-cron.sh                    # uses defaults
#   OUTPUT_DIR=/data/gc ./cron/gc-cron.sh

set -euo pipefail

TEAMS_FILE="${TEAMS_FILE:-$HOME/.gc/teams.json}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/gc}"

if [[ ! -f "$TEAMS_FILE" ]]; then
    echo "No teams file found at $TEAMS_FILE" >&2
    echo "Create it by running: gc teams --json > $TEAMS_FILE" >&2
    exit 1
fi

# Source token from .env if not already set
if [[ -z "${GC_TOKEN:-}" ]] && [[ -f "$HOME/.gc/.env" ]]; then
    # shellcheck source=/dev/null
    set -a
    source "$HOME/.gc/.env"
    set +a
fi

if [[ -z "${GC_TOKEN:-}" ]]; then
    echo "GC_TOKEN not set. Add it to ~/.gc/.env or export it." >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Parse team IDs from JSON array using python (available on all targets)
TEAM_IDS=$(python3 -c "
import json, sys
teams = json.load(open('$TEAMS_FILE'))
for t in teams:
    tid = t.get('id', t.get('teamId', ''))
    name = t.get('name', t.get('teamName', tid))
    if tid:
        print(f'{tid}:{name}')
")

while IFS=: read -r team_id team_name; do
    [[ -z "$team_id" ]] && continue

    # Sanitize name for filename (lowercase, replace spaces with dashes)
    safe_name=$(echo "$team_name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
    [[ -z "$safe_name" ]] && safe_name="$team_id"

    echo "[$(date '+%H:%M:%S')] Scraping $team_name ($team_id)..." >&2

    gc schedule --team "$team_id" --json > "$OUTPUT_DIR/${safe_name}-schedule.json" 2>/dev/null || \
        echo "[$(date '+%H:%M:%S')] WARN: schedule failed for $team_name" >&2

    gc summary --team "$team_id" --json > "$OUTPUT_DIR/${safe_name}-summary.json" 2>/dev/null || \
        echo "[$(date '+%H:%M:%S')] WARN: summary failed for $team_name" >&2

    echo "[$(date '+%H:%M:%S')] Done: $team_name" >&2
done <<< "$TEAM_IDS"

echo "[$(date '+%H:%M:%S')] All teams scraped -> $OUTPUT_DIR/" >&2
