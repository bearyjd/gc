# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-04-12

### Added
- `gc sync` command — syncs GameChanger schedule to Google Calendar via `gog` CLI
- `sync.py` — fingerprint-based change detection (create / update / cancel events)
- Playwright context persistence (`~/.gc/sessions/playwright_context.json`) — restores login without OTP on subsequent runs
- Capture `gc-device-id` alongside `gc-token` from API request headers for improved device recognition
- `_make_session()` helper — builds authenticated session with all required GC headers
- `GC_DEVICE_ID` env var support alongside `GC_TOKEN` for manual token path
- Test fixtures (`tmp_gc_dir`, `mock_session`) for session and sync unit tests

### Changed
- Authentication uses `gc-token` header instead of `Authorization: Bearer` per GameChanger API
- Login flow captures token from intercepted API request headers (not localStorage)
- `_playwright_login()` returns `Session` directly (previously returned `(Session, cookies)` tuple)
- `_token_from_env()` returns `(token, device_id)` tuple to support `GC_DEVICE_ID`
- `gc sync` passes RFC3339 times with UTC offset to `gog calendar create` (Google Calendar API requirement)
- Normalized schedule events include `timezone` field for accurate RFC3339 generation
- `client.py` accepts an injected `requests.Session` for testability

### Fixed
- Schedule events parsed correctly — unwrap `{"event": {...}, "pregame_data": {...}}` API envelope
- `gog` calendar create calls now include timezone offset, fixing 400 "Missing time zone" errors
- `gog` subprocess receives `GOG_KEYRING_PASSWORD=""` and `--account` flag for headless operation
- Google Calendar event ID parsed correctly from gog response envelope
- Session cache handles corrupt JSON without crashing

### Removed
- Tuple return value from `_playwright_login` (breaking internal API cleaned up)
