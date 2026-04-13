# GC Auto-OTP & Zero-Touch Session Design

## Overview

Eliminate all manual intervention from the GameChanger authentication lifecycle. When GC requires an OTP, the CLI reads it automatically from Gmail via `gog`. The systemd timer on .14 can fully re-authenticate headlessly whenever the browser context expires — no human ever needed again.

## Architecture

Three targeted changes to `gc_cli/session.py`, no other files touched:

1. **`_fetch_gc_otp(timeout_sec)`** — new function that calls `gog gmail search` as a subprocess, parses the 6-digit code from the email subject, retries every 5s until found or timeout.

2. **OTP auto-entry in `_playwright_login`** — after clicking Sign In, detect if the page is stuck on a login/OTP URL. If so, call `_fetch_gc_otp`, type the code into the browser, submit. Works in both headless and `--visible` modes.

3. **`--visible` no longer required** — headless mode can now complete full re-authentication end-to-end. The systemd timer handles everything.

## Component: `_fetch_gc_otp`

```
GOG_KEYRING_PASSWORD="" gog gmail search \
  "from:gamechanger-noreply@info.gc.com newer_than:5m" \
  -j --results-only --no-input
```

- Parses subject with `re.search(r'\b(\d{6})\b', subject)`
- Retries every 5s, raises `RuntimeError` after `timeout_sec` (default 120s)
- Passes `--account <GOG_ACCOUNT>` if `GOG_ACCOUNT` env var is set (mirrors `_run_gog` pattern)
- `GOG_KEYRING_PASSWORD` defaults to `""` (required on headless systems, same as calendar calls)

## Component: OTP detection in `_playwright_login`

After `page.click('button:has-text("Sign in")')`:

1. Poll every 5s (up to 5 min) waiting for URL to leave login/verify pages
2. On each poll, check if an OTP input field is present on the page
3. If OTP field detected → call `_fetch_gc_otp(timeout_sec=120)`, type code, submit; URL will then leave login naturally
4. Once URL leaves login (OTP path or no-OTP path) → navigate to `/home`, capture token

The OTP input detection uses: `page.wait_for_selector('input[autocomplete="one-time-code"], input[type="tel"], input[inputmode="numeric"]', timeout=10000)` — falls back gracefully if no such field found.

## Data Flow

```
gc token-refresh (cron, every 45 min)
    ↓
_try_context_login()
    ├── context valid → capture token from /home → update ~/.gc/.env → done
    └── context stale/missing
            ↓
        _playwright_login(headless=True)
            ↓
        email + password filled automatically
            ↓
        OTP page detected?
            ├── No  → navigate /home, capture token → save context → done
            └── Yes → _fetch_gc_otp() polls Gmail every 5s
                        ↓
                      code found → type into browser → submit
                        ↓
                      navigate /home, capture token → save context → done
```

## Error Handling

- `_fetch_gc_otp` raises `RuntimeError("GC OTP not received within Ns")` if email doesn't arrive — this surfaces as a cron error, alerting the operator without silently failing
- If `gog` is not on PATH or Gmail search fails, `RuntimeError` is raised with a clear message
- All existing fallback paths (manual `GC_TOKEN`, `--visible` override) remain intact

## What Does Not Change

- `_try_context_login` — unchanged, still the fast primary path
- `sync.py`, `cli.py`, `client.py` — untouched
- Systemd timer interval (45 min) — unchanged
- `--visible` flag — still works, now just optional

## Success Criteria

- `gc token-refresh` (headless, no flags) completes full re-auth including OTP without human input
- The systemd timer on .14 never requires manual intervention
- `gc teams` returns valid data after a forced context expiry + headless re-auth cycle
