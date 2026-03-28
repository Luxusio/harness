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
- Flag `blocked_env` tasks that may now be unblocked
- **Auto-fix:** Mark clearly abandoned tasks as `status: stale` (NOT closed — close is a semantic judgment)
- **Never auto-close.** Stale or archived are safe mechanical states. Close requires intent.

### 2. Index health (if doc/ exists)
- Verify each CLAUDE.md index matches actual files on disk
- **Auto-fix:** Add missing notes to index, remove entries for deleted files

### 3. Note health (if notes exist)
- Find notes with stale `last_verified_at`
- Find orphaned notes (on disk but not in any index)
- Find broken supersede chains (`superseded_by` pointing to non-existent file)
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
