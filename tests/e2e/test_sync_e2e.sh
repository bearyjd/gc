#!/usr/bin/env bash
# E2E test for gc sync — run on 192.168.1.14 where gog is authenticated.
#
# Prerequisites:
#   - gc installed: pip install -e '/opt/gc'
#   - gog on PATH and authenticated
#   - tests/e2e/.env present with GC_TEST_CALENDAR_ID, GC_EMAIL, GC_PASSWORD
#
# Run:
#   ssh root@192.168.1.14 'bash /opt/gc/tests/e2e/test_sync_e2e.sh'

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example and fill in values." >&2
  exit 1
fi

# shellcheck source=/dev/null
set -a
source "$ENV_FILE"
set +a

: "${GC_TEST_CALENDAR_ID:?must be set in tests/e2e/.env}"
: "${GC_EMAIL:?must be set in tests/e2e/.env}"
: "${GC_PASSWORD:?must be set in tests/e2e/.env}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# --- Step 1: dry run should succeed without calling gog -------------------
log "Step 1: dry-run sync..."
gc sync --calendar "$GC_TEST_CALENDAR_ID" --dry-run
log "  dry-run OK"

# --- Step 2: real sync ----------------------------------------------------
log "Step 2: real sync..."
gc sync --calendar "$GC_TEST_CALENDAR_ID"
log "  sync OK"

# --- Step 3: verify events appeared in Google Calendar -------------------
log "Step 3: verifying events in Google Calendar..."
EVENT_COUNT=$(gog calendar events "$GC_TEST_CALENDAR_ID" \
  --from "$(date -I)" \
  --to "$(date -d '+60 days' -I)" \
  --json | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data) if isinstance(data, list) else len(data.get('items', [])))")

if [[ "$EVENT_COUNT" -lt 1 ]]; then
  log "FAIL: No events found in calendar after sync"
  exit 1
fi
log "  Found $EVENT_COUNT event(s) — OK"

# --- Step 4: second sync should be a no-op (all unchanged) ---------------
log "Step 4: second sync (should be no-op)..."
SECOND_OUTPUT=$(gc sync --calendar "$GC_TEST_CALENDAR_ID" 2>&1)
if echo "$SECOND_OUTPUT" | grep -q "CREATE\|UPDATE"; then
  log "FAIL: second sync created/updated events unexpectedly"
  echo "$SECOND_OUTPUT"
  exit 1
fi
log "  no-op OK"

log ""
log "E2E PASSED — $EVENT_COUNT events synced to Google Calendar"
