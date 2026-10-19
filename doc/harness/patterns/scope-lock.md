---
freshness: current
freshness_updated: 2026-10-19T06:06:14Z
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
---

# Scope Lock Pattern

Scope lock turns a task's declared scope into a prewrite-gate deny. It is the
last rule `plugin/scripts/prewrite_gate.py` applies to a direct write
(`Write` / `Edit` / `MultiEdit` / `apply_patch`). Bash and shell writes are not
gated.

## PROGRESS.md schema

```yaml
phase: implementation
current_ac: AC-001
partial_ac: null
completed_acs: []

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

```

These are the seven canonical top-level keys. `phase`, `current_ac`, and
`partial_ac` are strings or null; the other four values are lists of strings.
Task identity comes from the parent directory, acceptance intent from PLAN.md,
and review/QA evidence from RECEIPTS.jsonl. Detailed notes, decisions, attempts,
and timestamps belong in checkpoints, durable docs, or the final report.

## Gate behavior

The gate signals a deny with a JSON `permissionDecision: "deny"` on stdout and
exits 0 in every case; see `prewrite-gate.md` for the envelope.

1. **Reach.** Scope lock runs only for a source-extension file (`SOURCE_EXTENSIONS`)
   that passed every earlier rule: an active task exists and has PLAN.md (or a
   MAINTENANCE marker, or `execution_mode: micro`), and the REQ rule did not
   deny. Paths inside `doc/harness/tasks/`, `EXEMPT_PREFIXES`, workflow-control
   files, and non-source files (Markdown, JSON, YAML, config) were already
   decided, so `forbidden_paths` cannot block them.
2. **Forbidden write.** A path that matches a `forbidden_paths` entry is denied
   with rule `scope-lock-forbidden` (owner `developer`). The message names the
   task id, the matching entry, the first three `allowed_paths`, and three
   options: edit PROGRESS.md, move the edit to a separate task, or bypass with
   `HARNESS_DISABLE_SCOPE_LOCK=1`.
3. **Every other write is a silent allow.** `allowed_paths` only appears in the
   deny message and `test_paths` is parsed but unused; neither changes the
   decision. An unlisted path is allowed without a warning or a log row.
4. **No PROGRESS.md.** Scope lock is not active.
5. **Malformed entries.** An absolute, `..`, or out-of-tree entry is skipped and
   logged as `gate-parse-fail` in `doc/harness/learnings.jsonl`. A parse or
   enforcement exception is logged the same way and the write is allowed
   (C-12).

A validated same-repository worktree target uses that checkout's active task
and relative paths even when the native cwd remains main; see the target-root
routing in `prewrite-gate.md`.

Matching is `fnmatch` on the repo-relative path, plus a directory prefix match
for entries ending in `/` and a `<entry>/**` match.

## Env var bypass

`HARNESS_DISABLE_SCOPE_LOCK=1` skips scope lock only; every earlier rule still
applies. The gate reads the variable on every call and never clears it, so it
applies to every write while set in the runtime's environment (the environment
the runtime gives hook processes).

Each bypassed write overwrites `<task_dir>/audit/scope-lock-bypass.flag` with
the latest path. The next scope-lock evaluation without the variable deletes
that flag, so it is a last-bypass marker, not a durable audit record.

## Migration guidance

Existing tasks without PROGRESS.md are unaffected. Legacy verbose/prose files
remain best-effort readable and are not bulk migrated. New files are written by
the develop coordinator at Phase 3.1 using the seven-key canonical shape.

## Pattern entries

| Pattern | Discovered | Source |
|---------|------------|--------|
| scope-lock-gate | 2026-04-17 | TASK__gstack-ideas-adoption |
| scope-lock-docs-match-gate | 2026-09-28 | TASK__prewrite-gate-hardening |
