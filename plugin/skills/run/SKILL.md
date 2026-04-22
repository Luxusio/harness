---
name: run
description: Orchestrate full development cycle — plan → develop → verify → close.
argument-hint: <task-slug-or-description>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent, Skill, AskUserQuestion, mcp__harness__task_start, mcp__harness__task_context, mcp__harness__task_verify, mcp__harness__task_close
---

Orchestrate the full harness development cycle for a task.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, tiered-learning promotion + pruning pipeline (runs after each task close).

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On any phase failure: stop, report, ask how to proceed.

### Phase 1: Start task

```
mcp__harness__task_start { slug: "<ARGUMENTS>" }
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

The develop skill reads PLAN.md, implements changes, runs plan completion audit, scope drift detection, bisectable commits, verification gate, runtime QA subagents, DOC_SYNC generation, and distilled change doc. On completion: HANDOFF.md and DOC_SYNC.md exist in task_dir. If BLOCKED: stop, report, ask user.

### Phase 4: Verify (QA agent)

Read `doc/harness/manifest.yaml` for project type. Spawn appropriate QA agent(s).

**Strategy selection:**
- `browser_qa_supported: true` → qa-browser
- `type: api` or diff contains route/endpoint files → qa-api
- `type: cli` or `type: library` → qa-cli
- Multiple types match (fullstack) → spawn relevant agents **in parallel**

Agent spawn template (substitute `<lens>` ∈ {browser, api, cli}):

```
Agent(
  name="<task_id>:qa-<lens>",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the <lens> QA agent for <task_id>.
Task dir: <task_dir>
Read ${CLAUDE_PLUGIN_ROOT}/agents/qa-<lens>.md for your full role definition.
Follow it exactly — all four roles (operation, intent, UX/design, runtime).
Call mcp__plugin_harness_harness__write_critic_qa with verdict, summary, and full transcript."
)
```

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
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py 2>&1 || true
```

Store the printed score for inclusion in the completion report. The script auto-appends to `doc/harness/health-history.jsonl`.

### Phase 5: Close

```
mcp__harness__task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: emit completion report, then run self-improvement pipeline (see `self-improvement.md`).

## Completion Report

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

Never silently continue past a failure.

## Self-Improvement (post-close)

After every task close, run the pipeline in `self-improvement.md`:
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings: Tier 3 (jsonl) → Tier 2 (patterns/*.md) → Tier 1 (CLAUDE.md)
- Prune promoted entries and stale (>90 day) non-eureka entries

Pipeline is housekeeping, not a gate. On failure: log warning and continue.
