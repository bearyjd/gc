# gc — GameChanger Calendar Sync Design

**Date:** 2026-04-12
**Status:** Approved
**Scope:** Playwright login, API response parsing, `gc sync` command via `gog`, CI autoversioning

---

## 1. Overview

The `gc` CLI currently has correct API endpoints and auth patterns but stubbed response parsing and no Google Calendar integration. This spec covers:

1. Replacing manual `GC_TOKEN` extraction with automated Playwright login (like `ixl`)
2. Completing API response parsing against real responses
3. A new `gc sync` command that pushes the team schedule to Google Calendar via the `gog` CLI (already authenticated on `192.168.1.14`)
4. GitHub repo creation, CI with `pytest`, and patch version autoincrement on green `main` builds

`gc` will eventually be part of the school-dashboard. The `gog` integration is the bridge — `gog` owns all Google auth and API surface; `gc` shells out to it.

---

## 2. Architecture

The codebase grows from 2 files to 4:

```
gc_cli/
  session.py    # NEW  — Playwright login, cookie cache (~/.gc/sessions/)
  client.py     # MOD  — accept requests.Session; complete response parsing
  sync.py       # NEW  — gog subprocess calls, sync-state.json management
  cli.py        # MOD  — wire session into all commands; add gc sync
```

**Runtime data flow for `gc sync`:**

```
gc sync
  └─ session.py   load cached session or run Playwright login
  └─ client.py    GET /teams/{id}/schedule → normalized event list
  └─ sync.py      load ~/.gc/sync-state.json
       ├─ new events     → gog calendar create <calId> --summary ... --from ... --to ...
       ├─ changed events → gog calendar update <calId> <eventId> --summary ...
       └─ removed events → gog calendar delete <calId> <eventId>
  └─ sync.py      write updated sync-state.json
```

---

## 3. Components

### 3.1 `session.py` (new)

Models `ixl_cli/session.py` exactly:

- `GCSession` class with `login()` — Playwright headless Chromium targets `https://app.gc.com`, exports cookies to `requests.Session`
- Cache: `~/.gc/sessions/{sha256(email)[:12]}.json`, 60-min TTL
- Playwright is a lazy import — only loaded at login time
- Public entry point: `get_session(verbose: bool) -> requests.Session`
- Credentials: `GC_EMAIL` + `GC_PASSWORD` from env or `~/.gc/.env` (replaces `GC_TOKEN`)
- `--visible` flag on login for debugging Cloudflare blocks

### 3.2 `client.py` (modified)

- Constructor accepts `requests.Session` instead of building its own from `GC_TOKEN`
- Response parsing TODOs filled in after first real Playwright login captures API responses
- All three endpoints normalize to consistent shapes:
  - Teams: `{id, name, sport, season}`
  - Schedule events: `{id, date, time, type, opponent, location, home_away}`
  - Clips: `{id, title, url, event_id}`

### 3.3 `sync.py` (new)

Single-purpose sync module:

```python
# Public surface
load_state(gc_dir: Path) -> dict          # reads ~/.gc/sync-state.json
save_state(state: dict, gc_dir: Path)     # writes ~/.gc/sync-state.json
event_fingerprint(event: dict) -> str     # sha256 of date+time+opponent+type
sync_team(events, calendar_id, gc_dir, dry_run) -> SyncResult
```

**Diff logic:**
- `new`: event ID not in state → `gog calendar create`
- `changed`: event ID in state but fingerprint differs → `gog calendar update`
- `removed`: event ID in state but not in fresh schedule → `gog calendar delete`

**Event duration defaults** (GameChanger may only return start time):
- Games: 2 hours
- Practices: 1.5 hours
- Unknown type: 1 hour

**`gog` commands:**
```bash
# Create
gog calendar create <calId> --summary "<type>: <opponent>" \
  --from <iso_start> --to <iso_end> \
  --event-color <9|10>   # 9=blue(game) 10=green(practice)

# Update
gog calendar update <calId> <gcalEventId> \
  --summary "<type>: <opponent>" --from <iso_start> --to <iso_end>

# Removed events — mark as cancelled (gog delete not confirmed in SKILL.md)
gog calendar update <calId> <gcalEventId> \
  --summary "[CANCELLED] <type>: <opponent>" --event-color 8
```

> **Note:** `gog calendar delete` is not listed in the SKILL.md common commands. Until confirmed available on `.14`, removed events are marked `[CANCELLED]` via update rather than deleted. If `gog calendar delete` exists, switch to hard delete in `sync.py`.

**`sync-state.json` shape:**
```json
{
  "gc_event_id": {
    "gcal_event_id": "...",
    "calendar_id": "...",
    "fingerprint": "sha256hex"
  }
}
```

### 3.4 `cli.py` (modified)

- All commands call `get_session()` to obtain a `requests.Session`
- New subcommand:

```
gc sync [--team ID] [--calendar ID] [--dry-run] [--visible]
```

- `--calendar` defaults to `GC_CALENDAR_ID` env var / `~/.gc/.env`
- `--dry-run` prints planned creates/updates/deletes, calls no `gog` commands
- `--team` defaults to first entry in `~/.gc/teams.json` (existing behaviour)
- `--visible` passes through to `session.py` to run Playwright in headed mode for debugging Cloudflare blocks

---

## 4. Configuration

**`~/.gc/.env` additions:**

| Variable | Required | Description |
|----------|----------|-------------|
| `GC_EMAIL` | yes | GameChanger login email |
| `GC_PASSWORD` | yes | GameChanger login password |
| `GC_CALENDAR_ID` | yes (for sync) | Google Calendar ID to sync into |
| `GOG_ACCOUNT` | no | Passed to `gog --account` if set |

`GC_TOKEN` is removed. All files in `~/.gc/` remain outside the repo.

**`.env.example` updated** to reflect new variables (no real values).

---

## 5. Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Playwright login fails | `RuntimeError` → stderr, exit 1 |
| Cloudflare blocks headless login | Message: "try `--visible` to debug" |
| Session expired mid-run | Auto re-login, transparent to caller |
| `gog` not on PATH | Fail fast before diff loop with clear message |
| `gog` command returns non-zero | Log to stderr, continue with remaining events (partial sync preferred) |
| Unknown API response shape | Log warning to stderr, return `[]`; `--json` stdout stays clean |
| Missing `GC_CALENDAR_ID` | Fail fast with actionable error before any API calls |

---

## 6. Testing

### Unit tests (`pytest`)

**`tests/conftest.py`**
- `mock_session` — pre-built `requests.Session` with `_logged_in=True`, bypasses Playwright
- `tmp_gc_dir` — isolated `~/.gc/` substitute in `tmp_path`

**`tests/test_sync.py`**
- `event_fingerprint` determinism and change detection
- `load_state` / `save_state` round-trip
- Diff logic: new / changed / removed event classification
- No real `gog` calls — `subprocess.run` is mocked

**`tests/test_session.py`**
- Cache load with valid TTL → no Playwright
- Cache load with expired TTL → triggers re-login (Playwright mocked)
- Missing credentials → clear error

### E2E tests (manual, on `192.168.1.14`)

**`tests/e2e/test_sync_e2e.sh`**

```bash
#!/usr/bin/env bash
# Requires: GC_TEST_CALENDAR_ID, GC_EMAIL, GC_PASSWORD set in tests/e2e/.env (gitignored)
set -euo pipefail
: "${GC_TEST_CALENDAR_ID:?must be set}"

gc sync --calendar "$GC_TEST_CALENDAR_ID" --dry-run
gc sync --calendar "$GC_TEST_CALENDAR_ID"
gog calendar events "$GC_TEST_CALENDAR_ID" \
  --from "$(date -I)" --to "$(date -d '+30 days' -I)" --json | \
  python3 -c "import sys,json; evts=json.load(sys.stdin); assert len(evts)>0,'No events found'"
echo "E2E PASSED"
```

Run: `ssh root@192.168.1.14 'bash /opt/gc/tests/e2e/test_sync_e2e.sh'`

---

## 7. CI & Autoversioning

### GitHub

- Repo: `bearyjd/gc` (create and push scaffold)
- `install-lxc.sh` already references this URL — works once repo is public

### `.github/workflows/ci.yml`

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --ignore=tests/e2e

      - name: Bump patch version
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: |
          NEW_VERSION=$(python3 - <<'EOF'
          import re, pathlib
          p = pathlib.Path("pyproject.toml")
          text = p.read_text()
          m = re.search(r'version = "(\d+\.\d+\.)(\d+)"', text)
          new_patch = int(m.group(2)) + 1
          new_version = m.group(1) + str(new_patch)
          p.write_text(text[:m.start()] + f'version = "{new_version}"' + text[m.end():])
          print(new_version, end="")
          EOF
          )
          echo "NEW_VERSION=$NEW_VERSION" >> "$GITHUB_ENV"

      - name: Commit and tag version bump
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add pyproject.toml
          git commit -m "chore: bump version to $NEW_VERSION"
          git tag "v$NEW_VERSION"
          git push --follow-tags
```

**`pyproject.toml` — add dev extras:**
```toml
[project.optional-dependencies]
dev = ["pytest", "pytest-cov"]
browser = ["playwright"]
```

---

## 8. Gitignore Additions

```gitignore
# Add to existing .gitignore
.pytest_cache/
tests/e2e/.env
tests/e2e/output/
*.log
```

All sensitive data lives in `~/.gc/` (outside repo). No real calendar IDs, event IDs, email addresses, or team IDs appear in committed code or fixtures. Test fixtures use placeholder values (`"team_abc123"`, `"evt_xyz"`).

---

## 9. File Layout After Implementation

```
gc-scrape/
  gc_cli/
    __init__.py
    session.py          # Playwright login + session cache
    client.py           # GCClient (normalized API responses)
    sync.py             # gog subprocess + sync-state diff
    cli.py              # argparse + all commands
  tests/
    conftest.py
    test_session.py
    test_sync.py
    e2e/
      test_sync_e2e.sh  # run on 192.168.1.14
      .env              # gitignored — GC_TEST_CALENDAR_ID etc.
  .github/
    workflows/
      ci.yml
  docs/
    superpowers/
      specs/
        2026-04-12-gc-calendar-sync-design.md
  .env.example          # updated with new variables
  .gitignore            # updated
  pyproject.toml        # updated with dev/browser extras
  README.md
  install-lxc.sh
  cron/
    gc-cron.sh
```

---

## 10. school-dashboard Integration Note

`gc` outputs to `/tmp/gc/` which is already wired into `school-sync.sh` alongside IXL and Schoology data. The `gc sync` command is a separate concern from the JSON data pipeline — cron will run both:

```bash
10 6 * * * /opt/gc/cron/gc-cron.sh 2>/tmp/gc-cron.log       # JSON for school-dashboard
15 6 * * * gc sync 2>/tmp/gc-sync.log                        # Google Calendar sync via gog
```

When `gc` is formally integrated into school-dashboard, `gog` auth and calendar ID config will be shared rather than duplicated.
