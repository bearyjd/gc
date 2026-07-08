# gc-scrape — Agent-Native Roadmap

Prioritized by **Human-Attention-Saved per Unit of Effort**. Items 1-5 are immediately
actionable — each states the exact file(s)/command(s) to touch and a concrete acceptance
criterion. Items 6+ are lower ROI or higher effort; tackle after the top 5.

This audit is code-only — no scraping was run and no student/child/roster data was
copied into this file or any deliverable.

---

## Top 5 — immediately actionable

### 1. Fixture-driven regression tests for the untested API-parsing normalizers — DONE (2026-07-07)
> Completed: `tests/fixtures/{teams_response_teams_key,teams_response_data_key,teams_response_bare_list,clips_response,schedule_response}.json` + `tests/test_client.py` (20 tests) now cover `get_my_teams()`, `get_schedule()`, `get_clips()`, and all three `_normalize_*` functions, including alternate-key and missing-field edge cases. Verified load-bearing by temporarily deleting the `data.get("data", [])` fallback in `get_my_teams()` — `test_get_my_teams_data_key_envelope` failed as expected, then passed again after restoring it.

**Why this is #1:** `gc_cli/client.py` is explicitly flagged SCAFFOLD ("Response parsing
uses defensive fallbacks until real API shapes are confirmed"), yet `tests/test_cli.py`
only tests `_parse_gc_team_map` — **`GCClient.get_my_teams()`, `GCClient.get_clips()`,
`_normalize_team()`, `_normalize_event()`, and `_normalize_clip()` have zero test
coverage.** This is the single biggest verification gap: if GameChanger changes its
`/me/teams` or `/clips` response shape, or if the current fallback chain
(`data.get("teams", data.get("data", [])`) is simply wrong, nothing will catch it.

**Files to touch:**
- Create `tests/fixtures/teams_response_teams_key.json` — synthetic response shaped
  `{"teams": [{...}]}`
- Create `tests/fixtures/teams_response_data_key.json` — synthetic response shaped
  `{"data": [{...}]}`
- Create `tests/fixtures/teams_response_bare_list.json` — synthetic response that is a
  bare JSON list (no envelope)
- Create `tests/fixtures/clips_response.json` — synthetic `{"clips": [...]}` shape
- Create `tests/test_client.py` — new test module, mocking `GCClient._get` (patch the
  method on an instance, or inject a fake `requests.Session` whose `.get()` returns a
  `MagicMock` with `.json()` returning the fixture content) to exercise
  `get_my_teams()`, `get_schedule()`, `get_clips()`, and each `_normalize_*` function
  directly with edge cases (missing fields, alternate key names like `teamId` vs `id`).

**Acceptance criteria:** New tests fail if `_normalize_team`/`_normalize_clip`/
`get_my_teams`/`get_clips` stop handling either the `teams`/`data` or `clips`/`data`
fallback branch (e.g. temporarily delete one fallback branch locally and confirm the
new test catches it), and pass against current code. This converts an implicit,
undocumented behavior into an explicit, regression-protected contract.

---

### 2. CLI command-layer test coverage (cli.py is 0% tested beyond one helper)
**Why:** `gc_cli/cli.py` wires `get_session()` → `GCClient` → output formatting → exit
codes for every user-facing command (`teams`, `schedule`, `summary`, `sync`,
`token-refresh`). None of `cmd_teams`, `cmd_schedule`, `cmd_summary`, `cmd_sync`,
`cmd_token_refresh`, or `_resolve_team_id` are exercised by any test. An agent asked to
fix "gc sync exits 0 when it shouldn't" or "token-refresh doesn't fall back correctly"
has to read code and reason from scratch instead of running a failing test.

**Files to touch:** `tests/test_cli.py` — add tests that `patch("gc_cli.cli.get_session")`
and `patch("gc_cli.cli.GCClient")` (or use `mock_session` fixture + fixture data from
item 1) to test:
- `_resolve_team_id`: explicit `--team` wins; falls back to first entry in
  `~/.gc/teams.json`; falls back to `client.get_my_teams()`; raises `RuntimeError` when
  all three are empty.
- `cmd_sync`: exits 1 when `GC_CALENDAR_ID` is unset and no `--calendar` given; exits 1
  when `result.errors` is non-empty.
- `cmd_token_refresh`: returns cleanly when `_try_context_login` succeeds; exits 1 on a
  401 in headless mode; falls through to full Playwright login when `--visible`.

**Acceptance criteria:** Each new test fails against a deliberately broken version of the
corresponding function (e.g. flip the exit-code condition) and passes against current
code.

---

### 3. Fix the misleading `mock_session` fixture in `tests/conftest.py` — DONE (2026-07-07)
> Completed: `mock_session` now sets `{"gc-token": ..., "gc-app-name": "web", "Accept": "application/json"}` matching `_make_session()`. Grepped the suite — only `test_session.py`'s cache-roundtrip tests still use `Authorization`, which per this item's own note is fine (header-persistence mechanics, not real GC auth shape).

**Why:** `tests/conftest.py`'s `mock_session` fixture sets
`Authorization: Bearer test-token-abc123`, but production auth (see `session.py
_make_session()` and the CHANGELOG "Changed" entry) uses a `gc-token` header, not
`Authorization: Bearer`. Any new test that reuses this fixture to simulate an
authenticated client will silently test the wrong auth shape — an agent copying this
fixture as a template will reproduce the mismatch.

**Files to touch:** `tests/conftest.py`, lines ~17-25.

**Acceptance criteria:** `mock_session` sets
`{"gc-token": "test-token-abc123", "gc-app-name": "web", "Accept": "application/json"}`
matching `_make_session()` in `gc_cli/session.py`. Grep the test suite after the change
to confirm no test asserts against the old `Authorization` key for GC API simulation
(the `Authorization` header pattern in `test_session.py`'s cache-roundtrip tests is fine
to leave — those tests only verify header persistence mechanics, not real GC auth shape,
but consider a one-line comment noting that).

---

### 4. Offline reproduction path: schedule-fixture → client → sync pipeline test
**Why:** `tests/e2e/test_sync_e2e.sh` is **not self-contained** — it requires a live
GameChanger account (`GC_EMAIL`/`GC_PASSWORD`), a real Google Calendar
(`GC_TEST_CALENDAR_ID`), `gog` installed and authenticated, and network access; its own
header says "run on 192.168.1.14 where gog is authenticated." There is currently no way
to reproduce a schedule-parsing-to-calendar-sync bug without a live account. `test_sync.py`
covers `sync_team()` in isolation using hand-built dict fixtures (`GAME_EVENT`, etc.), but
nothing chains a raw (fixture) API JSON response through `GCClient.get_schedule()`'s
normalizer and into `sync_team()`.

**Files to touch:**
- Reuse `tests/fixtures/schedule_response.json` from item 1 (or add one) with a
  realistic-shaped envelope: `{"event": {...}, "pregame_data": {...}}` per event, matching
  the confirmed real shape documented in `_normalize_event`'s docstring.
- New `tests/test_integration_schedule_to_sync.py`: mock `GCClient._get` to return the
  fixture, call `client.get_schedule(team_id)`, then feed the result into
  `sync_team(events, calendar_id, tmp_gc_dir, dry_run=True)` and assert on `SyncResult`.

**Acceptance criteria:** This test can reproduce and pin a full pipeline bug (e.g., an
event missing the `start.datetime` key, or a `pregame_data` that's `null` instead of
absent) end-to-end with zero network calls, zero `gog` dependency, and zero real
credentials — something the current suite cannot do.

---

### 5. Capture real `/me/teams` and `/clips` response shapes; document the gap in CLAUDE.md
**Why:** Per `docs/superpowers/plans/2026-04-12-gc-calendar-sync.md` (Step 4.3) and the
CHANGELOG, the **schedule** envelope shape (`{"event": {...}, "pregame_data": {...}}`) was
confirmed against a real response at some point (docstring in `_normalize_event` states it
as fact, and the CHANGELOG "Fixed" entry references it). **`/me/teams` and `/clips` have
never had their real shapes confirmed** — the `data.get("teams", data.get("data", []))`
and `data.get("clips", data.get("data", []))` fallbacks are pure guesses. An agent fixing
a teams/clips bug today has no ground truth to check against and would be guessing blind,
exactly as the SCAFFOLD warning implies for those two endpoints (but not schedule, which
is a nuance not currently documented anywhere).

**Files/commands to touch:**
- One-time manual capture (requires a live account — cannot be automated by an agent):
  `gc teams --json > /tmp/teams-real.json` and
  `gc summary --json` (inspect the `clips` key) on a machine with valid credentials.
- Redact any child/team-identifying names before committing; replace with placeholders
  like `"Team A"`, keep structural keys intact.
- Commit the redacted shape as `tests/fixtures/teams_response_real.json` /
  `tests/fixtures/clips_response_real.json`, and wire item 1's tests to validate against
  them instead of (or in addition to) synthetic fixtures.
- Update `gc_cli/client.py`'s normalizer docstrings and this repo's `CLAUDE.md` to state
  plainly: "schedule shape confirmed; teams/clips shape unconfirmed" instead of a single
  blanket SCAFFOLD label.

**Acceptance criteria:** `tests/fixtures/` contains at least one real (redacted) capture
for each of the three endpoints; `client.py` docstrings and `CLAUDE.md` no longer treat
all three endpoints as equally unverified.

---

## Lower-priority / follow-on items

6. **No schema validation on parsed output.** Normalized dicts (`_normalize_team`,
   `_normalize_event`, `_normalize_clip`) have no `TypedDict`/`pydantic`/`dataclass`
   enforcement — a silently-wrong field name just produces an empty string, no error.
   Consider `TypedDict` definitions in `client.py` plus a `mypy`/`pyright` check in CI so
   a normalizer typo becomes a type error instead of a silent empty field. Medium effort
   (needs a `dataclass` refactor of the normalize functions), medium value.

7. **`gc_cli/session.py` is 657 lines and does five distinct things** (credential
   loading, session-cache TTL logic, Playwright header capture, OTP polling via `gog`,
   and the public `get_session()` auth-priority orchestration). This exceeds the 800-line
   ceiling only barely today, but any one bug fix (e.g., in OTP polling) requires reading
   the whole file to find the four different places `page.evaluate(...)` duplicates the
   same localStorage-scanning JS snippet (in `_capture_gc_headers_from_page`,
   `_try_context_login`, and twice in `_playwright_login`). Extracting that duplicated JS
   into one helper (`_extract_token_from_storage(page) -> str | None`) would both shrink
   the file and remove a maintenance trap (a fix applied to one copy but not the other
   three). Low-to-medium effort, medium value for future debugging.

8. **`tests/e2e/test_sync_e2e.sh` has no CI or offline equivalent at all** — it's
   excluded from CI (`pytest tests/ -v --ignore=tests/e2e`) and only runs manually on a
   specific box (`192.168.1.14`). Consider at minimum a `README`/`CLAUDE.md` note (done —
   see item 4's offline alternative) plus keeping the e2e script as the final "real
   account" smoke test, not the primary regression gate.

9. **No `[tool.ruff]` section in `pyproject.toml`** despite a populated `.ruff_cache/` —
   ruff is being run with all-default settings via CLI, not a checked-in config, so
   lint behavior isn't reproducible/pinned for an agent running `ruff check .`
   independently. Low effort: add an explicit `[tool.ruff]` block matching whatever
   defaults are actually in use.

10. **Token-expiry detection is implicit, not explicit.** `session.py`'s JWT
    (~63-minute absolute expiry per prior investigation, see
    `.claude/agent-memory/claude-memory-signal-discoverer/project_auth_architecture.md`)
    is never checked for expiry directly — the code only discovers expiry reactively (a
    401, or a login form reappearing in `_try_context_login`). There's no
    `is_token_expired(token) -> bool` helper decoding the JWT `exp` claim, so a
    human/agent debugging "why did the timer suddenly need OTP" has to reason about HTTP
    status codes and page state instead of checking a clock. Would meaningfully reduce
    chokepoint #1 debugging time; medium effort (needs JWT decoding without a new
    dependency, e.g. manual base64 decode of the payload segment).

---

## Notes on existing tribal knowledge (already captured, but scattered)

`.claude/agent-memory/claude-memory-signal-discoverer/` already contains detailed,
verified notes on the auth architecture, OTP fetch mechanics, Playwright test mocking
gotchas, per-team sync state, and tower/.14 deployment topology. This is high-value and
accurate as of the last audit (2026-04-13), but it lives in a Claude-Code-specific memory
directory that a generic coding agent (or a human) would not know to check. The
`CLAUDE.md` written alongside this roadmap consolidates the load-bearing parts of that
memory into the one place an agent is guaranteed to read first.
