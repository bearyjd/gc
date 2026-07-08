---
name: Commit and push immediately when user says so — no extra confirmation
description: User says "commit and push" as a single short command and expects it done without further check-in
type: feedback
---

When the user says "commit and push", execute both operations immediately. In the auto-OTP session, "commit and push" produced a clean push to `bearyjd/gc` at `8108aff` with no back-and-forth.

**Why:** The user is terse ("commit and push", "you do it") and expects direct execution, not confirmation prompts or summaries before acting.

**How to apply:** On "commit and push" (or similar short directives), stage the relevant files, craft a conventional commit message from the work done, commit, and push. Do not ask "shall I push to main?" or summarize what will be committed before doing it.
