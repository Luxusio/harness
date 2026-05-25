---
name: run
description: Orchestrate full development cycle — plan -> develop -> verify -> close.
user-invocable: true
---

# GENERATED-CANDIDATE — hand-ported v1.5 spike from plugin/skills/run/SKILL.md (171L source).
# Source canonical at plugin/skills/run/SKILL.md. v1.5 AC-005 sync engine will replace this
# hand-port with mechanical emission. Lives here only to measure porting friction for
# AC-002 of TASK__dual-runtime-v1.5-spike-and-sync.


Orchestrate the full harness development cycle for a task.

> **Codex runtime notes** (delta from Claude):
> - Claude's `Skill("harness:plan", task_id)` programmatic chain has no Codex equivalent — on Codex, the orchestrator reads each downstream skill's SKILL.md inline and executes its phases as part of the same conversation. Effect is identical (plan -> develop -> verify -> close), but the chain is sequential prose, not tool calls.
> - Claude's `Agent(subagent_type="oh-my-claudecode:executor", ...)` maps to Codex capability-first routing. If the current Codex session exposes `spawn_agent`, use it for independent QA/review and bounded worker tasks. If `spawn_agent` is unavailable, run the role methodology inline, call the same MCP artifact writer, and record a short `Runtime Fallbacks` note only when that fallback replaces an expected independent QA/review path.
> - MCP tool names on Codex use bare form (`task_start`, `task_verify`, `task_close`, `write_critic_qa`) — not Claude-prefixed form. Where this skill mentions a prefixed name, read it as the bare form.
> - `${CLAUDE_PLUGIN_ROOT}` is not injected on Codex. Use `${HARNESS_PLUGIN_ROOT}` (set by the Codex plugin install).
> - AskUserQuestion (Phase 4 FAIL retry) is conversational prose on Codex — emit the question + options, read the reply from the next user turn.

## Codex Subagent Routing

Route from the current session tools.

When `spawn_agent` is available, prefer it for independent review/QA and bounded side work. Use concrete Codex calls like:

```text
spawn_agent {
  agent_type: "default",
  message: "You are the qa-cli lens for <task_id>. Read <task_dir>/PLAN.md, HANDOFF.md, CHECKS.yaml, and changed files. Run focused verification. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with evidence. If you can call write_critic_qa, write lens='cli'; otherwise return the exact verdict transcript for the orchestrator to write.",
  fork_context: true
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

Use inline execution as the fallback for roles that normally benefit from independence. Add a short `Runtime Fallbacks` section when an expected independent QA/review path was replaced by inline verification or a required tool was unavailable. Keep it to: reason, risk, compensating check.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, tiered-learning promotion + pruning pipeline (runs after each task close). Not ported in v1.5 spike; the Codex orchestrator reads the Claude-side sub-file at `plugin/skills/run/self-improvement.md` if/when self-improvement runs.

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On any phase failure: stop, report, ask how to proceed.

### Phase 0: Resume detection

Before creating a new task, check whether this session already has an active
harness task. If an active task exists, call `task_context` for that task and
resume instead of creating a duplicate.

Resume routing:
- PLAN.md missing → Phase 2 Plan.
- PLAN.md exists and HANDOFF.md missing → Phase 3 Develop.
- HANDOFF.md exists and runtime_verdict is not PASS → Phase 4 Verify.
- runtime_verdict is PASS and `missing_for_close` is empty → Phase 5 Close.
- `missing_for_close` names specific artifacts or AC blockers → fix that gate
  and then continue from the corresponding phase.

Only call `task_start` when no active task can be resolved, or when the user
explicitly asks for a new task.

### Phase 1: Start task

```
task_start { slug: "<ARGUMENTS>" }
```

(On Codex MCP this is the bare tool name; Claude uses a runtime-prefixed form.) Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

Read `plugin-codex/internal-skills/plan/SKILL.md` (the v1.5 hand-port; AC-003 spike target) and execute its phases inline, passing `task_id`. The plan skill writes PLAN.md to the task_dir. On BLOCKED: stop and report.

On Codex side the plan skill uses the available runtime surface. When `spawn_agent` or external model routes are available, use them for independent review voices; otherwise run the review methodology inline and record `Runtime Fallbacks` if expected independence was lost. The premise gate becomes a conversational ask.

### Phase 3: Develop

Read `plugin-codex/internal-skills/develop/SKILL.md` and execute its phases, passing `task_id`. The develop skill on Codex is a hand-port of the Claude source (`plugin/skills/develop/SKILL.md`) under the MCP-only-sharing policy (spike-report §3.6) — same canonical-loop methodology, with `Agent` fan-out routed through `spawn_agent` when available, `Skill()` chains rendered as inline-read sub-skill references, and `AskUserQuestion` gates rendered as conversational prose asks. Phase 0 through Phase 8.7 parity is preserved. Develop writes HANDOFF.md + DOC_SYNC.md to the task_dir. On BLOCKED: stop and report.

Multi-lens parallel QA (qa-browser + qa-api in one batch) should use `spawn_agent` when available. Browser MCP verification is availability-gated: if the current Codex session exposes browser tools (for example `chrome_devtools` or a future Playwright MCP), run the qa-browser methodology via subagent when possible or inline when no subagent path exists; if browser verification is required but no browser tool or reachable app exists, write a browser-lens `BLOCKED_ENV` verdict instead of silently falling back to CLI-only QA.

On completion: HANDOFF.md and DOC_SYNC.md exist in task_dir. If BLOCKED: stop, report, ask user.

### Phase 4: Verify (QA — capability-routed on Codex)

Read `doc/harness/manifest.yaml` for project type. On Codex, choose the appropriate QA lens and route it by current capability: `spawn_agent` when available, inline methodology only as fallback. Also route applicable UX review lenses for user-facing surfaces. QA proves correctness in `CRITIC__qa.md`; UX review judges shippability in `CRITIC__ux.md`.

**Strategy selection:**
- **qa-browser** — required when `manifest.qa.browser_qa_supported: true` AND the diff contains frontend files (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments). On Codex, check the actual session tool surface first. If browser tools are available, route the qa-browser lens through `spawn_agent` when available; otherwise read `plugin-codex/agents/qa-browser.md` and run that methodology inline, including real page navigation/interactions/screenshots where the tools support it. Call:
  ```
  write_critic_qa {
    task_id: "<task_id>",
    lens: "browser",
    verdict: "PASS" | "FAIL" | "BLOCKED_ENV",
    summary: "<one-line>",
    transcript: "<browser evidence>",
    manual_ux_verification: "<non-empty description of the pages, viewports, and interactions actually checked>"
  }
  ```
  If browser QA is required but no browser tool is available, the dev server cannot be reached, or a required browser setup is impossible, call the same browser lens with `verdict: "BLOCKED_ENV"` and a transcript naming the exact missing condition. Keep browser-required close evidence on the browser lens.
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
read `plugin-codex/agents/ux-<lens>.md` and call:

```text
write_critic_ux { lens: "cli|api|browser|desktop", verdict: "PASS" | "FAIL" | "BLOCKED_ENV", summary, transcript }
```

`task_close` blocks applicable user-facing work until the required
`CRITIC__ux.md` lens section is PASS. `write_critic_ux` does not update
`runtime_verdict` and does not auto-promote functional ACs.

Order: desktop branch before `type: cli` fallback so a desktop app declared as `type: cli` still routes to qa-desktop.

QA subagent pattern on Codex:

```text
spawn_agent {
  agent_type: "default",
  message: "You are the qa-<lens> lens for <task_id>. Read <task_dir>/PLAN.md, HANDOFF.md, CHECKS.yaml, and plugin-codex/agents/qa-<lens>.md. Follow all four roles. Do not modify files. Return PASS/FAIL/BLOCKED_ENV with command/browser evidence. If you can write the verdict, call write_critic_qa with lens='<lens>'; otherwise return the transcript for the orchestrator to write.",
  fork_context: true
}
```

When the QA lens returns PASS for the task as a whole, call `write_critic_qa`
to record evidence/runtime verdict, then run `task_verify` with
`reconcile_acs: true` so open CHECKS.yaml items are promoted with
CRITIC__qa.md evidence. The verify step only promotes `status: open` and only
when the effective merged verdict is PASS; failed/deferred ACs still require
explicit `update_checks.py` handling.

QA inline fallback pattern on Codex:

```
# Read qa-browser, qa-cli, or qa-api agent prompt from the Codex plugin tree
cat ${HARNESS_PLUGIN_ROOT}/agents/qa-cli.md   # or qa-api.md / qa-browser.md
# Follow the four-roles checklist inline (operation, intent, UX/design, runtime)
# Run the verification commands the agent prompt prescribes
# Call:
write_critic_qa { lens: "cli|api|browser", verdict: "PASS" | "FAIL" | "BLOCKED_ENV", summary, transcript, manual_ux_verification? }
```

When inline fallback replaces expected independent QA, add `Runtime Fallbacks` to HANDOFF with reason, risk, and compensating check.

After write_critic_qa, check the returned merged `runtime_verdict`:
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

Before closing, capture the final project health score:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py 2>&1 || true
```

Store the printed score for inclusion in the completion report. The script auto-appends to `doc/harness/health-history.jsonl`.

### Phase 5: Close

```
task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: emit completion report, then run self-improvement pipeline (see `self-improvement.md` in the Claude tree).

## Completion Report

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
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings: Tier 3 (jsonl) -> Tier 2 (patterns/*.md) -> Tier 1 (CLAUDE.md or AGENTS.md)
- Prune promoted entries and stale (>90 day) non-eureka entries

Pipeline is housekeeping, not a gate. On failure: log warning and continue.

---
