---
name: plan
description: Create a task contract — PLAN.md with scope, acceptance criteria, verification, and rollback.
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
- Read `.claude/harness/manifest.yaml` if it exists
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
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
blockers: []
updated: <ISO 8601>
```

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

## Acceptance criteria
- [ ] <specific, testable criterion 1>
- [ ] <specific, testable criterion 2>

## Verification
- commands: <exact commands to run>
- expected outputs: <what success looks like>

## Hard fail conditions
<conditions that would mean this task has failed>

## Risks / rollback
<what could go wrong, how to undo>
```

If PLAN.md alone is genuinely insufficient (10+ files, cross-domain, high ambiguity), add ONE supporting document — SPEC.md, DESIGN.md, or TASKS.md. Do not create a hierarchy by default.

### 6. Initialize HANDOFF.md

Create `.claude/harness/tasks/TASK__$ARGUMENTS/HANDOFF.md` with initial stub.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Verification must have concrete commands or endpoints
- Hard fail conditions must be explicit
- Risks section must name at least one rollback path for repo-mutating work
- Do not create spec hierarchies, QA mode declarations, or doc sync plans by default
