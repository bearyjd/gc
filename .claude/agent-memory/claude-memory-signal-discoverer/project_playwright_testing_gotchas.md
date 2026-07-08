---
name: Playwright mocking gotchas in gc-scrape tests
description: Two non-obvious test failures when mocking Playwright in session.py — lazy import path and storage_state file side effect
type: project
---

Two gotchas discovered via failing tests when mocking Playwright in `gc_cli/session.py`:

**1. Lazy import — patch the source, not the module alias.**
`sync_playwright` is imported inside `_playwright_login()` (not at module level). Patching `gc_cli.session.sync_playwright` does nothing. The correct patch target is:
```
patch("playwright.sync_api.sync_playwright", ...)
```

**2. `storage_state` must write the file it is given.**
`context.storage_state(path=CONTEXT_PATH)` is a Playwright call that physically writes the context file. When mocked with a plain `MagicMock`, the file is never created, so the subsequent `CONTEXT_PATH.chmod(0o600)` raises `FileNotFoundError`. Tests must give `storage_state` a `side_effect` that writes the file:
```python
def write_context(path):
    Path(path).write_text("{}")

mock_context.storage_state.side_effect = write_context
```

**3. `playwright` must be in `[dev]` deps even though it is a lazy import.**
`unittest.mock.patch("playwright.sync_api.sync_playwright", ...)` resolves the dotted string by importing `playwright.sync_api` at patch-apply time, even when the production code never imports it at module level. In CI, `pip install -e ".[dev]"` was missing `playwright>=1.40` (it lived only in `[browser]`), causing `ModuleNotFoundError` on every test that patched that target. Fix: add `playwright>=1.40` to `[dev]` optional deps in `pyproject.toml`. No browser binaries are required — the Python package alone is sufficient.

**Why:** All three issues are invisible from reading session.py casually — the lazy import is deliberate (Playwright is optional on display-less systems), the file-write side effect is easy to miss in mocks, and the mock.patch import requirement is a Python stdlib detail that bites you only in CI where `[browser]` extras are not installed.

**How to apply:** Any time new tests cover `_playwright_login` or `_try_context_login`, apply all three fixes immediately. In particular, verify `pyproject.toml [dev]` includes `playwright` before pushing — CI will fail silently with `ModuleNotFoundError` otherwise.
