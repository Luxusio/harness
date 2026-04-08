# harness runtime rules

This repository uses an **MCP-first harness**. Spend tokens on the task, not brittle shell assembly. Runtime control comes from MCP tools, task-local artifacts, and hook gates; the CLI is fallback only.

## 1. Classify first

Use the smallest lane that fits the request.

- **answer**: explain, summarize, or advise. No task folder.
- **investigate**: inspect, reproduce, diagnose, or prepare findings. Task required.
- **repo-mutating lanes** (`build`, `debug`, `verify`, `refactor`, `docs-sync`): task required. Keep only one repo-mutating task as the current write focus at a time; new overlapping mutating requests default to a separate task plus queued follow-up until the user switches focus.

If the request will change files or produce structured findings, do not stay in answer mode.

## 2. Runtime control comes from harness MCP tools

For every new or resumed tasked request, start with:

- `mcp__plugin_harness_harness__task_start`

Use `mcp__plugin_harness_harness__task_context` only when you need a refresh, a personalized worker view, or the hook-provided summary looks stale.

If a worker or critic ran out-of-band and the stop hook missed provenance, repair the durable count with `mcp__plugin_harness_harness__record_agent_run` before the next close attempt. `task_context` and `task_close` also reconcile missing zero-count provenance from durable protected artifacts when possible.

Treat the task pack returned by `task_start` or `task_context` as the **canonical task pack** for:

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

Do **not** re-derive these from long prose docs.

## 3. Read narrowly

At runtime, prefer this order:

1. `task_start` on new/resume, or `task_context` only for refresh/personalization
2. task-local files listed in `must_read`
3. only the source files directly needed for the current step

Avoid broad exploratory reads of `plugin/`, `hooks`, `skills`, or other workflow-control surfaces during normal product work.

## 4. Artifact ownership is strict

Protected artifacts have one owner each.

- `PLAN.md` → `Skill(harness:plan)`
- source files + `HANDOFF.md` → developer
- `DOC_SYNC.md` + durable notes → writer
- `CRITIC__plan.md` → critic-plan
- `CRITIC__runtime.md` → critic-runtime
- `CRITIC__document.md` → critic-document
- `CRITIC__intent.md` → critic-intent

Do not write another role’s artifact directly.

For `orchestration_mode: team`, only paths owned in `TEAM_PLAN.md` may be mutated; shared paths stay read-only. Set `HARNESS_TEAM_WORKER` (or a worker-suffixed `CLAUDE_AGENT_NAME`) when you need worker-specific context or recovery.
Use `team_bootstrap` to generate worker briefs and role env snippets, `team_dispatch` to freeze the lead launch pack, `team_launch` as the default fan-out entrypoint, and `team_relaunch` to resume a single worker or close-phase from that frozen pack. `task_context` also accepts `team_worker` / `agent_name` overrides for a personalized task pack.
Before lead synthesis and close, each contributor leaves `team/worker-<name>.md` with work completed, paths handled, verification, and residual risks. If `TEAM_PLAN.md` names a `lead` / `integrator`, that owner writes `TEAM_SYNTHESIS.md`, owns the final runtime verification pass, and refreshes `HANDOFF.md`; non-lead workers should not touch those artifacts.
When team-owned protected artifacts are written through `mcp__plugin_harness_harness__write_*`, pass `team_worker` explicitly or export `HARNESS_TEAM_WORKER`; ambiguous doc/runtime/handoff writes will be rejected.

## Agent delegation convention

When delegating to harness agents, tag the agent name with `TASK__<id>:<role>` so the stop hook auto-records provenance:

```
Agent(name="TASK__harness-zero-friction:developer", ...)
Agent(name="TASK__harness-zero-friction:critic-runtime", ...)
```

The stop hook (`subagent_stop_gate.py`) parses this pattern and calls `record_agent_run` automatically. You do NOT need to call `mcp__plugin_harness_harness__record_agent_run` manually when using this convention.

## 5. Workflow surface is locked by default

Normal tasks must not modify the harness control plane:

- `plugin/CLAUDE.md`
- `plugin/agents/*`
- `plugin/skills/*`
- `plugin/scripts/*`
- `plugin/hooks/hooks.json`
- `doc/harness/manifest.yaml`
- setup templates and other workflow-control files

Only tasks with `maintenance_task: true` may change those files.

## 6. Default loop

Normal loop:

```text
task_start
# read must_read / do the next coherent work unit
task_verify
task_close
```

Meaning: compile routing once, resolve focus, do the smallest coherent work unit, then let `task_verify` and `task_close` auto-sync and enforce the gates. Call `task_context` only when the task pack needs refresh or personalization.

## 7. Plan-first rule

For repo-mutating tasks:

- do not mutate source before `PLAN.md` exists and critic-plan PASS is recorded
- short approvals such as `ㅇㅇ ㄱ` or `go ahead` only approve the last explicit transition you proposed; they never skip task creation, planning, or critic gates
- if the task is unplanned, stop source work and repair the plan first
- if `planning_mode: broad-build`, let the plan skill write `01_product_spec.md`, `02_design_language.md`, and `03_architecture.md` before `PLAN.md`
- otherwise, if the request is broad and under-specified, use the plan skill to narrow it before implementation

## 8. Verification rule

`mcp__plugin_harness_harness__task_verify` is the normal task verification entry point; it auto-syncs changed paths from git diff before running verification. Use `mcp__plugin_harness_harness__verify_run` for repo-level verify.py modes.
Use browser-first verification only when the task pack or manifest requires it.
Do not claim success from static inspection alone when runtime verification is required.

## 9. Documentation rule

Only produce `DOC_SYNC.md` or durable notes when:

- docs actually changed, or
- `doc_sync_required: true`, or
- the user introduced a durable rule / requirement / invariant worth storing

Do not create documentation artifacts just because the harness exists.

### Doc delegation

Before delegating to `harness:writer`:

1. Read `doc/CLAUDE.md` (or the project's equivalent doc registry) to identify registered doc roots.
2. Include the target doc root path in the writer delegation brief (e.g. `doc/common/`).
3. Distinguish in the brief: durable notes (REQ/OBS/INF) go to the registered root; `DOC_SYNC.md` goes to the task dir.

Skipping step 1 causes writer to default to task dir, producing notes that will not be committed.

## 10. Degraded capability disclosure

If the normal delegated workflow is unavailable and the task still needs repo mutation:

- say that guarantees are degraded
- ask for approval before using a collapsed path
- do not present collapsed self-checks as full harness-compliant evaluation

## 11. Finish cleanly

Before closing a task:

- ensure required critics have written PASS in task state
- ensure blocking complaints or pending directives are resolved
- use `mcp__plugin_harness_harness__task_close` (it auto-syncs changed paths first)
- use `mcp__plugin_harness_harness__task_update_from_git_diff` only as a manual or fallback sync step

If `task_close` blocks, fix the stated gate instead of narrating around it.
