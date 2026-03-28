---
name: plan
description: Create a task contract — PLAN.md with scope, acceptance criteria, verification contract, doc sync, and rollback.
argument-hint: <task-slug>
context: fork
agent: Plan
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

Create a task contract for this request.

Task slug from user: `$ARGUMENTS`

## Procedure

### 1. Load context
- Read root `CLAUDE.md`
- Read `.claude/harness/manifest.yaml` if it exists (check `browser.enabled`, `qa.default_mode`)
- Scan relevant existing docs if any
- Understand what the user is asking for

### 2. Clarify requirements
- Separate explicit user requirements from inferred assumptions
- If requirements are ambiguous, ask the user (max 3 questions)

### 3. Create TASK_STATE.yaml

Create `.claude/harness/tasks/TASK__$ARGUMENTS/TASK_STATE.yaml`:

```yaml
task_id: TASK__$ARGUMENTS
status: planned
lane: <selected sub-lane: build|debug|verify|refactor|docs-sync|investigate>
mutates_repo: <true|false>
qa_required: <true|false>
qa_mode: <auto|tests|smoke|browser-first>
browser_required: <true|false>
doc_sync_required: <true|false>
touched_paths: []
roots_touched: []
verification_targets: []
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
blockers: []
updated: <ISO 8601>
```

Set `browser_required: true` and default `qa_mode: browser-first` when `manifest.browser.enabled: true`.
Set `doc_sync_required: true` for all repo-mutating tasks.

### 4. Write REQUEST.md

Create `.claude/harness/tasks/TASK__$ARGUMENTS/REQUEST.md`:

```markdown
# Request: TASK__$ARGUMENTS
created: <date>

<capture the original user request in their words>
```

### 5. Write PLAN.md

Create `.claude/harness/tasks/TASK__$ARGUMENTS/PLAN.md`:

```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
mutates_repo: <true|false>

## Scope in
<what this task will do>

## Scope out
<what this task will NOT do>

## User-visible outcomes
<what changes from the user's perspective — observable behavior, not implementation details>

## Touched files / roots
- <file or directory path>
- <file or directory path>

## QA mode
<tests | smoke | browser-first>

## Acceptance criteria
- [ ] <specific, testable criterion 1>
- [ ] <specific, testable criterion 2>

## Verification contract
- commands: <exact commands to run>
- routes: <URLs or API endpoints to hit, or "n/a">
- persistence checks: <database or file state to verify, or "n/a">
- expected outputs: <what success looks like>

## Required doc sync
<which doc surfaces need updating, or "none">

## Hard fail conditions
<conditions that would mean this task has failed>

## Risks / rollback
<what could go wrong, how to undo>

## Open blockers
<known blockers before implementation, or "none">
```

If PLAN.md alone is genuinely insufficient (10+ files, cross-domain, high ambiguity), add ONE supporting document — SPEC.md, DESIGN.md, or TASKS.md. Do not create a hierarchy by default.

### 6. Initialize HANDOFF.md

Create `.claude/harness/tasks/TASK__$ARGUMENTS/HANDOFF.md` with initial stub.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Verification contract must have concrete executable commands or endpoints — prose alone is not sufficient
- Hard fail conditions must be explicit
- Risks section must name at least one rollback path for repo-mutating work
- For browser-first projects: QA mode must be `browser-first`, not `smoke` or `tests` alone
- All mandatory PLAN.md fields must be present (critic-plan will FAIL plans missing required fields)
