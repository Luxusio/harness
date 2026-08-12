---
name: run
description: Orchestrate full development cycle — plan -> develop -> verify -> close.
user-invocable: false
---

# GENERATED-CANDIDATE — hand-ported v1.5 spike from plugin/skills/run/SKILL.md (171L source).
# Source canonical at plugin/skills/run/SKILL.md. v1.5 AC-005 sync engine will replace this
# hand-port with mechanical emission. Lives here only to measure porting friction for
# AC-002 of TASK__dual-runtime-v1.5-spike-and-sync.


Orchestrate the full harness development cycle for a task.

> Current artifact model: `PLAN.md` owns acceptance intent and unified
> `RECEIPTS.jsonl` owns review/QA evidence. Do not create or consume
> `CHECKS.yaml` or `USER_FEEDBACK.jsonl`; later legacy wording is non-operative.

> **Codex runtime notes** (delta from Claude):
> - Claude's `Skill("harness:plan", task_id)` programmatic chain has no Codex equivalent — on Codex, the orchestrator reads each downstream skill's SKILL.md inline and executes its phases as part of the same conversation. Effect is identical (plan -> develop -> verify -> close), but the chain is sequential prose, not tool calls.
> - Claude's `Agent(subagent_type="oh-my-claudecode:executor", ...)` maps to Codex capability-first routing. Check the deferred tool catalog (for example `ALL_TOOLS`) before declaring `spawn_agent` unavailable. If the current Codex session exposes `spawn_agent`, use it for independent QA/review and bounded worker tasks; the user does not need to request delegation. For QA/review, `spawn_agent` is mandatory when available: the orchestrator must not self-author a PASS while skipping an available independent subagent. The Codex lifecycle watcher records starts and observed completions in `RECEIPTS.jsonl`; do not call a receipt writer or critic writer yourself. If `spawn_agent` is unavailable after discovery, run the role methodology inline and state the fallback in task state or final response only when it affects verification.
> - MCP tool names on Codex use bare form (`task_start`, `task_verify`, `task_close`) — not Claude-prefixed form. Where this skill mentions a prefixed name, read it as the bare form.
> - `${CLAUDE_PLUGIN_ROOT}` is not injected on Codex. Use `${HARNESS_PLUGIN_ROOT}` (set by the Codex plugin install).
> - AskUserQuestion (Phase 4 FAIL retry) is conversational prose on Codex — emit the question + options, read the reply from the next user turn.

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

The MCP-hosted Codex lifecycle watcher owns the protected receipt stream. Do
not call a receipt tool or write critic verdict artifacts. Do not call a harness receipt tool
or write `RECEIPTS.jsonl` yourself. If no subagent was
spawned, use the inline fallback path and record the material fallback reason;
strict close still requires the plan-declared independent evidence.

Every review or QA spawn must provide a valid structured `task_name` containing
its exact lens. Prompt text may mirror it only for readability. Await the final
response normally; late recovery cannot authorize already-completed work.
Codex acquisition/identity/completion is defined only by
`doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md`; storage,
schema, snapshots, and gates are defined only by
`doc/harness/patterns/ADR__consolidated-task-artifacts.md`.

Subagent lifecycle cleanup: track every `agent_id` returned by `spawn_agent`.
After a spawned agent completes, fails, is cancelled, or is no longer needed,
call `close_agent` when that tool is available. Before final response,
`task_close`, or handoff, close every agent this workflow spawned unless the
user explicitly asked to leave a still-running agent open. Completed agents can
continue to count toward the concurrency limit until closed.

Subagent wait UX: after spawning a batch, do useful coordinator-side work
before waiting. When no useful work remains, use one `wait_agent` interval of
up to 60 seconds. Do not issue rapid 10/20/30-second wait loops or interleave
agent-status polling between timeouts. After a timeout, give one compact status update
before the next wait interval. Treat an agent's progress message and
final response as one lifecycle; do not add an extra wait solely to collect a
duplicate completion notification. Use `wait_agent` only to coordinate
completion. `wait_agent` and `list_agents` output do not author receipts.

Use inline execution as the fallback for roles that normally benefit from independence only when `spawn_agent` is unavailable or the work is not actually independent. If independent work runs sequentially, state the concrete blocker and affected lanes in the lane table or final response; vague reasons such as lack of user request are invalid. Do not create a runtime fallback document just to record routing history.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, tiered-learning promotion + pruning pipeline (runs after each task close). Not ported in v1.5 spike; the Codex orchestrator reads the Claude-side sub-file at `plugin/skills/run/self-improvement.md` if/when self-improvement runs.

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On any phase failure: stop, report, ask how to proceed.

Plain repo-mutating requests are valid task intake. Do not require the user to
re-issue a clear feature, fix, refactor, behavior change, or durable process/doc
request as `/goal`. If no active task can be resolved, create or resume the
harness task yourself and continue through this flow.

### Task-pack continuation

For a user request that names multiple sequential stages, roadmap items, or
follow-up tasks, create an ordered task pack before implementation when the
known tasks can be named. Use `plugin/scripts/task_pack_runner.py init` with
one `--task "slug:title"` per known stage. The user does not choose the split
or sequence; derive order from the stated roadmap, dependency order, or
highest-risk/highest-value order.

When a task pack exists, use `task_pack_runner.py next` / `claim-next` to report
and claim the next queued task. Present the selected next task as status, not a
question. Ask the user only for go/no-go at an agreed batch boundary, genuine
product/architecture/auth/billing/data/destructive decisions, environment or
credential blockers, or contradictions that would likely implement the wrong
intent.

### Phase 0: Resume detection

Before creating a new task, check whether this session already has an active
harness task. If an active task exists, call `task_context` for that task and
resume instead of creating a duplicate.

Resume routing:
- PLAN.md missing → Phase 2 Plan.
- PLAN.md exists and runtime_verdict is not PASS → Phase 3 Develop/Verify.
- runtime_verdict is PASS and `missing_for_close` is empty → Phase 5 Close.
- `missing_for_close` names specific artifacts or AC blockers → fix that gate
  and then continue from the corresponding phase.

Only call `task_start` when no active task can be resolved, or when the user
explicitly asks for a new task.

If no active task exists and `doc/harness/task-packs/current.json` is active,
run `python3 ${HARNESS_PLUGIN_ROOT}/scripts/task_pack_runner.py next` and then
`claim-next` for the next queued item. Start that returned `task_id` or slug
without asking which task to do next.

### Phase 1: Start task

```
task_start { slug: "<ARGUMENTS>" }
```

(On Codex MCP this is the bare tool name; Claude uses a runtime-prefixed form.) Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

Read `plugin-codex/internal-skills/plan/SKILL.md` (the v1.5 hand-port; AC-003 spike target) and execute its phases inline, passing `task_id`. The plan skill writes PLAN.md to the task_dir. On BLOCKED: stop and report.

On Codex side the plan skill uses the available runtime surface. When `spawn_agent` or external model routes are available, use them for independent review voices; otherwise run the review methodology inline and state the fallback in task state or final response if expected independence was lost. The premise gate becomes a conversational ask.

### Phase 3: Develop

Read `plugin-codex/internal-skills/develop/SKILL.md` and execute its phases, passing `task_id`. The develop skill on Codex is a hand-port of the Claude source (`plugin/skills/develop/SKILL.md`) under the MCP-only-sharing policy (spike-report §3.6) — same canonical-loop methodology, with `Agent` fan-out routed through `spawn_agent` when available, `Skill()` chains rendered as inline-read sub-skill references, and `AskUserQuestion` gates rendered as conversational prose asks. Execute Phase 0 through Phase 9. Develop owns the implementation-through-close transaction, including independent code review, QA, verified installation, durable-doc classification, final freshness restoration, and `task_close`. Execute that lifecycle once, suppress its nested user-facing completion response, and return here for post-close continuation. Do not run a second QA or close cycle after develop succeeds. On BLOCKED: stop and report.

Multi-lens parallel QA (qa-browser + qa-api in one batch) should use `spawn_agent` when available. Browser MCP verification is availability-gated: if the current Codex session exposes browser tools (for example `chrome_devtools` or a future Playwright MCP), run the qa-browser methodology via subagent when possible or inline when no subagent path exists; if browser verification is required but no browser tool or reachable app exists, write a browser-lens `BLOCKED_ENV` verdict instead of silently falling back to CLI-only QA.

On completion: watcher-owned review and QA receipts are present, `task_verify` reports PASS, and the task is closed. If BLOCKED: stop, report, ask user.

Before entering develop, re-entering after QA/UX FAIL, verifying, or closing,
incorporate explicit user corrections from the conversation. Promote durable
rules directly into PLAN.md or the applicable project documentation; Harness
does not maintain a separate feedback sidecar.

### Phase 4: Verify recovery (only when develop returned before close)

Skip this phase when Phase 3 closed the task. This is a recovery path for an
interrupted or older develop flow, not a second QA pass. First call
`task_context`: when fresh required QA receipts and a PASS verdict already
exist, call `task_verify` only; spawn QA below only for a missing, failed, or
stale required lens.

Read `doc/harness/manifest.yaml` for project type. On Codex, choose the appropriate QA lens and route it by current capability: discover deferred tools first, use `spawn_agent` when available, and use inline methodology only as fallback. If `spawn_agent` is available, the QA lens MUST run as a subagent; the orchestrator must not invent a PASS from its own context. Also route applicable UX review lenses for user-facing surfaces. Verification is recognized by watcher-recorded QA completions in `RECEIPTS.jsonl`; findings and the explicit verdict come from the subagent final response. A start entry alone cannot pass verification.

**Strategy selection:**
- **qa-browser** — required when `manifest.qa.browser_qa_supported: true` AND the diff contains frontend files (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments). On Codex, check the actual session tool surface first. If browser tools are available, route the qa-browser lens through `spawn_agent` when available; otherwise read `plugin-codex/agents/qa-browser.md` and run that methodology inline, including real page navigation/interactions/screenshots where the tools support it. If browser QA is required but no browser tool is available, the dev server cannot be reached, or a required browser setup is impossible, return `BLOCKED_ENV` with the exact blocker instead of fabricating a PASS.
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
read `plugin-codex/agents/ux-<lens>.md` and return findings in their final
response. QA completion receipts are the close-gate verification signal; no
UX critic artifact is written.

Order: desktop branch before `type: cli` fallback so a desktop app declared as `type: cli` still routes to qa-desktop.

QA subagent pattern on Codex:

```text
spawn_agent {
  task_name: "qa_<lens>_<task_slug>_<run_id>",
  message: "task_name: qa_<lens>_<task_slug>_<run_id>\nYou are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md and plugin-codex/agents/qa-<lens>.md. Follow all four roles. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with command/browser evidence and concrete findings.",
  fork_turns: "all"
}
```

Start each QA run with a fresh task name whose prefix is exactly `qa_cli_`,
`qa_api_`, `qa_browser_`, or `qa_desktop_`, followed by a short sanitized task
slug and run id. The watcher binds the lens from that prefix and the unique
suffix prevents collaboration-tree name collisions across sequential tasks.
Run at most one agent for each required QA lens in a single verification cycle.

After awaiting QA/UX, run `task_verify`. The verify step reads `RECEIPTS.jsonl`
and computes the verdict from all required ordered review and QA completions.
`task_verify` returns a `subagent_receipts` summary
so missing independent QA/UX calls are visible before close.

QA inline fallback on Codex reads the relevant qa-* prompt, follows the same
methodology in-conversation. It does not write a critic artifact or handoff
artifact. If the fallback means verification is incomplete, return
`BLOCKED_ENV`; otherwise report the concrete commands/interactions used.

After `task_verify`, check the returned `runtime_verdict`:
- **PASS**: proceed to Phase 5.
- **FAIL**: report findings, then ask the user:
  > QA returned FAIL. Findings: <summary>
  > A) Send back to develop — fix the issues
  > B) Override — accept current state (justify in the next reply)
  > C) Abort task

  A → return to Phase 3 with QA findings as additional context. Retry limit: 3 cycles. After 3 FAILs: stop and report.

**Persist QA failure patterns** after each retry cycle:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"qa-failure-pattern","source":"run-retry","runtime":"codex","key":"FAILURE_TYPE","insight":"QA failed: <reason>, workaround: <fix>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 4.5: Health score snapshot

If Phase 3 did not already capture it, capture the final project health score:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py --dry-run 2>&1 || true
```

Store the printed score for inclusion in the completion report.

### Phase 5: Close

Skip the `task_close` call when Phase 3 already closed the task. Otherwise this
phase owns the one recovery close attempt after Phase 4 has restored fresh PASS
evidence.

```
task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: run self-improvement pipeline (see `self-improvement.md` in the
Claude tree) before emitting the completion report.

For harness-source changes, develop Phase 7.8 installs the verified payload
before this close attempt. Do not defer installation until after close. If the
already-running MCP/hook process still reports a pre-install receipt gap, keep
the task pending and request a new thread; do not write receipts by hand. On
resume, the root installer is idempotent and may be run again after confirming
the diff still has fresh review+QA PASS.

If this run belongs to an active task pack, mark the item closed with
`python3 ${HARNESS_PLUGIN_ROOT}/scripts/task_pack_runner.py close --task <slug>`.
If the runner prints another `next:` item, start or queue that task before a
final DONE response unless the pack is done, blocked/stopped, budget-capped, or
waiting at an explicit user go/no-go boundary.

If this run is a Goal queue child task, task close is an iteration checkpoint
rather than a final Goal completion. Before any final response, run the Goal
queue iteration review: compare the implementation to the locked Goal, list
remaining gaps, choose the next highest-value slice, and start or queue that
slice unless the Goal is done, blocked/stopped by user or environment, or the
Goal queue budget/cap has been reached.

For this harness plugin source repo, successful repo-mutating development is
not complete at task close. Phase 7.8 must already have run the verified
auto-install helper after the last source edit and fresh QA. After
post-close self-improvement returns `none` or `queued`, commit the completed diff
before the final response unless the user explicitly says not to. Include
the commit hash and pre-close force-install result in the completion report.

## Mandatory Follow-up Continuation

Post-close self-improvement is part of the same Goal child-task transaction.
Do not report DONE until the pipeline has been evaluated.

If `hygiene_followup.py --json` returns `"action": "run_followup"`:
- Treat the returned `task_id` as the next active harness task immediately.
- Do not send a final completion response yet.
- Do not treat this as optional cleanup or a recommendation.
- Run that follow-up through plan/develop/verify/close before reporting DONE.
- If the user asks for a commit, status, or summary during this window, satisfy
  that request briefly, then continue the follow-up unless the user explicitly
  says stop, pause, or cancel.
- If another follow-up returns `"action": "run_followup"`, continue up to
  `HARNESS_AUTO_FOLLOWUP_MAX` (default 3). After the cap, report the queued
  work instead of continuing indefinitely.

If the result is `"queued"`, report the queued task and stop. If the result is
`"none"`, the harness run may complete. If the follow-up is blocked, record the
blocker through the normal task-blocked path and report it.

## Completion Report

Before writing DONE, assert:
- primary task is closed
- runtime_verdict is PASS or task is BLOCKED
- post-close self-improvement returned `none` or `queued`
- no auto-runnable follow-up task remains open
- if this was a Goal queue child task, the Goal is done/blocked/stopped/budgeted
  or the next slice is already active/queued
- for this harness plugin source repo, the completed diff has been committed
  and `python3 install.py --force` has run, unless the user explicitly opted out

```
DONE

Task:    <task_id>
Status:  closed
Dir:     <task_dir>
Runtime: codex

Phases completed: plan, develop, verify, close
Runtime verdict:  PASS
Health score:     <score>/10
Files changed:    <count>
Doc:              doc/changes/<date>-<slug>.md
```

## Retry Tracking

Phase 3 (develop): max 3 retries after runtime FAIL. After max: stop, emit DONE_WITH_CONCERNS.

## Error Handling

On any phase error or MCP timeout:
1. Report what happened
2. Check state via `task_context`
3. Ask user: retry / skip / abort

Stop on phase failures, report the failure, check task state, and ask how to proceed.

## Self-Improvement (post-close)

After every task close, run the pipeline in `self-improvement.md` (Claude tree):
- Schedule pending hygiene as a separate follow-up task; do not mix unrelated
  hygiene cleanup into the just-finished primary task
- If the scheduler returns `run_followup`, continue that task before reporting
  DONE; this is a mandatory continuation, not advisory cleanup
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
- Promote learnings: Tier 3 (jsonl) -> Tier 2 (patterns/*.md) -> Tier 1 (CLAUDE.md or AGENTS.md)
- Prune promoted entries and stale (>90 day) non-eureka entries

Pipeline failures are housekeeping, not a gate. Auto-runnable follow-up tasks
are different: they must be run or explicitly blocked before DONE.

---
