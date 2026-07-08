---
name: GC auth architecture and token lifecycle
description: How gc-scrape acquires, caches, and refreshes the GameChanger Bearer token — including Playwright context persistence and OTP avoidance
type: project
---

GC uses a short-lived JWT (~63 min absolute expiry). The API cannot renew it — only a new browser session can. Three acquisition paths exist, in priority order:

1. **`GC_TOKEN` env var / `~/.gc/.env`** — skips Playwright entirely; used when a known-good token is available.
2. **`_try_context_login()`** — restores `~/.gc/sessions/playwright_context.json` (saved browser context); fast path that avoids OTP even on a new cron run because GC does not re-challenge a recognised device context.
3. **`_playwright_login()`** — full headless login with auto-OTP via `_fetch_gc_otp()`.

The gc-token is a JWT (`eyJ...`, length > 200) captured from API request headers via Playwright response interception — NOT from login page HTML or cookies.

GC's SPA does NOT call `api.team-manager.gc.com` during the login/OTP flow — the token only appears after navigating to `/home`. Any code that waits for API calls before that navigation will time out.

**Why:** JWT lifetime is absolute (not sliding), so the systemd timer on .14 fires every 45 min to stay ahead of expiry. Context persistence is the key to zero-touch cron: save once on tower (where a display exists), copy `~/.gc/sessions/playwright_context.json` to .14, then all subsequent refreshes are headless.

**How to apply:** When debugging token acquisition failures, check path 2 first (context file exists and is fresh). If context is stale/absent, path 3 runs. If OTP appears in headless mode, the context file is gone or expired — re-run `gc token-refresh --visible` on a machine with a display to regenerate it.
