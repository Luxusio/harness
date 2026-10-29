---
name: run
description: Orchestrate the public WBS open → plan → develop → QA → close lifecycle.
argument-hint: <task-slug-or-description>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__wbs
---

Orchestrate the full harness development cycle for a task.

> `PLAN.md` owns acceptance intent; existing WBS assertions own measured outcome checks.

Before entering this workflow, resolve the current project with the existing
Harness manifest guard. Run only in a project initialized by `harness:setup`
(`doc/harness/manifest.yaml`); a nested Git checkout needs its own setup.
Outside an initialized project, continue ordinary work without Harness tasks,
planning, routing reminders or automatic setup. Explicit setup remains available.

## Substantive review and QA

Await actual independent code review and conditional security review PASS before actual QA. Fix actual FAIL findings; publish actual BLOCKED_ENV with a concrete blocker and unblock condition. Direct independent finals are the workflow results; coordinator paraphrases and copied text cannot replace them. After actual QA PASS, run `wbs(action="verify")` and close only when current required WBS checks pass. Machine verdicts measure WBS checks and do not attest review or QA. Receipt files, collectors, naming bindings and recording availability play no role; do not rerun, repair or park work for missing recording.

Explicit user invocation or approval of this harness repo-mutating workflow
authorizes the subagents required by the workflow's verification and review
gates. Examples include "use harness", "run/continue/close the harness task",
native `/goal`, or clear approval to proceed with a harness task. This workflow
authorization does not apply to read-only answers or ordinary non-harness work.

When the runtime exposes explicit close tools such as `close_agent`, track each
spawned agent id and close it after the result is consumed, the agent completes
or fails, it is cancelled, or it is no longer needed. Before final response,
`wbs(action="close")`, or handoff, do not leave completed agents open unless the user
explicitly asked to keep a still-running agent alive. Completed agents can
continue to count toward the concurrency limit until closed.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, and validated tiered-learning promotion with an append-only raw ledger (runs after each task close).

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## One public WBS interface

Use only `wbs` MCP for this workflow. Names below identify internal owners,
not additional public tools: task_start → wbs(action="open"), task_context →
context, write_plan → plan, task_verify → verify, task_close → close,
task_blocked → block. Pass `wbs_id` using the known root or legacy task ID;
plan/check/dispatch share that same tree. check uses node_id; dispatch accepts
current execution ownership and parks the parent automatically. Preserve
workspace on every worktree call. No separate Goal WBS or child task queue.

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On failure, inspect actual state and follow Error Handling; repair authorized failures without repeating approval.

Plain repo-mutating requests are valid task intake. Do not require the user to
re-issue a clear feature, fix, refactor, behavior change, or durable process/doc
request as `/goal`. If no active task can be resolved, create or resume the
harness task yourself and continue through this flow.

### Native Goal continuation

Read the native objective with get_goal and open/resume the same WBS with
`wbs(action="open", objective="<objective>")`, or use the already-known root
wbs_id. Keep one coarse outcome tree and refine only work about to be done.
Use wbs context/next for measured progress and dependency-ready outcomes;
plan updates the same PLAN. For substantial independent work use wbs dispatch
with current execution ownership, then run the returned exact batch through
the existing batch skill. Leads develop, independently review, test and close
inside their own worktrees. The pool serializes integration and preserves
proof. Reopen the same root after pool completion, check integrated outcomes,
then run combined review/QA and close. Native Goal completion uses that root's
measured completion, without a second Goal ledger.

### Phase 0: Resume detection

Before creating a new task, check whether this session already has an active
harness task. If one exists, call `wbs(action="context")`
and resume rather than creating a duplicate.

Resume routing:
- Already closed with outstanding coordinator disposal → Phase 5's shared
  completion procedure; do not repeat close or review solely for cleanup.
- Current actual review/QA/UX FAIL → remediate the failed work in Phase 3;
  actual BLOCKED_ENV → preserve the real blocker and unblock condition.
  These results take precedence over a green machine runtime_verdict.
- Required actual results unrun or stale → restore ordered review/QA in Phase 3 or 4.
- PLAN.md missing → Phase 2 Plan.
- PLAN.md exists and runtime_verdict is not PASS → Phase 3 Develop/Verify.
- runtime_verdict is PASS and `missing_for_close` is empty, with current actual
  independent review and applicable QA/UX PASS → Phase 5 Close.
- `missing_for_close` names specific artifacts or AC blockers → fix that gate
  and then continue from the corresponding phase.

Only call `wbs(action="open")` when no active task can be resolved, or when the user
explicitly asks for a new task.

If only a native Goal is active, open/resume its one WBS root; never attach a second planning ledger.

### Phase 1: Start task

`wbs(action="open")` is also the only resume operation. On an existing valid open or
blocked task, omit `fresh_run` to preserve the same run and measured checks.
Use `fresh_run: true` only when deliberately starting a new generation and
starting a new generation; a closed task refuses the default call.

```
wbs { action: "open", wbs_id: "<ARGUMENTS>" }
```

Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

```
Skill("harness:plan", "<task_id>")
```

The plan skill conservatively selects the compact or full planning procedure
and writes PLAN.md. Compact planning may skip only plan-time Engineering
Review; implementation-time independent `review-code` remains mandatory. On
completion: PLAN.md exists in task_dir. If BLOCKED: stop and report.

### Phase 3: Develop

```
Skill("harness:develop", "<task_id>")
```

The develop skill reads PLAN.md, implements changes, runs scope drift detection, bisectable commits, verification gate, runtime QA subagents, and any needed durable-doc updates. On completion, actual independent review and QA passed and `wbs(action="verify")` reports PASS. If BLOCKED: preserve the unfinished blocker and unblock condition; ask only for a missing user decision or authorization.

Before entering develop, re-entering after QA/UX FAIL, verifying, or closing,
incorporate explicit user corrections from the conversation. Promote durable
rules directly into PLAN.md or the applicable project documentation; Harness
does not maintain a separate feedback sidecar.

### Phase 4: Verify recovery (only when develop returned before close)

Skip this phase when Phase 3 closed the task. Develop Phase 7 owns QA lens
selection and spawning, and develop Phase 6.6 owns review; this phase is a
recovery path for an interrupted or older develop flow, not a second QA pass.

First call `wbs(action="context")`. Inspect current actual independent finals before
machine checks. Reuse applicable actual review and QA/UX PASS results; rerun
only actually unrun, failed or stale substantive work. Recording never requires a rerun.

For a lens that must be re-run, follow `develop/SKILL.md` Phase 7 and
`develop/quality-audit-pipeline.md` — do not restate routing here. Use the
native subagent types (`harness:qa-cli`, `harness:qa-api`, `harness:qa-browser`,
`harness:qa-desktop`, and the matching `harness:ux-*`), and issue multiple
lenses in a single assistant message for independent parallel execution. Never
restate or relocate the verdict contract in a spawn prompt: the agent
definition owns the output contract.

Two selection rules are gates rather than routing detail, so they are stated
here and not delegated:

- You **MUST spawn qa-browser** when `manifest.qa.browser_qa_supported: true`
  AND the diff contains any frontend file (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss`
  or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments).
  Await its actual result before claiming completion.
- Spawn the applicable UX lens alongside QA for user-facing surfaces:
  `ux-browser` for browser UI, `ux-cli` for command/help/output/error changes,
  `ux-api` for route/schema/error/docs changes, `ux-desktop` for desktop GUI —
  each gated on `ux_review_supported: true` (or the matching
  `browser_qa_supported`/`desktop_qa_supported`). UX review does not replace QA:
  `qa-*` proves correctness, `ux-*` judges whether the experience is shippable.

Agents return PASS/FAIL/BLOCKED_ENV findings in their final response; do not ask
them to write critic artifacts.

Handle actual QA/UX results before `wbs(action="verify")`:
- **FAIL**: return to Phase 3, fix substantive findings and restore affected
  independent review PASS before rerunning actual QA/UX. A green machine
  verdict cannot clear an unresolved actual failure.
- **BLOCKED_ENV**: report the exact external blocker and executable unblock
  condition; use `wbs(action="block")` for unfinished work. Do not close or
  convert the blocker to PASS.
- **PASS**: only after every applicable actual lens passes, run `wbs(action="verify")`.

Its runtime verdict and `missing_for_close` measure current WBS checks,
blocked state and applicability, not review/QA attestation. PLAN.md remains the
acceptance document. Machine PASS permits Phase 5 only after actual PASS;
otherwise fix the named WBS check and reverify. Never label a machine check
failure as a QA verdict or offer an override of substantive failure.

**Persist QA failure patterns** after each retry cycle:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"qa-failure-pattern","source":"run-retry","key":"FAILURE_TYPE","insight":"QA failed: <reason>, workaround: <fix>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 4.5: Health reporting

Reuse an already reported health measurement only when it applies to the current
scope. Otherwise omit the health score from the completion report. Never invent
a zero score or report an unmeasured result as PASS.

Do not launch `health.py`, `test_command`, or component checks to obtain a score
at completion. Explicit manual health execution remains available; `--dry-run`
only lists planned checks and produces no measurement.

### Phase 5: Close

Read `${CLAUDE_PLUGIN_ROOT}/skills/run/worktree-completion.md`. Skip
`wbs(action="close")` when develop already closed the task; finish any outstanding
coordinator disposal under that procedure without repeating review solely for
cleanup. Otherwise close once after Phase 4 restores PASS, then complete the
procedure before claiming overall completion. Batch leads return to their
coordinator without integrating or removing their own worktree.

```
wbs { action: "close", wbs_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: run self-improvement pipeline (see `self-improvement.md`) before
emitting the completion report.

For harness-source changes, develop Phase 7.8 installs the verified payload
before this close attempt. Do not defer installation until after close. The installer checks trusted source/task ownership, current WBS applicability and stable payload bytes. A runtime update may require a new session to load the changed code; recording availability never blocks completion.

Within an open root, use `wbs(action="next")`, development and checks to
continue pending outcomes. Close only after all required outcomes, integration
and independent evidence pass. Then run self-improvement and learning promotion
before a separately authorized follow-up execution. Respect explicit stops,
budgets and agreed go/no-go boundaries.

For this harness plugin source repo, successful repo-mutating development is
not complete at task close. Phase 7.8 must already have run the verified
auto-install helper after the last source edit and fresh QA. After
post-close self-improvement has run, commit the completed diff
before the final response unless the user explicitly says not to. Include
the commit hash and pre-close conditional verified-delivery result in the completion report.

## Completion Report

Before writing DONE, assert:
- primary task is closed
- owned temporary worktree disposition is complete under the shared rule
- runtime_verdict is PASS or task is BLOCKED
- post-close self-improvement has run
- if this WBS belongs to a native Goal, that objective is done/blocked/stopped/budgeted
  or the next slice is already active/queued
- for this harness plugin source repo, the completed diff has been committed
  and verified delivery has confirmed synchronized runtime payloads, unless the user explicitly opted out

```
DONE

Task:    <task_id>
Status:  closed
Dir:     <task_dir>

Phases completed: WBS open, plan, develop, QA, close
Runtime verdict:  PASS
Health score:     <score>/10 (only if an applicable measurement was already reported; otherwise omit this line)
Files changed:    <count>
Doc:              doc/changes/<date>-<slug>.md
```

## Recovery and Error Handling

On any phase error, agent crash or MCP timeout, report the concrete failure and
inspect actual owner state via `wbs(action="context")` plus the affected operation's
state before retrying. A timeout does not prove failure or completion. Determine
whether effects occurred and whether any writer is still active; do not duplicate
unknown or non-idempotent effects. Retry only when state and effects are known
and the retry is safe. Preserve the same task/run, applicable proofs, actual
failure history and filesystem guards; do not use fresh_run as recovery.

Repair authorized failures autonomously and restore affected independent review
PASS before actual QA. Reuse unaffected applicable evidence; never skip failed
QA, invent completion, or offer override/skip/abort as routine recovery. When
repeated attempts produce no new evidence, investigate the cause rather than
blindly retrying. Unknown effects, unresolved environment or user-data blockers,
and missing permission remain explicit and unfinished with an unblock condition.
Ask only for a real unresolved material user decision or missing authorization
for external/destructive effects. Required current checks and actual BLOCKED_ENV
remain binding; a green machine check never clears substantive failure.

## Self-Improvement (post-close)

After every task close, run the pipeline in `self-improvement.md`:
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings only when a validated entry matches the just-closed verified task/run; distinct verified historical task/runs for the same key may satisfy repetition thresholds, while duplicates from one run count once
- Preserve `learnings.jsonl` as append-only; promotion never rewrites or prunes raw rows, and no-signal runs remain no-write
- Trigger retro independently after three verified task closes, using retro.py's shared close predicate rather than task-directory activity

Pipeline failures are housekeeping, not a gate. Auto-runnable follow-up tasks
are different: they must be run or explicitly blocked before DONE.
