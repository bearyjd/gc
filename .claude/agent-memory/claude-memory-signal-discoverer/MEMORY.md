# Memory Index

- [GC auth architecture and token lifecycle](project_auth_architecture.md) — JWT ~63 min expiry; 3 acquisition paths; token captured from API headers after /home nav, not login HTML
- [Playwright mocking gotchas in tests](project_playwright_testing_gotchas.md) — lazy import requires patching playwright.sync_api not gc_cli.session; storage_state mock must write the file
- [Auto-OTP via gog gmail](project_otp_fetch.md) — _fetch_gc_otp polls gog gmail search; GOG_KEYRING_PASSWORD="" required on headless to prevent keyring hang
- [Per-team sync state files](project_sync_state.md) — sync-state-{team_id}.json prevents cross-team calendar cancellation (404) bug
- [Deployment topology tower vs .14](project_deployment.md) — visible login on tower once; copy playwright_context.json to .14; uv tool install needs manual reinstall on code changes
- [TDD cycle is expected for new features](feedback_tdd_workflow.md) — RED-GREEN-REFACTOR narrated explicitly; user accepts without pushback
- [Commit and push on short directive](feedback_commit_push_on_request.md) — execute immediately, no confirmation prompt; user is terse

## Scan Log

### 2026-04-13 — caller-provided session summary (CI failure debug)
- Scanned: 1 session (GitHub Actions run 24323720224, playwright ModuleNotFoundError in CI)
- UPDATE: 0 | CONTRADICT: 0 | FILL_GAP: 1 | NOISE: 0
- Accepted: FILL_GAP — added gotcha #3 to `project_playwright_testing_gotchas.md`: `playwright` must be in `[dev]` deps because `mock.patch` resolves dotted target strings via import, even for lazy imports
