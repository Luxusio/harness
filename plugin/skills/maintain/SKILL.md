---
name: maintain
description: Doc and task cleanup tool — finds and fixes stale tasks, broken links, index drift, and obvious entropy.
argument-hint: [optional focus area]
context: fork
agent: Explore
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit
---

Cleanup tool for harness docs and tasks.

Optional focus from user: `$ARGUMENTS`

## What to check and fix

### 1. Task health
- Find open/blocked tasks in `.claude/harness/tasks/`
- Flag abandoned tasks (non-closed, old `updated` date)
- **Stale task detection**: Mark tasks as stale if `updated` > 7 days ago and status is not `closed` or `archived`
- Flag `blocked_env` tasks that may now be unblocked
- **Task artifact integrity**: Find tasks with inconsistent state (e.g., `status: closed` but missing required artifacts like PLAN.md, HANDOFF.md, or CRITIC__runtime.md PASS for repo-mutating tasks)
- **DOC_SYNC audit**: Find repo-mutating tasks (`mutates_repo: true`) that are missing `DOC_SYNC.md`
- **Auto-fix:** Mark clearly abandoned tasks as `status: stale` (NOT closed — close is a semantic judgment)
- **Never auto-close.** Stale or archived are safe mechanical states. Close requires intent.

### 2. Index health (if doc/ exists)
- Verify each CLAUDE.md index matches actual files on disk
- **Broken index repair**: Rebuild CLAUDE.md indexes from actual files on disk when entries are missing or point to deleted files
- **Auto-fix:** Add missing notes to index, remove entries for deleted files

### 3. Note health (if notes exist)
- Find notes with stale `last_verified_at`
- **Orphan note detection**: Find notes in `doc/common/` that exist on disk but are not referenced in any CLAUDE.md index
- **Broken supersede chain**: Find notes with `superseded_by` pointing to non-existent notes
- **Auto-fix:** Add orphaned notes to index, fix broken supersede links where target is obvious

### 4. Obvious drift
- Documentation that clearly contradicts current code (check key claims against reality)
- **Flag only** — do not auto-fix content contradictions (needs writer + critic-document)

## Procedure

### 1. Scan
Run all checks above. Collect findings.

### 2. Auto-fix
Apply safe mechanical fixes immediately:
- Rebuild broken indexes
- Add orphaned notes to correct root index
- Fix broken supersede links
- Mark abandoned tasks as `status: stale` with `updated: <now>`

### 3. Report
- **Fixed**: what was auto-repaired
- **Flagged**: issues that need human or writer attention
- **Stats**: note count by type, stale count, task count by status

## Rules

- Auto-fix only for mechanical issues (indexes, links, stale marking)
- Content changes (contradictions, merges, archives) need writer + critic-document
- **Never auto-close tasks** — use `stale` or `archived` for mechanical states
- Do not create maintenance queues or logs — just fix and report
- Keep it fast — scan, fix, report

## Maintain-lite (automatic at session end)

Maintain-lite runs automatically via the `session-end-sync.sh` hook. It performs read-only entropy detection — no writes, no auto-fixes. Results appear in the session-end summary.

### What maintain-lite detects

| Check | How |
|-------|-----|
| **Stale tasks** | Tasks with `updated` > 7 days ago and status not `closed`/`archived`/`stale` |
| **Orphan notes** | Files in `doc/common/` that are not referenced in any CLAUDE.md index |
| **Broken supersede chains** | Notes with `superseded_by:` pointing to a file that does not exist on disk |
| **Dead artifacts** | `CRITIC__*.md` files in closed task folders (status: `closed`) |

### What maintain-lite does NOT do

- Never auto-closes tasks
- Never deletes files
- Never modifies notes or indexes
- Never marks tasks stale (that is the full `maintain` skill's job)

### Entropy health score

The session-end summary includes a quick entropy health score:

```
entropy: LOW | MEDIUM | HIGH
```

Scoring:
- **LOW**: 0 stale tasks, 0 orphan notes, 0 broken chains, 0 dead artifacts
- **MEDIUM**: Any 1–3 issues across all categories combined
- **HIGH**: 4+ issues, or any broken supersede chain

The score is informational only. It does not block the session or gate any task.

### When to run full maintain

Run `/harness:maintain` when entropy is MEDIUM or HIGH, or when a session-end summary reports multiple stale tasks or orphan notes.
