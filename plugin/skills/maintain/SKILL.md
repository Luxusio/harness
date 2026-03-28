---
name: maintain
description: Optional cleanup tool — finds stale tasks, broken links, and obvious entropy.
argument-hint: [optional focus area]
context: fork
agent: Explore
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit
---

Optional cleanup tool for the harness task system.

Optional focus from user: `$ARGUMENTS`

## What to check

1. **Stale open tasks** — Task folders with non-closed status that appear abandoned
2. **Blocked tasks** — Tasks with `status: blocked_env` that may now be unblocked
3. **Broken links** — CLAUDE.md indexes pointing to files that don't exist
4. **Obvious drift** — Documentation that clearly contradicts current code

## Procedure

### 1. Scan task health
- Check `.claude/harness/tasks/` for open/blocked tasks
- Report their status and age

### 2. Scan doc health (if docs exist)
- Verify CLAUDE.md indexes match files on disk
- Check for obvious contradictions

### 3. Report
- **Found**: issues by type
- **Suggested**: actions to take

## Rules

- This is an optional tool, not a mandatory loop phase
- Only report issues that are clearly actionable
- Do not create maintenance queues or compaction logs
- Do not police freshness metadata or normalize note headers
