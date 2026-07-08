---
name: Per-team sync state files prevent cross-team cancellation
description: sync.py uses per-team state files to avoid one team's events being cancelled when another team syncs
type: project
---

`gc_cli/sync.py` uses per-team sync state files:

- Template: `sync-state-{team_id}.json` in `GC_DIR` (`~/.gc/`)
- Legacy single-file path `sync-state.json` is preserved for backward compatibility
- `load_state` and `save_state` accept `team_id: str | None = None`
- `sync_team` accepts `team_id: str | None = None` and passes it through

`cli.py` passes `team_id` to `sync_team`:
```python
result = sync_team(events, calendar_id, GC_DIR, dry_run=args.dry_run, team_id=team_id)
```

**Why:** When shared `sync-state.json` existed across all teams, syncing team B would find team A's events in state and issue cancellation API calls against team A's Google Calendar entries, producing 404 errors. Per-team state files eliminate cross-team state contamination entirely.

**How to apply:** Any new team-scoped operation in sync.py should follow the same `team_id`-keyed file pattern. Do not revert to a shared state file.
