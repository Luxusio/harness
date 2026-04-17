---
freshness: current
---

# Scope Lock Pattern

Scope lock mechanizes the C-09 prose-only scope contract into a hard prewrite gate
enforced by `plugin/scripts/prewrite_gate.py`.

## PROGRESS.md schema

```yaml
task_id: TASK__my-feature
phase: 3
completed_acs: []
current_ac: AC-001
partial_ac: null

# Scope lock (prewrite_gate reads these three arrays)
allowed_paths:
  - src/feature.py
  - src/utils.py
  - plugin/CLAUDE.md

test_paths:
  - tests/test_feature.py
  - tests/fixtures/my-feature/

forbidden_paths:
  - src/billing.py        # separate concern
  - db/migrations/        # separate task

decisions: []
attempts: []
notes: []
updated: 2026-04-17T00:00:00Z
```

## Gate behavior

1. **forbidden write**: exits 2 with message naming `task_id`, matching pattern, allowed summary, and 3 fix options.
2. **allowed/test write**: exits 0 (proceed).
3. **unlisted path**: exits 0 + logs warn (auto-add to allowed with note per SKILL.md Phase 3.1).
4. **no PROGRESS.md**: scope lock not active — gate falls through to existing plan-first rule.
5. **malformed PROGRESS.md**: gate logs `gate-parse-fail` to `learnings.jsonl` and exits 0 (fail-safe).

## Env var bypass

`HARNESS_DISABLE_SCOPE_LOCK=1` — one-shot bypass. Creates `<task_dir>/audit/scope-lock-bypass.flag`, then clears it. Only bypasses one command; the next write is re-evaluated normally.

## Migration guidance

Existing tasks without PROGRESS.md are unaffected — scope lock is opt-in via PROGRESS.md presence.
New tasks get PROGRESS.md written by the plan skill at Phase 3.1.

## Pattern entries

| Pattern | Discovered | Source |
|---------|------------|--------|
| scope-lock-gate | 2026-04-17 | TASK__gstack-ideas-adoption |
