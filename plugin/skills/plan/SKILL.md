---
name: plan
description: Create or refresh a task-local PLAN.md before implementation.
argument-hint: <task-slug>
context: fork
agent: Plan
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

Create a PLAN.md for this task.

Task slug from user: `$ARGUMENTS`

## Procedure

1. **Load context**
   - Read `doc/CLAUDE.md` for root registry
   - Read relevant root CLAUDE.md files
   - Scan existing REQ/OBS/INF notes related to this task

2. **Clarify requirements**
   - Separate explicit user requirements from inferred assumptions
   - If requirements are ambiguous, ask the user (max 3 questions)

3. **Write PLAN.md**
   Create `.claude/harness/tasks/TASK__$ARGUMENTS/PLAN.md` with:

   ```markdown
   # Plan: <task title>
   created: <date>
   touched_roots: [<list of doc roots affected>]

   ## Requirements (REQ)
   <explicit user requirements — reference existing REQ notes if applicable>

   ## Assumptions (INF)
   <inferred assumptions — clearly marked as unverified>

   ## Acceptance criteria
   - [ ] <specific, testable criterion 1>
   - [ ] <specific, testable criterion 2>

   ## Verification plan
   <concrete commands, endpoints, or checks to prove each criterion>

   ## Touched files
   <files/directories that will be modified>

   ## Risks and rollback
   <what could go wrong and how to undo>

   ## Required doc updates
   <REQ/OBS/INF notes that need creation or update>
   ```

4. **Create REQUEST.md**
   Also create `.claude/harness/tasks/TASK__$ARGUMENTS/REQUEST.md` with the original user request text.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Every assumption must be marked as INF
- Verification plan must have concrete commands, not just "test manually"
- Risks section must name at least one rollback path
