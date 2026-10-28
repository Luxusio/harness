---
name: run
description: Orchestrate the public WBS open -> plan -> develop -> QA -> close lifecycle.
user-invocable: false
---

Orchestrate the full harness development cycle for a task.

> `PLAN.md` owns acceptance intent; existing WBS assertions own measured outcome checks.

## Substantive review and QA

Await actual independent code review and conditional security review PASS before actual QA. Fix actual FAIL findings; publish actual BLOCKED_ENV with a concrete blocker and unblock condition. Direct independent finals are the workflow results; coordinator paraphrases and copied text cannot replace them. After actual QA PASS, run `wbs(action="verify")` and close only when current required WBS checks pass. Machine verdicts measure WBS checks and do not attest review or QA. Receipt files, collectors, naming bindings and recording availability play no role; do not rerun, repair or park work for missing recording.

> **Codex runtime notes** (delta from Claude):
> - Claude's `Skill("harness:plan", task_id)` programmatic chain has no Codex equivalent — on Codex, the orchestrator reads each downstream skill's SKILL.md inline and executes its phases as part of the same conversation. The public effect is identical (task start -> plan -> develop -> QA -> close), with review and verification retained as internal close gates, but the chain is sequential prose rather than tool calls.
> - Claude's `Agent(subagent_type="oh-my-claudecode:executor", ...)` maps to Codex capability-first routing. Check the deferred tool catalog (for example `ALL_TOOLS`) before declaring `spawn_agent` unavailable. If the current Codex session exposes `spawn_agent`, use it for independent QA/review and bounded worker tasks; the user does not need to request delegation. For QA/review, `spawn_agent` is mandatory when available: the orchestrator must not self-author a PASS while skipping an available independent subagent. Await the actual independent final results; machine verification measures WBS checks. If `spawn_agent` is unavailable after discovery, run the role methodology inline and state the fallback in task state or final response only when it affects verification.
> - The sole Codex MCP tool is bare `wbs`. Task-handler names below identify private owners; use the WBS action mapping.
> - `${CLAUDE_PLUGIN_ROOT}` is not injected on Codex. Use `${HARNESS_PLUGIN_ROOT}` (set by the Codex plugin install).
> - AskUserQuestion is conversational prose on Codex when a real user decision is needed; substantive FAIL findings follow the remediation path below.

## Codex Subagent Routing

Treat explicit user invocation or approval of a harness repo-mutating workflow
as authorization to use the subagents required by that workflow's verification
and review gates. Examples include "use harness", "run/continue/close the
harness task", native `/goal`, or clear approval to proceed with a harness task.
This workflow authorization does not apply to read-only answers or ordinary
non-harness work.

Route from the current session tools and the task shape, not from whether the
user explicitly requested delegation. "The user did not ask for parallel
agents" is not a valid reason to skip `spawn_agent`. Do not wait for the user
to request delegation. User request is not a condition for parallel routing.

When `spawn_agent` is available and work is independent, use it for
independent review/QA and bounded side work. Independent lanes include separate
AC ownership, QA/UX lenses, read-only exploration, and bounded worker tasks
that can run without touching the same files. Use concrete Codex calls like:

```text
spawn_agent {
  task_name: "qa_cli_<task_slug>_<run_id>",
  message: "task_name: qa_cli_<task_slug>_<run_id>\nYou are the qa-cli lens for <task_id>. Read <task_dir>/PLAN.md and the planned target files. Run focused verification. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with concrete findings and evidence.",
  fork_turns: "all"
}
```

For bounded code-change side work:

```text
spawn_agent {
  agent_type: "worker",
  message: "Implement AC-00X only. Ownership: <paths>. You are not alone in the codebase; do not revert edits made by others. Edit files directly and list changed paths in your final answer.",
  fork_context: true
}
```

For read-only codebase questions:

```text
spawn_agent {
  agent_type: "explorer",
  message: "Inspect <specific files/area> and answer <specific question>. Do not modify files.",
  fork_context: false
}
```

Await every required independent review and QA result. Select clear task names for operator readability; names have no verification authority.

Subagent lifecycle cleanup: track every `agent_id` returned by `spawn_agent`.
After a spawned agent completes, fails, is cancelled, or is no longer needed,
call `close_agent` when that tool is available. Before final response,
`wbs(action="close")`, or handoff, close every agent this workflow spawned unless the
user explicitly asked to leave a still-running agent open. Completed agents can
continue to count toward the concurrency limit until closed.

Subagent wait UX: after spawning a batch, do useful coordinator-side work
before waiting. When no useful work remains, use one `wait_agent` interval of
up to 60 seconds. Do not issue rapid 10/20/30-second wait loops or interleave
agent-status polling between timeouts. After a timeout, give one compact status update
before the next wait interval. Treat an agent's progress message and
final response as one lifecycle; do not add an extra wait solely to collect a
duplicate completion notification. Use `wait_agent` only to coordinate
completion. `wait_agent` and `list_agents` output coordinate work; they do not establish substantive PASS.

Use inline execution as the fallback for roles that normally benefit from independence only when `spawn_agent` is unavailable or the work is not actually independent. If independent work runs sequentially, state the concrete blocker and affected lanes in the lane table or final response; vague reasons such as lack of user request are invalid. Do not create a runtime fallback document just to record routing history.

## Sub-file

`${HARNESS_PLUGIN_ROOT}/internal-skills/run/self-improvement.md` — signal detection, auto-fix, and validated tiered-learning promotion with an append-only raw ledger (runs after each task close).

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
harness task. If an active task exists, call `wbs(action="context")` for that task and
resume instead of creating a duplicate.

Resume routing:
- Source task already closed with owned temporary worktrees remaining → Phase 5
  coordinator disposal only; do not repeat close or lenses solely for cleanup.
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

(On Codex MCP this is the bare tool name; Claude uses a runtime-prefixed form.) Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/plan/SKILL.md` and execute its phases inline, passing `task_id`. The plan skill writes PLAN.md to the task_dir. On BLOCKED: stop and report.

On Codex side the plan skill uses the available runtime surface. When `spawn_agent` is available, use it to run one independent reviewer subagent per phase; otherwise run the review methodology inline as coordinator-only and state the fallback in task state or final response if expected independence was lost. The premise gate becomes a conversational ask.

### Phase 3: Develop

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/develop/SKILL.md` and execute its phases, passing `task_id`. It uses the canonical-loop methodology with `Agent` fan-out routed through `spawn_agent` when available, `Skill()` chains rendered as inline-read sub-skill references, and `AskUserQuestion` gates rendered as conversational prose asks. Execute Phase 0 through Phase 9. Develop owns the implementation-through-close transaction, including independent code review, QA, verified installation, durable-doc classification, final freshness restoration, and `wbs(action="close")`. Execute that lifecycle once, suppress its nested user-facing completion response, and return here for post-close continuation. Do not run a second QA or close cycle after develop succeeds. On BLOCKED: stop and report.

Multi-lens parallel QA (qa-browser + qa-api in one batch) should use `spawn_agent` when available. Browser MCP verification is availability-gated: if the current Codex session exposes browser tools (for example `chrome_devtools` or a future Playwright MCP), run the qa-browser methodology via subagent when possible or inline when no subagent path exists; if browser verification is required but no browser tool or reachable app exists, write a browser-lens `BLOCKED_ENV` verdict instead of silently falling back to CLI-only QA.

On completion: actual independent review and QA passed, `wbs(action="verify")` reports PASS, and the task is closed. If BLOCKED: preserve the unfinished blocker and unblock condition; ask only for a missing user decision or authorization.

Before entering develop, re-entering after QA/UX FAIL, verifying, or closing,
incorporate explicit user corrections from the conversation. Promote durable
rules directly into PLAN.md or the applicable project documentation; Harness
does not maintain a separate feedback sidecar.

### Phase 4: Verify recovery (only when develop returned before close)

Skip this phase when the source task is already closed. This is a recovery path for an
interrupted or older develop flow, not a second QA pass. First call
`wbs(action="context")`. Inspect current actual independent finals before
machine checks. Reuse applicable actual review and QA/UX PASS results; rerun
only actually unrun, failed or stale substantive work. Never rerun successful
work for recording. The agent definition owns its final output contract.

Read `doc/harness/manifest.yaml` for project type. On Codex, choose the appropriate QA lens and route it by current capability: discover deferred tools first, use `spawn_agent` when available, and use inline methodology only as fallback. If `spawn_agent` is available, the QA lens MUST run as a subagent; the orchestrator must not invent a PASS from its own context. Also route applicable UX review lenses for user-facing surfaces. Await actual QA finals with concrete findings and evidence; a spawn alone is not completion.

**Strategy selection:**
- **qa-browser** — required when `manifest.qa.browser_qa_supported: true` AND the diff contains frontend files (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments). On Codex, check the actual session tool surface first. If browser tools are available, route the qa-browser lens through `spawn_agent` when available; otherwise read `${HARNESS_PLUGIN_ROOT}/agents/qa-browser.md` and run that methodology inline, including real page navigation/interactions/screenshots where the tools support it. If browser QA is required but no browser tool is available, the dev server cannot be reached, or a required browser setup is impossible, return `BLOCKED_ENV` with the exact blocker instead of fabricating a PASS.
- `desktop_qa_supported: true` → qa-desktop via `spawn_agent` when available; otherwise run the methodology inline only if desktop tools are available, or write `BLOCKED_ENV` with the missing tool/display condition.
- `type: api` or diff contains route/endpoint files → qa-api via `spawn_agent` when available; otherwise inline fallback.
- `type: cli` or `type: library` → qa-cli via `spawn_agent` when available; otherwise inline fallback.

**UX strategy selection:**
- frontend/browser UI diff with `browser_qa_supported: true` or `ux_review_supported: true` → ux-browser
- CLI command/help/output/error diff with `ux_review_supported: true` → ux-cli
- API route/schema/error/docs diff with `ux_review_supported: true` → ux-api
- desktop GUI diff with `desktop_qa_supported: true` or `ux_review_supported: true` → ux-desktop

When QA and UX lenses both apply, use `spawn_agent` to run them in parallel
where available; otherwise run the UX methodology inline after QA. UX lenses
read `${HARNESS_PLUGIN_ROOT}/agents/ux-<lens>.md` and return findings in their final
response. Actual QA finals are workflow results; WBS checks remain the machine completion basis.

Order: desktop branch before `type: cli` fallback so a desktop app declared as `type: cli` still routes to qa-desktop.

QA subagent pattern on Codex:

```text
spawn_agent {
  task_name: "qa_<lens>_<task_slug>_<run_id>",
  message: "task_name: qa_<lens>_<task_slug>_<run_id>\nYou are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md and ${HARNESS_PLUGIN_ROOT}/agents/qa-<lens>.md. Follow all four roles. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with command/browser evidence and concrete findings.",
  fork_turns: "all"
}
```

Use distinct task names to prevent collaboration-tree name collisions. Names do not bind verification authority. Run each applicable QA lens once per substantive verification cycle.

Handle actual QA/UX results before `wbs(action="verify")`:
- **FAIL**: return to Phase 3, fix substantive findings and restore affected
  independent review PASS before rerunning actual QA/UX. A green machine
  verdict cannot clear an unresolved actual failure.
- **BLOCKED_ENV**: report the exact external blocker and executable unblock
  condition; use `wbs(action="block")` for unfinished work. Do not close or
  convert the blocker to PASS.
- **PASS**: only after every applicable actual lens passes, run `wbs(action="verify")`.

Its runtime verdict and `missing_for_close` describe current WBS assertions,
blocked state and applicability; they do not attest review/QA.

QA inline fallback on Codex reads the relevant qa-* prompt, follows the same
methodology in-conversation. It does not write a critic artifact or handoff
artifact. If the fallback means verification is incomplete, return
`BLOCKED_ENV`; otherwise report the concrete commands/interactions used.

Machine PASS permits Phase 5 only after actual PASS; otherwise fix the named
WBS check and reverify. Never label a machine check failure as a QA verdict or
offer an override of substantive failure.

**Persist QA failure patterns** after each retry cycle:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"qa-failure-pattern","source":"run-retry","runtime":"codex","key":"FAILURE_TYPE","insight":"QA failed: <reason>, workaround: <fix>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 4.5: Health reporting

Reuse an already reported health measurement only when it applies to the current
scope. Otherwise omit the health score from the completion report. Never invent
a zero score or report an unmeasured result as PASS.

Do not launch `health.py`, `test_command`, or component checks to obtain a score
at completion. Explicit manual health execution remains available; `--dry-run`
only lists planned checks and produces no measurement.

### Phase 5: Close

Read `${HARNESS_PLUGIN_ROOT}/internal-skills/run/worktree-completion.md` now.
Follow its ownership, surviving-checkout and batch-lead rules. After source
close, finish outstanding coordinator integration/disposal before reporting
overall completion; report retained originals with their blocker and next action.
Already-closed sources still need this disposition, without repeating close or
rerunning lenses solely for cleanup.

Skip the `wbs(action="close")` call when Phase 3 already closed the task or the source
was already closed on resume. Otherwise this phase owns the one recovery close
attempt after Phase 4 has restored fresh PASS
evidence.

```
wbs { action: "close", wbs_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: run self-improvement pipeline (see `self-improvement.md` in the
Claude tree) before emitting the completion report.

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
Runtime: codex

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

After every task close, run the pipeline in `self-improvement.md` (Claude tree):
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Treat dogfooding feedback, retrospectives, QA complaints, and agent-proposed
  harness improvements as hypotheses until the repo proves them. Before shaping
  backlog or implementation, inspect owning code/tests and classify each claim
  as `confirmed`, `partially-confirmed`, `already-handled`, `duplicate`,
  `not-found`, or `needs-runtime-check`.
- Rewrite `partially-confirmed` claims to the smallest accurate failing case,
  and preserve QA/runtime/close gate safety by adding an explicit alternative
  evidence tier instead of removing a gate.
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings only when a validated entry matches the just-closed verified task/run; distinct verified historical task/runs for the same key may satisfy repetition thresholds, while duplicates from one run count once
- Preserve `learnings.jsonl` as append-only; promotion never rewrites or prunes raw rows, and no-signal runs remain no-write
- Trigger retro independently after three verified task closes, using retro.py's shared close predicate rather than task-directory activity

Pipeline failures are housekeeping, not a gate. Auto-runnable follow-up tasks
are different: they must be run or explicitly blocked before DONE.

---
