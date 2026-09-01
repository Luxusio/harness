---
name: run
description: Orchestrate the public task_start → plan → develop → QA → close lifecycle.
argument-hint: <task-slug-or-description>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context, mcp__plugin_harness_harness__task_verify, mcp__plugin_harness_harness__task_close
---

Orchestrate the full harness development cycle for a task.

> Current artifact model: `PLAN.md` owns acceptance intent and unified
> `RECEIPTS.jsonl` owns review/QA evidence.

## Missing receipt policy

Receipt absence never immediately fails or suppresses a substantive lens. Await
the actual result and label an unreceipted final **NON-ATTESTING**: actual FAIL
is remediated, actual BLOCKED_ENV uses the standard blocker path, and only an
actual review PASS advances to substantive QA. Do not repair, restart, resume,
recollect, or rerun a lens solely to obtain a receipt. After actual QA PASS,
call `task_verify` once; close on ordered receipt PASS, otherwise enter the
stop-judge/`task_blocked` path with a generic attestation-evidence reason.
Direct finals never authorize PASS or close.
> Receipt acquisition is normative in
> `doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md`; storage,
> schema, snapshots, and gates are normative in
> `doc/harness/patterns/ADR__consolidated-task-artifacts.md`.

Explicit user invocation or approval of this harness repo-mutating workflow
authorizes the subagents required by the workflow's verification and review
gates. Examples include "use harness", "run/continue/close the harness task",
native `/goal`, or clear approval to proceed with a harness task. This workflow
authorization does not apply to read-only answers or ordinary non-harness work.

When the runtime exposes explicit close tools such as `close_agent`, track each
spawned agent id and close it after the result is consumed, the agent completes
or fails, it is cancelled, or it is no longer needed. Before final response,
`task_close`, or handoff, do not leave completed agents open unless the user
explicitly asked to keep a still-running agent alive. Completed agents can
continue to count toward the concurrency limit until closed.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, and validated tiered-learning promotion with an append-only raw ledger (runs after each task close).

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On any phase failure: stop, report, ask how to proceed.

Plain repo-mutating requests are valid task intake. Do not require the user to
re-issue a clear feature, fix, refactor, behavior change, or durable process/doc
request as `/goal`. If no active task can be resolved, create or resume the
harness task yourself and continue through this flow.

### Native Goal continuation

Add Goal children by dependency/risk; future IDs are valid. Use `goal_add_task`;
`goal_next_task` selects first queued/active. Present the selected next task as status.

### Phase 0: Resume detection

Before creating a new task, check whether this session already has an active
harness task. If one exists, call `mcp__plugin_harness_harness__task_context`
and resume rather than creating a duplicate.

Resume routing:
- PLAN.md missing → Phase 2 Plan.
- PLAN.md exists and runtime_verdict is not PASS → Phase 3 Develop/Verify.
- runtime_verdict is PASS and `missing_for_close` is empty → Phase 5 Close.
- `missing_for_close` names specific artifacts or AC blockers → fix that gate
  and then continue from the corresponding phase.

Only call `task_start` when no active task can be resolved, or when the user
explicitly asks for a new task.

If only a Goal is active, call `goal_next_task`; if none and unproven, attach a child.

### Phase 1: Start task

```
mcp__plugin_harness_harness__task_start { slug: "<ARGUMENTS>" }
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

The develop skill reads PLAN.md, implements changes, runs plan completion audit, scope drift detection, bisectable commits, verification gate, runtime QA subagents, and any needed durable-doc updates. On completion, fresh hook-owned QA completion receipts exist and `task_verify` reports PASS. If BLOCKED: stop, report, ask user.

Before entering develop, re-entering after QA/UX FAIL, verifying, or closing,
incorporate explicit user corrections from the conversation. Promote durable
rules directly into PLAN.md or the applicable project documentation; Harness
does not maintain a separate feedback sidecar.

### Phase 4: Verify recovery (only when develop returned before close)

Skip this phase when Phase 3 closed the task. Develop Phase 7 owns QA lens
selection and spawning, and develop Phase 6.6 owns review; this phase is a
recovery path for an interrupted or older develop flow, not a second QA pass.

First call `task_context`. When the required review and QA lenses already have
completed PASS receipts for the current run, call `task_verify` only. Spawn a
lens here for one that is actually unrun, failed, or stale. A missing receipt
after an awaited substantive final is not an unrun lens and never justifies a
receipt-only rerun; apply the Missing receipt policy instead.

For a lens that must be re-run, follow `develop/SKILL.md` Phase 7 and
`develop/quality-audit-pipeline.md` — do not restate routing here. Use the
native subagent types (`harness:qa-cli`, `harness:qa-api`, `harness:qa-browser`,
`harness:qa-desktop`, and the matching `harness:ux-*`), and issue multiple
lenses in a single assistant message so every start is hook-recorded.

Two selection rules are gates rather than routing detail, so they are stated
here and not delegated:

- You **MUST spawn qa-browser** when `manifest.qa.browser_qa_supported: true`
  AND the diff contains any frontend file (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss`
  or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments).
  Skipping leaves no completed qa-browser receipt, and `task_close` blocks on it.
- Spawn the applicable UX lens alongside QA for user-facing surfaces:
  `ux-browser` for browser UI, `ux-cli` for command/help/output/error changes,
  `ux-api` for route/schema/error/docs changes, `ux-desktop` for desktop GUI —
  each gated on `ux_review_supported: true` (or the matching
  `browser_qa_supported`/`desktop_qa_supported`). UX review does not replace QA:
  `qa-*` proves correctness, `ux-*` judges whether the experience is shippable.

Agents return PASS/FAIL/BLOCKED_ENV findings in their final response; do not ask
them to write critic artifacts.

Then run `task_verify`. It computes the verdict from required ordered review and
QA completions in `RECEIPTS.jsonl`; PLAN.md remains the sole acceptance
document. It exposes required lenses, verdicts, and `missing_for_close`, not raw
receipt records.

After completion, check runtime_verdict:
- **PASS**: proceed to Phase 5.
- **FAIL**: report findings, then ask:
  ```
  QA returned FAIL. Findings: <summary>
  A) Send back to developer — fix the issues
  B) Override — accept current state (requires justification)
  C) Abort task
  ```
  A → return to Phase 3 with QA findings as additional context. Retry limit: 3 cycles. After 3 FAILs: stop and report.

**Persist QA failure patterns** after each retry cycle:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"qa-failure-pattern","source":"run-retry","key":"FAILURE_TYPE","insight":"QA failed: <reason>, workaround: <fix>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 4.5: Health score snapshot

Before closing, capture the final project health score:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py --dry-run 2>&1 || true
```

Store the printed score for inclusion in the completion report.

### Phase 5: Close

```
mcp__plugin_harness_harness__task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: run self-improvement pipeline (see `self-improvement.md`) before
emitting the completion report.

For harness-source changes, develop Phase 7.8 installs the verified payload
before this close attempt. Do not defer installation until after close. If the
already-running MCP/hook process still reports missing required lifecycle
evidence after substantive QA and one fresh `task_verify`, use the standard
stop-judge/`task_blocked` attestation path; do not request a new session or
rerun a lens solely for a receipt, and do not write receipts by hand.
The stateless root installer remains idempotent for ordinary verified-delivery
retries, but receipt absence is not a reason to invoke it again.

For a Goal child, run `task_close` first, then self-improvement including
learning promotion; only then call `goal_next_task`.
Continue unless complete, blocked/stopped, capped, or awaiting go/no-go.

For this harness plugin source repo, successful repo-mutating development is
not complete at task close. Phase 7.8 must already have run the verified
auto-install helper after the last source edit and fresh QA. After
post-close self-improvement has run, commit the completed diff
before the final response unless the user explicitly says not to. Include
the commit hash and pre-close conditional verified-delivery result in the completion report.

## Completion Report

Before writing DONE, assert:
- primary task is closed
- runtime_verdict is PASS or task is BLOCKED
- post-close self-improvement has run
- if this was a native Goal child task, the Goal is done/blocked/stopped/budgeted
  or the next slice is already active/queued
- for this harness plugin source repo, the completed diff has been committed
  and verified delivery has confirmed synchronized runtime payloads, unless the user explicitly opted out

```
DONE

Task:    <task_id>
Status:  closed
Dir:     <task_dir>

Phases completed: task_start, plan, develop, QA, close
Runtime verdict:  PASS
Health score:     <score>/10
Files changed:    <count>
Doc:              doc/changes/<date>-<slug>.md
```

## Retry Tracking

Phase 3 (develop): max 3 retries after runtime FAIL. After max: stop, emit DONE_WITH_CONCERNS.

## Error Handling

On any agent timeout or crash:
1. Report what happened
2. Check state via `task_context`
3. Ask user: retry / skip / abort

Stop on phase failures, report the failure, check task state, and ask how to proceed.

## Self-Improvement (post-close)

After every task close, run the pipeline in `self-improvement.md`:
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings only when a validated entry matches the just-closed receipt-verified task/run; distinct verified historical task/runs for the same key may satisfy repetition thresholds, while duplicates from one run count once
- Preserve `learnings.jsonl` as append-only; promotion never rewrites or prunes raw rows, and no-signal runs remain no-write
- Trigger retro independently after three receipt-verified task closes, using retro.py's shared close predicate rather than task-directory activity

Pipeline failures are housekeeping, not a gate. Auto-runnable follow-up tasks
are different: they must be run or explicitly blocked before DONE.
