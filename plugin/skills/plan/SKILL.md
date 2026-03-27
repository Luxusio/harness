---
name: plan
description: Create or refresh a task contract scaled to request size — PLAN.md for small tasks, full spec hierarchy for large/ambiguous work.
argument-hint: <task-slug>
context: fork
agent: Plan
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

Create a task contract for this request, scaled to its size and ambiguity.

Task slug from user: `$ARGUMENTS`

## Procedure

### 1. Load context
- Read root `CLAUDE.md` for registry and registered roots
- Read `doc/common/CLAUDE.md`
- Read relevant root CLAUDE.md files
- Scan existing REQ/OBS/INF notes related to this task
- Check for existing product specs, design docs, architecture docs

### 2. Assess planner depth

Evaluate the request along three axes:

| Axis | Low (→ PLAN.md only) | Medium (→ detailed PLAN.md) | High (→ spec hierarchy) |
|------|----------------------|----------------------------|------------------------|
| **Size** | 1-2 files | 3-10 files | 10+ files |
| **Ambiguity** | Clear target, specific verb | Some unknowns, needs clarification | Vague verbs, no specific target |
| **Impact** | Single domain | 2 domains | 3+ domains, cross-cutting |

**Depth decision:**
- All Low → `PLAN.md` only (small contract)
- Any Medium → `PLAN.md` with detailed acceptance criteria, risks, rollback
- Any High → full spec hierarchy

Record the depth decision and reasoning.

### 3. Clarify requirements
- Separate explicit user requirements from inferred assumptions
- If requirements are ambiguous, ask the user (max 3 questions for small, max 5 for large)
- For spec-depth tasks: ask about product goals, constraints, and non-negotiables

### 4. Create TASK_STATE.yaml
Create `.claude/harness/tasks/TASK__$ARGUMENTS/TASK_STATE.yaml`:

```yaml
task_id: TASK__$ARGUMENTS
run_id: <generated>
lane: <selected lane>
lane_rationale: <why this lane>
planner_depth: <small | medium | large>
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

### 5a. Small/Medium: Write PLAN.md

Create `.claude/harness/tasks/TASK__$ARGUMENTS/PLAN.md`:

```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
mutates_repo: true
qa_mode: browser-first
planner_depth: <small | medium>

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

### 5b. Large: Write spec hierarchy

Create the spec hierarchy under `.claude/harness/tasks/TASK__$ARGUMENTS/`:

**`01_product_spec.md`** — What and why:
```markdown
# Product Spec: <title>
created: <date>
task_id: TASK__$ARGUMENTS

## Problem statement
<what problem this solves>

## User stories
- As a <role>, I want <goal> so that <benefit>

## Requirements (REQ)
<explicit user requirements>

## Non-goals
<what this explicitly does NOT address>

## Success criteria
<how we know this succeeded — measurable>

## Constraints
<budget, timeline, compatibility, compliance>
```

**`02_design_language.md`** (when UI/UX is involved):
```markdown
# Design Language: <title>
created: <date>
task_id: TASK__$ARGUMENTS

## Visual direction
<design principles, references>

## Component inventory
<new/modified components>

## Interaction patterns
<key flows, states, transitions>

## Accessibility requirements
<WCAG level, screen reader, keyboard nav>
```

**`03_architecture.md`**:
```markdown
# Architecture: <title>
created: <date>
task_id: TASK__$ARGUMENTS

## System context
<where this fits in the overall system>

## Key decisions
- <decision 1>: <chosen approach> because <rationale>

## Component design
<modules, interfaces, data flow>

## Data model changes
<schema changes, migrations>

## Integration points
<APIs, events, external services>

## Risks and mitigations
<technical risks, rollback strategies>
```

**`exec-plans/`** — Ordered execution steps:
```markdown
# Exec Plan: Phase N — <title>
task_id: TASK__$ARGUMENTS
depends_on: [<previous phases>]

## Scope
<what this phase delivers>

## Steps
1. <step>
2. <step>

## Acceptance criteria
- [ ] <criterion>

## Verification
<commands, checks>
```

Also create a `PLAN.md` that references the spec hierarchy:
```markdown
# Plan: <task title>
created: <date>
task_id: TASK__$ARGUMENTS
planner_depth: large
spec_hierarchy:
  - 01_product_spec.md
  - 02_design_language.md (if applicable)
  - 03_architecture.md
  - exec-plans/

<summary of the overall plan, referencing spec docs>
```

### 6. Create REQUEST.md
Create `.claude/harness/tasks/TASK__$ARGUMENTS/REQUEST.md` with the original user request text.

### 7. Initialize HANDOFF.md
Create `.claude/harness/tasks/TASK__$ARGUMENTS/HANDOFF.md` with initial handoff stub.

## Guardrails

- Every acceptance criterion must be testable (no "works correctly")
- Every assumption must be marked as INF
- Verification contract must have concrete commands, not just "test manually"
- Risks section must name at least one rollback path
- QA mode must be explicit
- Persistence and docs sync steps are required for repo-mutating work
- Hard fail conditions must be specified
- Planner depth must be recorded and justified
- For large tasks, spec hierarchy must exist before PLAN.md references it
