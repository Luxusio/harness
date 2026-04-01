# harness runtime rules

This repository uses a **CLI-first harness**.

The goal is simple: the model should spend tokens on the task, not on re-deriving workflow policy. Runtime control comes from `hctl`, task-local artifacts, and hook gates.

## 1. Classify first

Use the smallest lane that fits the request.

- **answer**: explain, summarize, or advise. No task folder.
- **investigate**: inspect, reproduce, diagnose, or prepare findings. Task required.
- **repo-mutating lanes** (`build`, `debug`, `verify`, `refactor`, `docs-sync`): task required.

If the request will change files or produce structured findings, do not stay in answer mode.

## 2. Runtime control comes from `hctl`

For every tasked request:

```bash
python3 plugin/scripts/hctl.py start --task-dir <task-dir>
python3 plugin/scripts/hctl.py context --task-dir <task-dir> --json
```

Treat `hctl context` as the **canonical task pack** for:

Write/Edit/MultiEdit on normal repo files is hook-blocked unless the current task has `hctl start`, a recorded `hctl context --json` read, `PLAN.md`, and `plan_verdict: PASS`.

- `risk_level`
- `qa_required`
- `doc_sync_required`
- `browser_required`
- `workflow_locked`
- `maintenance_task`
- `compat.execution_mode`
- `compat.orchestration_mode`
- `must_read`
- `next_action`

Do **not** re-derive these from long prose docs.
Do **not** read global harness docs again unless the task is explicitly changing the harness.

## 3. Read narrowly

At runtime, prefer this order:

1. `hctl context --json`
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

```bash
hctl start
hctl context
# plan / implement / evaluate
hctl update --from-git-diff
hctl verify
hctl close
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
- if the request is broad and under-specified, use the plan skill to narrow it before implementation

## 8. Verification rule

`hctl verify` is the normal verification entry point.
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

- sync changed paths with `hctl update --from-git-diff`
- ensure required critics have written PASS in task state
- ensure blocking complaints or pending directives are resolved
- use `hctl close`

If `hctl close` blocks, fix the stated gate instead of narrating around it.
