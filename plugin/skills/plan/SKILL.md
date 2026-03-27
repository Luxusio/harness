---
name: plan
description: Create or refresh a task-local PLAN.md as a contract before implementation.
argument-hint: <task-slug>
context: fork
agent: Plan
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

Create a PLAN.md contract for this task.

Task slug from user: `$ARGUMENTS`

## Procedure

### 1. Load context
- Read root `CLAUDE.md` for registry and registered roots
- Read `doc/common/CLAUDE.md`
- Read relevant root CLAUDE.md files
- Scan existing REQ/OBS/INF notes related to this task

### 2. Clarify requirements
- Separate explicit user requirements from inferred assumptions
- If requirements are ambiguous, ask the user (max 3 questions)

### 3. Create TASK_STATE.yaml
Create `.claude/harness/tasks/TASK__$ARGUMENTS/TASK_STATE.yaml`:

```yaml
status: created
mutates_repo: true
qa_required: true
qa_mode: browser-first
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
needs_env: []
updated: <date>
```

### 4. Write PLAN.md
Create `.claude/harness/tasks/TASK__$ARGUMENTS/PLAN.md` with:

```markdown
# Plan: <task title>
created: <date>
mutates_repo: true
qa_mode: browser-first

touched_roots: [<list of doc roots affected>]

## Scope in
<what this task will do>

## Scope out
<what this task will NOT do>

## Requirements (REQ)
<explicit user requirements — reference existing REQ notes if applicable>

## Assumptions (INF)
<inferred assumptions — clearly marked as unverified>

## User-visible outcomes
- <outcome 1>
- <outcome 2>

## Acceptance criteria
- [ ] <specific, testable criterion 1>
- [ ] <specific, testable criterion 2>

## Verification contract
- commands: <exact commands to run>
- routes: <endpoints or pages to check>
- persistence checks: <DB queries or file checks>
- expected outputs: <what success looks like>

## Persistence
- TASK_STATE updates: <state transitions planned>
- HANDOFF updates: <handoff notes planned>

## Required doc sync
- notes to add/update/supersede: <list>
- root indexes to refresh: <list>

## Risks / rollback / hard fail conditions
<what could go wrong, how to undo, when to abort>
```

### 5. Create REQUEST.md
Also create `.claude/harness/tasks/TASK__$ARGUMENTS/REQUEST.md` with the original user request text.

### 6. Initialize HANDOFF.md
Create `.claude/harness/tasks/TASK__$ARGUMENTS/HANDOFF.md` with initial handoff stub.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Every assumption must be marked as INF
- Verification contract must have concrete commands, not just "test manually"
- Risks section must name at least one rollback path
- QA mode must be explicit
- Persistence and docs sync steps are required for repo-mutating work
- Hard fail conditions must be specified
