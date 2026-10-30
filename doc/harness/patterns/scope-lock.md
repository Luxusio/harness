---
freshness: current
freshness_updated: 2026-10-30T00:00:00Z
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
---

# Scope Lock Pattern

Scope lock turns a WBS's declared scope into a prewrite-gate deny. It runs
before the final execution-assignment check in `plugin/scripts/prewrite_gate.py`
for a direct write
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
  - db/migrations/        # separate WBS

```

These are the seven canonical top-level keys. `phase`, `current_ac`, and
`partial_ac` are strings or null; the other four values are lists of strings.
WBS identity comes from the canonical `doc/harness/wbs/WBS__<id>` directory,
acceptance intent from PLAN.md,
and measured outcome checks from WBS.json. Actual independent review and QA
remain workflow requirements. Detailed notes, decisions, attempts,
and timestamps belong in checkpoints, durable docs, or the final report.

## Gate behavior

The gate signals a deny with a JSON `permissionDecision: "deny"` on stdout and
exits 0 in every case; see `prewrite-gate.md` for the envelope.

1. **Reach.** Scope lock runs only for a source-extension file (`SOURCE_EXTENSIONS`)
   that passed every earlier rule: an exact-session current-open-run WBS
   binding exists and has PLAN.md (or a
   MAINTENANCE marker, or `execution_mode: micro`), and the REQ rule did not
   deny. Other paths inside `doc/harness/wbs/`, `EXEMPT_PREFIXES`, workflow-control
   files, and non-source files (Markdown, JSON, YAML, config) were already
   decided, so `forbidden_paths` cannot block them. The entire retired
   `doc/harness/tasks/` tree is protected earlier, without a scope exemption.
   Integration custody and REQ checks precede scope lock; current execution
   assignment is checked afterward.
2. **Forbidden write.** A path that matches a `forbidden_paths` entry is denied
   with rule `scope-lock-forbidden` (owner `developer`). The message names the
   WBS id, the matching entry, the first three `allowed_paths`, and three
   options: edit PROGRESS.md, move the edit to a separate WBS, or bypass with
   `HARNESS_DISABLE_SCOPE_LOCK=1`.
3. **Every other path passes scope lock.** `allowed_paths` only appears in the
   deny message and `test_paths` is parsed but unused; neither changes the
   scope decision. An unlisted path passes without a warning or a log row;
   the final execution-assignment check still applies.
4. **No PROGRESS.md.** Scope lock is not active.
5. **Malformed entries.** An absolute, `..`, or out-of-tree entry is skipped and
   logged as `gate-parse-fail` in `doc/harness/learnings.jsonl`. A parse or
   enforcement exception is logged the same way and scope lock allows the write
   (C-12).

A validated same-repository worktree target uses that checkout's bound WBS
and relative paths even when the native cwd remains main; see the target-root
routing in `prewrite-gate.md`.

Matching is `fnmatch` on the repo-relative path, plus a directory prefix match
for entries ending in `/` and a `<entry>/**` match.

## Env var bypass

`HARNESS_DISABLE_SCOPE_LOCK=1` skips scope lock only; earlier rules and the
final execution-assignment check still apply. The gate reads the variable on every call and never clears it, so it
applies to every write while set in the runtime's environment (the environment
the runtime gives hook processes).

Each bypassed write overwrites `<wbs_dir>/audit/scope-lock-bypass.flag` with
the latest path. The next scope-lock evaluation without the variable deletes
that flag, so it is a last-bypass marker, not a durable audit record.

## Migration guidance

Canonical WBS roots without PROGRESS.md have no scope lock.
Retired Task records are migration inputs only and never grant execution
authority. Legacy verbose/prose files
remain best-effort readable and are not bulk migrated. New files are written by
the develop coordinator at Phase 3.1 using the seven-key canonical shape.

## Pattern entries

| Pattern | Discovered | Source |
|---------|------------|--------|
| scope-lock-gate | 2026-04-17 | TASK__gstack-ideas-adoption |
| scope-lock-docs-match-gate | 2026-09-28 | TASK__prewrite-gate-hardening |
