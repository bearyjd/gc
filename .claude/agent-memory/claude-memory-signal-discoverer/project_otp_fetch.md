---
name: Auto-OTP implementation via gog gmail
description: How _fetch_gc_otp polls Gmail for the GameChanger OTP code, and the env var needed to prevent keyring prompts on headless systems
type: project
---

`_fetch_gc_otp()` in `gc_cli/session.py` fetches the GameChanger OTP automatically:

- Calls `gog gmail search` subprocess with the query filtering for `gamechanger-noreply@info.gc.com`
- Parses a 6-digit code with `r'\b(\d{6})\b'`
- Retries every 5 seconds up to a configurable timeout
- Raises `RuntimeError` on timeout or if `gog` is not on PATH

Critical env var: **`GOG_KEYRING_PASSWORD=""`** must be set (or the env must have it as empty string) to prevent `gog` from prompting for the system keyring password on headless servers. Without it, the subprocess hangs waiting for terminal input.

`GOG_ACCOUNT` env var routes `gog` to a specific Gmail account — mirrors the `_run_gog` pattern already used in `sync.py`.

OTP sender address: `gamechanger-noreply@info.gc.com`

**Why:** Headless servers (LXC .14) have no terminal for manual OTP entry. The auto-OTP path was added so the systemd timer can handle the rare case where the context file is absent or expired and a fresh OTP-challenged login is required.

**How to apply:** If OTP fetching fails silently on .14, check that `GOG_KEYRING_PASSWORD=""` is set in the systemd unit environment. If `gog` is missing from PATH in the unit, the `RuntimeError` will surface in the timer logs at `/tmp/gc-cron.log`.
