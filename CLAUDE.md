# CLAUDE.md — gc-scrape

Guidance for an AI coding agent picking up a bug report or feature request against this
repo with minimal human input. This file is specific to `gc-scrape`; the sibling repos
(`schoology-scrape/`, `ixl-scrape/`) each have their own. The parent
`/home/user/Documents/vibe-code/school/CLAUDE.md` gives a cross-repo overview but is
**stale on auth details for this package** — trust this file and `README.md` over the
parent doc's "Bearer token, no OAuth flow" description; the real flow is Playwright +
JWT + auto-OTP (see below).

## Verified run / test commands

Only commands confirmed to exist in `pyproject.toml`, `README.md`, or
`.github/workflows/ci.yml` — do not invent others.

```bash
# Install (editable, with Playwright extra — needed for login, not for API calls)
pip install -e '.[browser]'
playwright install chromium

# Install with dev/test deps (what CI uses)
pip install -e ".[dev]"

# Run the full test suite exactly as CI does (excludes the e2e shell script)
pytest tests/ -v --ignore=tests/e2e

# Run a single test file / test
pytest tests/test_sync.py -v
pytest tests/test_cli.py -k test_parse_gc_team_map_valid_pairs -v

# CLI commands (installed as `gc`)
gc teams                                    # list teams for the authenticated user
gc schedule  [--team ID] [--json]           # upcoming schedule for a team
gc summary   [--team ID] [--json]           # schedule + clips in one shot
gc sync      [--team ID] [--calendar ID] [--dry-run] [--visible]
gc token-refresh  [--visible]               # refresh gc-token via saved browser context
```

There is **no lint/type-check command wired into CI** — `.ruff_cache/` exists locally but
`pyproject.toml` has no `[tool.ruff]` section and CI only runs `pytest`. Do not assume a
`ruff check` or `mypy` gate exists unless you add one yourself.

The e2e script (`tests/e2e/test_sync_e2e.sh`) is **not part of the automated verification
loop** — see "Reproduction & verification chokepoints" below before relying on it.

## Package layout

```
gc_cli/
  client.py   # GCClient — wraps GET /me/teams, /teams/{id}/schedule, /clips
              # Response-shape normalizers: _normalize_team, _normalize_event, _normalize_clip
  session.py  # Auth: credential loading, Playwright login, JWT capture, OTP auto-fetch,
              # session cache, get_session() priority orchestration
  cli.py      # argparse commands (teams/schedule/summary/sync/token-refresh) + output formatting
  sync.py     # gc → Google Calendar sync via `gog` CLI subprocess; fingerprint diffing,
              # per-team state files, soft-cancel/uncancel logic
cron/gc-cron.sh       # iterates ~/.gc/teams.json, dumps schedule/summary JSON + runs sync
systemd/gc-token-refresh.{service,timer}   # refreshes gc-token every 45 min
install-lxc.sh        # idempotent installer for headless LXC deployment
```

## Auth / token model (read this before touching session.py)

Auth is **not** a static Bearer token as the parent doc's summary implies. It is a
short-lived JWT (`gc-token` header, format `eyJ...`, length > 200) with an **absolute
~63-minute expiry** — the API does not support token renewal; only a fresh authenticated
browser session can mint a new one.

`get_session()` in `gc_cli/session.py` tries auth sources in this priority order:

1. **`GC_TOKEN` env var / `~/.gc/.env`** — direct token, skips Playwright entirely. Used
   when a known-good token is already available (e.g. pasted manually from DevTools →
   Network → any `api.team-manager.gc.com` request → `gc-token` request header).
2. **`_load_cached_session(email)`** — an on-disk session cache
   (`~/.gc/sessions/{sha256(email)[:12]}.json`, 60-minute TTL) that stores headers +
   cookies from a prior login. Skips Playwright if still fresh.
3. **`_try_context_login()`** — restores a saved Playwright browser context
   (`~/.gc/sessions/playwright_context.json`). Navigates to `https://web.gc.com/home`,
   detects whether a login form reappears (JWT expired) and if so re-authenticates with
   email+password (no OTP needed — the device is already recognized), then re-captures a
   fresh `gc-token` from intercepted API response headers.
4. **`_playwright_login()`** — full headless login. If GameChanger challenges with an
   OTP, `_fetch_gc_otp()` polls `gog gmail search` for a 6-digit code from
   `gamechanger-noreply@info.gc.com` and submits it automatically — no human input
   required, provided `gog` is installed and authenticated to the right Gmail account.

**Where an agent gets stuck without this context:**

- The token is captured from **intercepted API request headers during a page
  navigation to `/home` or `/teams`**, not from login-page HTML, not from cookies, and
  not always from localStorage (localStorage read is only a fallback). GameChanger's SPA
  does not call `api.team-manager.gc.com` until after that navigation — code that waits
  for API calls before navigating will time out silently.
- `sync_playwright` is a **lazy import** inside functions (not module-level), specifically
  so the package works without the `playwright` extra installed when only `GC_TOKEN` is
  used. When writing tests that `unittest.mock.patch` this import, the correct patch
  target is `playwright.sync_api.sync_playwright`, not `gc_cli.session.sync_playwright`
  — patching the latter does nothing because the name is never bound at module level.
  Also: `playwright` must be listed in the `[dev]` extra in `pyproject.toml` (not only
  `[browser]`), because `mock.patch("playwright.sync_api....")` resolves that dotted
  string via a real import at patch-apply time even though production code never imports
  it eagerly — omitting it from `[dev]` causes `ModuleNotFoundError` in CI even though the
  lazy import itself never fires.
- `context.storage_state(path=...)` is a real Playwright call that **writes a file to
  disk**. A test that mocks `storage_state` with a bare `MagicMock` will silently skip
  writing the context file, then crash later on `CONTEXT_PATH.chmod(0o600)` with
  `FileNotFoundError`. Give the mock a `side_effect` that actually writes the file.
- **Token refresh is not automatic on failure** — there is no code path that detects "JWT
  is about to expire" proactively by decoding the token; expiry is discovered reactively
  (a 401 response, or a login form reappearing). The `gc-token-refresh.timer` systemd unit
  (every 45 min, see `systemd/gc-token-refresh.timer`) exists specifically to stay ahead
  of the ~63-minute absolute expiry by re-running `gc token-refresh` before it lapses —
  if that timer is disabled or its box is down, the next `gc sync`/`gc schedule` run will
  hit a 401 (headless) or trigger a full OTP-challenged Playwright login (which, if `gog`
  Gmail access is also broken, will fail and require a human to run
  `gc token-refresh --visible` on a machine with a display).
- Env var **`GOG_KEYRING_PASSWORD=""`** is required in the environment `gog` subprocess
  calls run in (both `_fetch_gc_otp` and `sync.py`'s `_run_gog`) — without it, `gog` blocks
  waiting for a keyring password prompt on headless servers, hanging indefinitely instead
  of failing fast.
- Two deployment machines are implied by the systemd/install scripts: a machine with a
  display for the one-time `gc token-refresh --visible` (to solve OTP once), and a
  headless target (e.g. an LXC container via `install-lxc.sh`) that only ever restores
  the saved `playwright_context.json` copied over from the display machine. An agent
  debugging a "token refresh suddenly needs OTP again" report should check whether the
  context file is stale/missing on the headless box before assuming a code bug.

## SCAFFOLD / response-parsing caveat — what's actually confirmed vs. guessed

`gc_cli/client.py` labels its parsing "SCAFFOLD" broadly, but the three endpoints are
**not equally unverified**:

- **`GET /teams/{id}/schedule`** — the envelope shape
  `{"event": {...}, "pregame_data": {...}}` per list item (see `_normalize_event`'s
  docstring and the CHANGELOG "Fixed" entry: "Schedule events parsed correctly — unwrap
  ... API envelope") **was confirmed against a real API response** at some point in this
  project's history. Treat this shape as trustworthy, though the individual field-level
  fallbacks inside it (`game_type`, `notes`, etc.) still use defensive multi-key `.get()`
  chains that were never individually field-checked against real data.
- **`GET /me/teams`** and **`GET /clips?kind=event&teamId={id}`** — the
  `data.get("teams", data.get("data", []))` and `data.get("clips", data.get("data", []))`
  fallback chains in `get_my_teams()` / `get_clips()` have **never been confirmed against
  a real response**. There is no fixture, log capture, or recorded example anywhere in
  this repo of what these two endpoints actually return. Treat any bug report touching
  team listing or clips as "shape unconfirmed" — do not assume the current fallback
  covers the real shape.
- **No JSON fixtures exist in this repo** (`tests/fixtures/` does not exist yet — see
  `.agent_native/agent_roadmap.md` item 1 and item 5 for how to add them). Until fixtures
  exist, the *only* way to inspect a real response is to run `gc teams --json` / `gc
  summary --json` on a machine with valid credentials (per the commented-out step in
  `docs/superpowers/plans/2026-04-12-gc-calendar-sync.md`, Step 4.3) — an agent without
  credentials cannot do this and should say so rather than guessing.

## Verification gaps (read before claiming a fix is done)

- `tests/test_cli.py` covers **only** `_parse_gc_team_map`. `cmd_teams`, `cmd_schedule`,
  `cmd_summary`, `cmd_sync`, `cmd_token_refresh`, and `_resolve_team_id` in `cli.py` have
  **zero** test coverage.
- `GCClient.get_my_teams`, `get_schedule`, `get_clips`, and all three `_normalize_*`
  functions in `client.py` have **zero** test coverage — this is the direct consequence of
  the SCAFFOLD status never having been tested.
- `tests/conftest.py`'s `mock_session` fixture sets an `Authorization: Bearer` header,
  which does **not** match real GC auth (`gc-token` header — see `_make_session()` in
  `session.py`). Don't copy this fixture as a template for testing `GCClient` auth; it
  models the wrong shape. (`test_session.py`'s own cache-roundtrip tests use
  `Authorization` too, but only to test cache persistence mechanics, not real auth shape —
  that's a more defensible use of the same header name.)
- `tests/test_sync.py` and `tests/test_session.py` are otherwise thorough (fingerprinting,
  soft-cancel/uncancel, state migration, OTP polling, Playwright mocking) — don't assume
  the whole suite is thin; it's specifically the API-response-parsing layer that's
  untested.
- Running `pytest tests/ -v --ignore=tests/e2e` (the CI command) will **not** catch a real
  API response-shape regression, because nothing in the unit suite calls the real API or
  a captured fixture of it.

## Reproduction paths — what needs a live account vs. what doesn't

- `tests/e2e/test_sync_e2e.sh` **requires a live GameChanger account, a real Google
  Calendar, `gog` installed and pre-authenticated, and network access** — its own header
  comment says to run it "on 192.168.1.14 where gog is authenticated." It is excluded
  from CI. An agent cannot run this offline or reproduce a bug through it without real
  credentials being supplied by a human.
- Everything in `tests/test_sync.py`, `tests/test_session.py`, and `tests/test_cli.py`
  runs fully offline with mocks/fixtures — these are the right place to add a
  reproduction test for any bug that doesn't require hitting the live GameChanger API.
- There is currently **no saved API response JSON anywhere in the repo** to replay a
  parsing bug against. A bug report like "clips aren't showing up" cannot be reproduced
  offline today; see `.agent_native/agent_roadmap.md` items 1, 4, and 5 for how to build
  that capability incrementally (starting with synthetic fixtures matching the documented
  shapes, then real captured-and-redacted ones once a human runs the commands with live
  credentials).

## Style / conventions specific to this repo

- All commands' human-readable status/progress output goes to **stderr**; only `--json`
  payloads go to **stdout**. Preserve this when adding new output.
- Config precedence everywhere: **explicit env var > `~/.gc/.env` file value**. Follow the
  same pattern (`os.environ.get(...)` first, then parse `.env`) for any new config value.
- Credential/session/state files are written `chmod 0o600` (files) / `0o700`
  (directories) — preserve this for any new persisted secret or session artifact.
- Per-team state (`sync-state-{team_id}.json`) exists specifically to prevent one team's
  sync from cancelling another team's calendar events (a real bug that was fixed this
  way — see `.claude/agent-memory/claude-memory-signal-discoverer/project_sync_state.md`).
  Do not reintroduce a single shared state file for multi-team logic.
- Follow the parent repo's global Python conventions (PEP 8, type annotations on all
  signatures, tests-first for new functions) as described in
  `/home/user/.claude/rules/python/*.md` — this repo's existing code already follows
  that pattern (see the RED→GREEN→REFACTOR history implied by `CHANGELOG.md`).
