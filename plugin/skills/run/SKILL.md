---
name: run
description: Orchestrate full development cycle — plan → develop → verify → close.
argument-hint: <task-slug-or-description>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent, Skill, AskUserQuestion, mcp__harness__task_start, mcp__harness__task_context, mcp__harness__task_verify, mcp__harness__task_close
---

Orchestrate the full harness2 development cycle for a task.

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins.
On any phase failure: stop, report the failure to the user, ask how to proceed.

### Phase 1: Start task

```
mcp__harness__task_start { slug: "<ARGUMENTS>" }
```

Store the returned `task_dir` and `task_id` for all subsequent phases.
Report: task created/resumed, task_dir path.

### Phase 2: Plan

Invoke the plan skill:

```
Skill("harness:plan", "<task_id>")
```

The plan skill runs its full review pipeline and writes PLAN.md.
On completion: PLAN.md exists in task_dir.

If the plan skill reports BLOCKED: stop and report to user.

### Phase 3: Develop

Invoke the develop skill:

```
Skill("harness:develop", "<task_id>")
```

The develop skill reads PLAN.md, implements changes, runs plan completion audit, scope drift detection, bisectable commits, verification gate, runtime QA subagents, DOC_SYNC generation, and distilled change doc. On completion: HANDOFF.md and DOC_SYNC.md exist in task_dir.

If develop skill reports BLOCKED: stop, report, ask user.

### Phase 4: Verify (QA agent)

Read `doc/harness/manifest.yaml` to determine project type. Spawn the appropriate QA agent:

**Browser QA (browser_qa_supported=true):**

```
Agent(
  name="<task_id>:qa-browser",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the browser QA agent for <task_id>.
Task dir: <task_dir>
Read plugin/agents/qa-browser.md for your full role definition.
Follow it exactly — all four roles (operation, intent, UX, runtime).
Call mcp__harness__write_critic_runtime with verdict, summary, and full transcript."
)
```

**API QA (type=api or API endpoints in diff):**

```
Agent(
  name="<task_id>:qa-api",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the API QA agent for <task_id>.
Task dir: <task_dir>
Read plugin/agents/qa-api.md for your full role definition.
Follow it exactly — all four roles (operation, intent, design, runtime).
Call mcp__harness__write_critic_runtime with verdict, summary, and full transcript."
)
```

**CLI QA (type=cli or type=library):**

```
Agent(
  name="<task_id>:qa-cli",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the CLI QA agent for <task_id>.
Task dir: <task_dir>
Read plugin/agents/qa-cli.md for your full role definition.
Follow it exactly — all four roles (operation, intent, UX, runtime).
Call mcp__harness__write_critic_runtime with verdict, summary, and full transcript."
)
```

**Strategy selection:**
- `browser_qa_supported: true` → qa-browser
- `type: api` or diff contains route/endpoint files → qa-api
- `type: cli` or `type: library` → qa-cli
- Multiple types match (e.g., fullstack) → spawn relevant agents **in parallel**

After completion, check runtime_verdict:
- **PASS**: proceed to Phase 5 (Close).
- **FAIL**: report findings to user. Ask:
  ```
  QA returned FAIL. Findings: <summary>
  A) Send back to developer — fix the issues
  B) Override — accept current state (requires justification)
  C) Abort task
  ```
  If A: return to Phase 3 with the QA findings as additional context.
  Retry limit: 3 cycles. After 3 FAILs, stop and report.

### Phase 5: Close

```
mcp__harness__task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: emit completion report.

## Completion Report

```
DONE

Task:    <task_id>
Status:  closed
Dir:     <task_dir>

Phases completed: plan, develop, verify, close
Runtime verdict:  PASS
Files changed:    <count>
Doc:              doc/changes/<date>-<slug>.md
```

## Retry Tracking

Track retry counts per phase:
- Phase 3 (develop): max 3 retries after runtime FAIL

After max retries: stop, emit report with DONE_WITH_CONCERNS.

## Error Handling

On any agent timeout or crash:
1. Report what happened
2. Check current state via `task_context`
3. Ask user: retry / skip / abort

Never silently continue past a failure.

## Self-Improvement

After each task completes (regardless of outcome), check for harness improvement signals.

### Signals to detect

During the full cycle, watch for these friction patterns:

1. **Wrong verification strategy** — manifest says "library" but critic needed browser QA. Or manifest says "web_app" but no dev server command was stored.
2. **Missing manifest fields** — test_command is wrong, build_command is missing, entry_url is incorrect.
3. **Repeated critic failures** — same type of failure across 2+ tasks (e.g., "missing test coverage" every time → test framework needs bootstrap).
4. **Phase friction** — a phase consistently takes 3+ retry cycles. The plan or develop methodology may need adjustment.
5. **New project patterns** — the project evolved (added frontend, changed test framework, new port) but manifest is stale.

### Log improvements

After task close, if any signals were detected:

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
# One line per signal detected during this task cycle
echo '{"ts":"'"$_TS"'","type":"harness-improvement","source":"run","key":"SHORT_KEY","insight":"DESCRIPTION","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Auto-fix during close

If a signal has a clear fix AND the fix is safe:

1. **Stale manifest field** — update `doc/harness/manifest.yaml` with the correct value discovered during the task. E.g. test_command was wrong → fix it.
2. **Missing dev_command** — if browser QA was needed and dev server was discovered, store it.
3. **Wrong project type** — if the critic had to switch strategies, update the manifest.

Before auto-fixing, report to user:
```
Harness improvement: <what was wrong> → <what was fixed>
```

If the fix is ambiguous or risky, log the signal only. Do NOT modify manifest without clear evidence.

### Signals feed into setup

The plan skill reads `doc/harness/learnings.jsonl` at Phase 0.1.5. The setup skill reads it during repair mode. This means improvement signals compound across tasks — the harness gets smarter about the project over time.

### Write learnings as docs (primary)

Most learnings go directly into readable docs under `doc/harness/patterns/`:

```
doc/harness/patterns/
├── testing.md          # Test conventions, framework quirks, coverage patterns
├── build.md            # Build commands, ordering constraints, env requirements
├── verification.md     # Verification strategy, dev server setup, browser QA tips
└── architecture.md     # Module boundaries, dependency patterns, known gotchas
```

**Rule: write a doc immediately when you discover something.** Don't wait for repetition.

Each doc starts with a summary table and follows with concrete details:

```markdown
# <Topic> Patterns

| Pattern | Discovered | Source |
|---------|------------|--------|
| <pattern> | <date> | TASK__<id> |

## <Pattern Name>

<context>

**Why:** <reason this matters>
**How to apply:** <what to do differently>
```

If the doc already exists, append to it. Do not overwrite.

**When to write:**
- Any discovery that would save 5+ minutes in a future session → write a doc.
- Build quirks, env var requirements, ordering constraints, port numbers, test framework specifics.
- After every task close, check if anything worth documenting was learned.

### Tiered learning storage

```
CLAUDE.md                    # Tier 1: loaded every session. Frequent, critical facts.
doc/harness/patterns/*.md    # Tier 2: detailed patterns. Read when relevant.
doc/harness/learnings.jsonl  # Tier 3: session/user-specific, transient only.
```

**Tier 1 — CLAUDE.md** (every session loads this):
- Test command: `bun test` (not `npm test`)
- Dev server: `bun run dev` on port 3000
- Build quirks: must run X before Y
- Project-specific env vars or config requirements
- Anything referenced in 2+ tasks → promote to CLAUDE.md

**Tier 2 — doc/harness/patterns/** (read when relevant):
- Detailed pattern descriptions with examples
- Architecture decisions and their rationale
- Verification strategy specifics
- Error rescue procedures

**Tier 3 — learnings.jsonl** (session-specific only):
- User preferences, temporary state
- Signals that need aggregation before becoming a doc
- Transient data that doesn't warrant a standalone file

### Promotion: Tier 3 → Tier 2 → Tier 1

After each task close:
1. If a `learnings.jsonl` entry matches something from a previous task → promote to Tier 2 doc.
2. If a Tier 2 pattern doc is referenced during 2+ tasks → promote the key fact to CLAUDE.md.
3. CLAUDE.md entries should be one-liners. Details stay in the pattern doc.

Example promotion:
```
# learnings.jsonl (Tier 3)
{"key":"test-command","insight":"bun test, not npm test","task":"TASK__001"}

# doc/harness/patterns/testing.md (Tier 2, after 2nd occurrence)
| test-command | 2026-04-14 | observed |
## Test command is `bun test`
This project uses Bun, not npm. All test commands use `bun test`.

# CLAUDE.md (Tier 1, after 3rd reference)
## Testing
Test command: `bun test` (Bun runtime, not npm)
```

Non-blocking — if any promotion step fails, the data is still at its current tier.
