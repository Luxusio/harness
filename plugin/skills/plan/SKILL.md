---
name: plan
description: Create a task contract — PLAN.md with scope, acceptance criteria, verification contract, doc sync, and rollback.
argument-hint: <task-slug>
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context
---

Create or repair the task contract for `TASK__$ARGUMENTS`.

The skill is **MCP-first**: let the harness MCP tools compile routing, then write only the minimum task-local artifacts needed for implementation and evaluation.

## Procedure

### 0. Open the plan session

Before guarded reads or `PLAN.md` writes, open `PLAN_SESSION.json` in the task directory and set:

- `state: open`
- `phase: context`
- `source: plan-skill`

Also set `plan_session_state: context_open` in `TASK_STATE.yaml`.

### 1. Compile routing once

Run:

- `mcp__plugin_harness_harness__task_start { task_dir: "doc/harness/tasks/TASK__$ARGUMENTS" }`
- `mcp__plugin_harness_harness__task_context { task_dir: "doc/harness/tasks/TASK__$ARGUMENTS" }` only when the task pack needs to be refreshed

Use the returned task pack as the source of truth for:

- `risk_level`
- `qa_required`
- `doc_sync_required`
- `browser_required`
- `workflow_locked`
- `maintenance_task`
- `planning_mode`
- `compat.execution_mode`
- `compat.orchestration_mode`
- `must_read`
- `next_action`

Do **not** re-derive mode from long prose docs.

### 2. Read narrowly

Read only what is needed to write a valid contract:

1. root `CLAUDE.md`
2. `doc/harness/manifest.yaml` if it exists
3. existing task-local artifacts (`TASK_STATE.yaml`, `REQUEST.md`, `PLAN.md`, `CHECKS.yaml`, `HANDOFF.md`)
4. only the source files directly implicated by the request

Avoid broad scans of workflow-control files during normal planning.

### 3. Clarify only when needed

If ambiguity blocks a safe plan, ask at most 3 concise questions.
If a workable narrow contract is possible without asking, prefer writing the plan.

### 4. Write or refresh `REQUEST.md`

Capture the user request in their words.
Keep it short and faithful.

### 5. Broad-build narrowing when selected

If `task_context.planning_mode == broad-build`, create these brief high-level artifacts before `PLAN.md`:

- `01_product_spec.md`
- `02_design_language.md`
- `03_architecture.md`

Use them only to narrow the task. Do not turn them into long implementation novels.

If `planning_mode` is `standard`, skip this trio unless the user explicitly asked for that planning depth.

### 6. Transition the session to write mode

Set:

- `PLAN_SESSION.json.phase: write`
- `TASK_STATE.yaml.plan_session_state: write_open`

### 7. Write `PLAN.md`

Keep the contract concise and executable.

Required sections:

- objective
- scope in
- scope out (when omission matters)
- target files or surfaces
- acceptance checks with stable IDs
- verification commands / flows
- doc-sync expectation
- risk / rollback when `risk_level` is high
- next implementation step

Do not fill `PLAN.md` with repeated harness policy.

### 8. Write or refresh `CHECKS.yaml`

Create stable criterion IDs such as `AC-001`, `AC-002`, ...
Each criterion must be short, observable, and testable.
Do not dump long prose into titles.

### 9. Close the plan session cleanly

After `PLAN.md` is written:

- create `PLAN.meta.json` with `author_role: plan-skill`
- set `plan_session_state: closed`
- close `PLAN_SESSION.json`
- leave the task ready for critic-plan

## Success condition

The plan is successful when a developer and an evaluator can both answer these questions without extra repo archaeology:

- what are we changing?
- how do we know it works?
- what should not accidentally expand?
- how will the next role verify it?
