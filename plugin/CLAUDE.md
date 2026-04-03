# harness runtime rules

This repository uses an **MCP-first harness** for model-facing control.

The goal is simple: the model should spend tokens on the task, not on brittle shell assembly. Runtime control comes from MCP tools, task-local artifacts, and hook gates. The CLI remains as backend/manual fallback.

## 1. Classify first

Use the smallest lane that fits the request.

- **answer**: explain, summarize, or advise. No task folder.
- **investigate**: inspect, reproduce, diagnose, or prepare findings. Task required.
- **repo-mutating lanes** (`build`, `debug`, `verify`, `refactor`, `docs-sync`): task required.

If the request will change files or produce structured findings, do not stay in answer mode.

## 2. Runtime control comes from harness MCP tools

For every tasked request, use:

- `mcp__plugin_harness_harness__task_start`
- `mcp__plugin_harness_harness__task_context`

Treat `task_context` as the **canonical task pack** for:

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
Do **not** read global harness docs again unless the task is explicitly changing the harness.

## 3. Read narrowly

At runtime, prefer this order:

1. `mcp__plugin_harness_harness__task_context`
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

Do not write another role’s artifact directly.

For `orchestration_mode: team`, source writes are additionally constrained by `TEAM_PLAN.md`: only declared owned writable paths may be mutated, and shared read-only paths stay read-only. Set `HARNESS_TEAM_WORKER` (or use a worker-suffixed `CLAUDE_AGENT_NAME`) when you want the harness to personalize context and recovery for a specific worker.
Once `TEAM_PLAN.md` is ownership-complete, use `mcp__plugin_harness_harness__team_bootstrap` (or `python3 plugin/scripts/hctl.py team-bootstrap --task-dir ... --write-files`) to generate `team/bootstrap/*` worker briefs plus role-scoped env snippets before fan-out. `task_context` also accepts `team_worker` / `agent_name` overrides when you need a contributor- or reviewer-specific task pack without mutating the parent shell env.
After bootstrap, prefer `mcp__plugin_harness_harness__team_dispatch` (or `python3 plugin/scripts/hctl.py team-dispatch --task-dir ... --write-files`) so the lead works from a frozen launch pack: provider prompt, provider launcher, per-phase worker prompts, and `run-*.sh` helpers. That pack now includes explicit lead synthesis / handoff-refresh helpers in addition to implementers and docs/runtime roles. Then use `mcp__plugin_harness_harness__team_launch` (or `python3 plugin/scripts/hctl.py team-launch --task-dir ... --write-files`) as the default fan-out entrypoint — it auto-refreshes stale bootstrap/dispatch artifacts, writes `team/bootstrap/provider/launch.json`, surfaces the native lead prompt when the provider is interactive, and can auto-fall back to the implementer dispatcher for `--execute`. When a single worker or close-phase needs to be resumed, use `mcp__plugin_harness_harness__team_relaunch` (or `python3 plugin/scripts/hctl.py team-relaunch --task-dir ... --write-files`) to pick the current best worker/phase pair from the frozen dispatch pack before optionally spawning it.
Before lead synthesis and close, each contributor worker should leave `team/worker-<name>.md` summarizing completed work, owned paths handled, verification, and residual risks. When `TEAM_PLAN.md` names an explicit `lead` / `integrator`, that synthesis owner should write `TEAM_SYNTHESIS.md`, own the final runtime verification pass, then hand off to writer / critic-document for the documentation pass before refreshing `HANDOFF.md`; non-lead workers should not touch those artifacts.
When team-owned protected artifacts are written through `mcp__plugin_harness_harness__write_*`, pass the current worker explicitly (for example `team_worker: lead`) or export `HARNESS_TEAM_WORKER`; the harness now enforces team ownership inside `write_artifact.py` as well, so ambiguous doc/runtime/handoff writes will be rejected instead of silently bypassing the team gate.

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
task_context
# plan / implement / evaluate
task_update_from_git_diff
task_verify
task_close
```

Practical meaning:

1. compile routing once
2. read the compact task pack
3. do the smallest coherent work unit
4. sync changed files from git
5. run verification
6. close only after required critics and gates pass

## 7. Plan-first rule

For repo-mutating tasks:

- do not mutate source before `PLAN.md` exists and critic-plan PASS is recorded
- if the task is unplanned, stop source work and repair the plan first
- if `planning_mode: broad-build`, let the plan skill write `01_product_spec.md`, `02_design_language.md`, and `03_architecture.md` before `PLAN.md`
- otherwise, if the request is broad and under-specified, use the plan skill to narrow it before implementation

## 8. Verification rule

`mcp__plugin_harness_harness__task_verify` is the normal task verification entry point. Use `mcp__plugin_harness_harness__verify_run` for repo-level verify.py modes.
Use browser-first verification only when the task pack or manifest requires it.
Do not claim success from static inspection alone when runtime verification is required.

## 9. Documentation rule

Only produce `DOC_SYNC.md` or durable notes when:

- docs actually changed, or
- `doc_sync_required: true`, or
- the user introduced a durable rule / requirement / invariant worth storing

Do not create documentation artifacts just because the harness exists.

## 10. Degraded capability disclosure

If the normal delegated workflow is unavailable and the task still needs repo mutation:

- say that guarantees are degraded
- ask for approval before using a collapsed path
- do not present collapsed self-checks as full harness-compliant evaluation

## 11. Finish cleanly

Before closing a task:

- sync changed paths with `mcp__plugin_harness_harness__task_update_from_git_diff`
- ensure required critics have written PASS in task state
- ensure blocking complaints or pending directives are resolved
- use `mcp__plugin_harness_harness__task_close`

If `task_close` blocks, fix the stated gate instead of narrating around it.
