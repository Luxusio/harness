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
- **Auto-fix:** Close clearly abandoned tasks (no activity, no open work)

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
- Close clearly dead tasks

### 3. Report
- **Fixed**: what was auto-repaired
- **Flagged**: issues that need human or writer attention
- **Stats**: note count by type, stale count, task count by status

## Rules

- Auto-fix only for mechanical issues (indexes, links, dead tasks)
- Content changes (contradictions, merges, archives) need writer + critic-document
- Do not create maintenance queues or logs — just fix and report
- Keep it fast — scan, fix, report
