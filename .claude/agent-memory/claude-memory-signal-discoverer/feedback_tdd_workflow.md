---
name: TDD workflow is the expected approach for new features
description: User expects RED-GREEN-REFACTOR TDD cycle to be followed explicitly, including announcing each phase
type: feedback
---

When implementing new functions in gc-scrape, the user expects the full TDD cycle to be executed and narrated: write tests first (RED — confirm they fail for the right reason), implement to pass (GREEN), then lint/refactor (REFACTOR).

The assistant followed this cycle without prompting in the auto-OTP session and the user accepted it cleanly (no pushback, immediately asked to commit and push).

**Why:** The project has a test suite (`tests/test_session.py`) and the user tracks test counts explicitly. Skipping TDD or writing tests after implementation would be a deviation from the established pattern.

**How to apply:** For any new function added to `gc_cli/session.py` or elsewhere, write tests first, confirm RED failure, implement GREEN, then run lint. Do not combine phases or skip RED verification.
