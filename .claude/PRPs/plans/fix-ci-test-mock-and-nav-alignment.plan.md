# Plan: Fix CI Test Mock and Align Playwright Login Navigation

## Summary
The latest CI run (35d23ca) fails because `test_playwright_login_headless_skips_otp_when_no_otp_field` mocks all `wait_for_selector` calls as exceptions, but the updated `_playwright_login` now calls `wait_for_selector` for email/password fields before reaching the OTP loop. Additionally, `_try_context_login` was updated to navigate to `/teams` for more reliable API token capture, but `_playwright_login` still uses `/home` — these should be aligned.

## User Story
As a developer, I want CI to pass reliably, so that every push to main is green and regressions are caught immediately.

## Problem → Solution
Broken test mock (all `wait_for_selector` calls raise timeout) → discriminate by selector (email/password succeed, OTP fails per test case). `/home` navigation may not trigger authenticated API calls → change to `/teams` like `_try_context_login`.

## Metadata
- **Complexity**: Small
- **Source PRD**: N/A
- **PRD Phase**: N/A
- **Estimated Files**: 2

---

## UX Design

Internal change — no user-facing UX transformation.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `gc_cli/session.py` | 398-552 | `_playwright_login` — all selectors and navigation |
| P0 | `tests/test_session.py` | 222-322 | `_make_playwright_mocks` and the failing test |
| P1 | `gc_cli/session.py` | 269-309 | `_try_context_login` — the `/teams` pattern to mirror |

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: tests/test_session.py:222-226
def _make_playwright_mocks(
    *,
    otp_field_found: bool = False,
    gc_token: str = "eyJ" + "a" * 250,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
```

### TEST_STRUCTURE
```python
# SOURCE: tests/test_session.py:309-321
def test_playwright_login_headless_skips_otp_when_no_otp_field(tmp_gc_dir, monkeypatch):
    """Headless _playwright_login does NOT call _fetch_gc_otp when no OTP field is present."""
    monkeypatch.setattr("gc_cli.session.SESSION_DIR", tmp_gc_dir / "sessions")
    monkeypatch.setattr("gc_cli.session.CONTEXT_PATH", tmp_gc_dir / "sessions" / "ctx.json")

    mock_pw_cm, _, _, _ = _make_playwright_mocks(otp_field_found=False)

    with patch("playwright.sync_api.sync_playwright", return_value=mock_pw_cm), \
         patch("gc_cli.session._fetch_gc_otp") as mock_otp:
        from gc_cli.session import _playwright_login
        _playwright_login("user@gc.com", "pass", visible=False)

    mock_otp.assert_not_called()
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tests/test_session.py` | UPDATE | Fix `_make_playwright_mocks` so email/password selectors succeed |
| `gc_cli/session.py` | UPDATE | Change `_playwright_login` navigation from `/home` to `/teams` |
| `gc_cli/session.py` | UPDATE | Bump `page.wait_for_timeout` after `/teams` nav to match (15s for fresh login) |

## NOT Building

- No new test cases — existing tests cover the behavior
- No changes to `_try_context_login` — already correct
- No changes to `_capture_gc_headers_from_page` — not part of this fix

---

## Step-by-Step Tasks

### Task 1: Fix `_make_playwright_mocks` to discriminate selectors
- **ACTION**: Update the `wait_for_selector` mock to succeed for email/password selectors, fail only for OTP selector when `otp_field_found=False`
- **IMPLEMENT**: Replace inline side_effect with a discriminator function:

```python
# wait_for_selector: succeed for email/password, fail for OTP when not found
def _wfs(selector, **kwargs):
    if "email" in selector or "password" in selector:
        return MagicMock()
    if otp_field_found:
        return MagicMock()
    raise Exception("timeout")

mock_page.wait_for_selector.side_effect = _wfs
```

- **MIRROR**: Matches existing side_effect pattern, just adds conditional logic
- **IMPORTS**: None — uses existing `MagicMock` import
- **GOTCHA**: Must handle both `otp_field_found=True` and `False` correctly. When True, OTP selector should succeed too (same as before). The `_wfs` function handles both.
- **VALIDATE**: `pytest tests/test_session.py -v` — all 19 session tests pass

### Task 2: Align `_playwright_login` navigation to `/teams`
- **ACTION**: Change headless and visible navigation from `/home` to `/teams` in `_playwright_login`
- **IMPLEMENT**: 
  - Line 442 (visible mode): `page.goto("https://web.gc.com/home", ...)` → `page.goto("https://web.gc.com/teams", ...)`
  - Line 501 (headless mode): `page.goto("https://web.gc.com/home", ...)` → `page.goto("https://web.gc.com/teams", ...)`
  - Keep `wait_until="domcontentloaded"` and timeout values unchanged
- **MIRROR**: `_try_context_login` line 309: `page.goto("https://web.gc.com/teams", timeout=30000, wait_until="domcontentloaded")`
- **IMPORTS**: None
- **GOTCHA**: The `/teams` page also triggers authenticated API calls that carry `gc-token` in request headers, same as `/home`. The network interception handler (`page.on("response", handle_response)`) is active before navigation. localStorage fallback is unaffected.
- **VALIDATE**: All Playwright login tests still pass (they mock `page.goto` via MagicMock so URL change is invisible to tests)

---

## Testing Strategy

### Unit Tests

All existing tests should continue to pass. No new tests needed — the discriminator behavior is already covered by the two OTP test cases (`otp_field_found=True/False`).

### Edge Cases Checklist
- [x] OTP field found → `_wfs` returns MagicMock for all selectors (test: `test_playwright_login_headless_calls_fetch_otp_when_otp_field_detected`)
- [x] OTP field not found → `_wfs` raises Exception("timeout") only for OTP selector (test: `test_playwright_login_headless_skips_otp_when_no_otp_field`)
- [x] Email/password selectors always succeed regardless of `otp_field_found` (implicit in both tests)

---

## Validation Commands

### Static Analysis
```bash
ruff check tests/test_session.py gc_cli/session.py
```
EXPECT: All checks passed

### Unit Tests
```bash
python -m pytest tests/ -v
```
EXPECT: 36 passed

---

## Acceptance Criteria
- [ ] `_make_playwright_mocks` discriminates selectors (email/password succeed, OTP fails per test case)
- [ ] `_playwright_login` navigates to `/teams` in both headless and visible modes
- [ ] All 36 tests pass
- [ ] Lint clean

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `/teams` doesn't trigger API calls on fresh login | Low | Medium | Network handler is active before navigation; localStorage fallback as safety net |

## Notes
The test mock fix (Task 1) is the critical part — this is what broke CI. Task 2 is preventative alignment so both login paths use the same URL strategy for token capture.
