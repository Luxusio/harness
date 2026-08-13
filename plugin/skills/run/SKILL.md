---
name: run
description: Orchestrate full development cycle — plan → develop → verify → close.
argument-hint: <task-slug-or-description>
user-invocable: false
allowed-tools: Read, Glob, Grep, Bash, Agent, Skill, AskUserQuestion, mcp__plugin_harness_harness__task_start, mcp__plugin_harness_harness__task_context, mcp__plugin_harness_harness__task_verify, mcp__plugin_harness_harness__task_close
---

Orchestrate the full harness development cycle for a task.

> Current artifact model: `PLAN.md` owns acceptance intent and unified
> `RECEIPTS.jsonl` owns review/QA evidence.
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

`self-improvement.md` — signal detection, auto-fix, tiered-learning promotion + pruning pipeline (runs after each task close).

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

If no active task exists and `doc/harness/task-packs/current.json` is active,
run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/task_pack_runner.py next` and then
`claim-next` for the next queued item. Start that returned `task_id` or slug
without asking which task to do next.

### Phase 1: Start task

```
mcp__plugin_harness_harness__task_start { slug: "<ARGUMENTS>" }
```

Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

```
Skill("harness:plan", "<task_id>")
```

The plan skill runs its full review pipeline and writes PLAN.md. On completion: PLAN.md exists in task_dir. If BLOCKED: stop and report.

### Phase 3: Develop

```
Skill("harness:develop", "<task_id>")
```

The develop skill reads PLAN.md, implements changes, runs plan completion audit, scope drift detection, bisectable commits, verification gate, runtime QA subagents, and any needed durable-doc updates. On completion, fresh hook-owned QA completion receipts exist and `task_verify` reports PASS. If BLOCKED: stop, report, ask user.

Before entering develop, re-entering after QA/UX FAIL, verifying, or closing,
incorporate explicit user corrections from the conversation. Promote durable
rules directly into PLAN.md or the applicable project documentation; Harness
does not maintain a separate feedback sidecar.

### Phase 4: Verify (QA agent)

Read `doc/harness/manifest.yaml` for project type. Spawn appropriate QA agent(s).
Also spawn applicable UX review agents for user-facing surfaces. UX review is
not a replacement for QA: qa-* proves correctness; ux-* judges whether the
experience is shippable. Claude hooks record subagent lifecycle events in
`RECEIPTS.jsonl`; `task_verify` requires a completed explicit
PASS for every applicable QA lens. A start entry proves delegation only.

**Strategy selection:**
- **MUST spawn qa-browser when** `manifest.qa.browser_qa_supported: true` AND the diff contains any frontend file (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments). Skipping leaves no completed qa-browser receipt and is blocked by `task_close`.
- `desktop_qa_supported: true` → qa-desktop
- `type: api` or diff contains route/endpoint files → qa-api
- `type: cli` or `type: library` → qa-cli
- Multiple types match (fullstack) → spawn relevant agents **in parallel**

**UX strategy selection:**
- frontend/browser UI diff with `browser_qa_supported: true` or `ux_review_supported: true` → ux-browser
- CLI command/help/output/error diff with `ux_review_supported: true` → ux-cli
- API route/schema/error/docs diff with `ux_review_supported: true` → ux-api
- desktop GUI diff with `desktop_qa_supported: true` or `ux_review_supported: true` → ux-desktop

When QA and UX lenses both apply, spawn them in the same parallel batch when
available. Agents return PASS/FAIL/BLOCKED_ENV findings in their final
response. Do not ask them to write critic artifacts. `task_verify` exposes
required lenses, verdicts, and `missing_for_close`; it does not return raw
receipt records or completion summaries.

Order matters: the desktop branch is evaluated before the `type: cli` / `type: library`
fallback so a desktop app declared as `type: cli` still routes to qa-desktop.

Agent spawn template (substitute `<lens>` ∈ {browser, desktop, api, cli}):

**Single lens** (one type matches):

```
Agent(
  name="<task_id>:qa-<lens>",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the <lens> QA agent for <task_id>.
Task dir: <task_dir>
Read ${CLAUDE_PLUGIN_ROOT}/agents/qa-<lens>.md for your full role definition.
Follow it exactly — all four roles (operation, intent, UX/design, runtime).
Return PASS/FAIL/BLOCKED_ENV with concrete findings and evidence. Do not modify files and do not write critic artifacts."
)
```

**Multi-lens fullstack** (two or more types match) — spawn ALL agents in a single assistant message so all starts are hook-recorded:

```
# Issue these N Agent calls in ONE assistant message
Agent(
  name="<task_id>:qa-cli",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the cli QA agent for <task_id>. ... Return PASS/FAIL/BLOCKED_ENV with findings."
)
Agent(
  name="<task_id>:qa-browser",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the browser QA agent for <task_id>. ... Return PASS/FAIL/BLOCKED_ENV with findings."
)
```

After awaiting every QA/UX subagent, run `task_verify`. It computes the verdict
from required ordered review and QA completions in `RECEIPTS.jsonl`; PLAN.md
remains the sole acceptance document.

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
already-running MCP/hook process still reports a pre-install lifecycle gap, keep
the task pending and request a new session; do not write receipts by hand. On
resume, the stateless root installer may be run again after confirming
the diff still has fresh review+QA PASS.

If this run belongs to an active task pack, mark the item closed with
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/task_pack_runner.py close --task <slug>`.
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

Phases completed: plan, develop, verify, close
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
- Schedule pending hygiene as a separate follow-up task; do not mix unrelated
  hygiene cleanup into the just-finished primary task
- If the scheduler returns `run_followup`, continue that task before reporting
  DONE; this is a mandatory continuation, not advisory cleanup
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings: Tier 3 (jsonl) → Tier 2 (patterns/*.md) → Tier 1 (CLAUDE.md)
- Prune promoted entries and stale (>90 day) non-eureka entries

Pipeline failures are housekeeping, not a gate. Auto-runnable follow-up tasks
are different: they must be run or explicitly blocked before DONE.
