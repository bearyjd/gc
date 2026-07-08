---
name: gc-scrape deployment topology and install method
description: How gc is installed and run on tower vs LXC .14, including uv tool install quirk
type: project
---

Two machines involved:

- **Tower** (local workstation with display): used for `gc token-refresh --visible` to capture the initial Playwright context. Has `uv` installed.
- **LXC .14** (`192.168.1.14`): headless server running a systemd timer every 45 min that calls `gc token-refresh` then `gc sync` per team. System Python is externally managed.

Install method on tower (externally managed Python):
```bash
uv tool install -e '.[browser]'
playwright install chromium
```
Note: `uv tool install` does not auto-reinstall on code changes — must reinstall manually after each source edit to pick up changes. Verify via `inspect.getfile`.

Install method on .14:
```bash
pip install 'gc[browser]' --break-system-packages
playwright install chromium
```

Workflow for initial zero-touch setup:
1. Run `gc token-refresh --visible` on tower (browser opens, user enters OTP once)
2. Copy `~/.gc/sessions/playwright_context.json` from tower to .14
3. Run `gc token-refresh` on .14 headlessly to confirm context restore works
4. Systemd timer takes over — fires every 45 min

**Why:** .14 has no display (`DISPLAY` unset, no `.Xauthority`), so Playwright cannot open a browser there. The context file approach means .14 never needs to open a browser at all.

**How to apply:** If the timer on .14 starts failing with OTP errors, the context file has expired. Re-run `gc token-refresh --visible` on tower and re-copy the file to .14.
