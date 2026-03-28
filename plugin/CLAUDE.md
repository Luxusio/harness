# harness v3 — Completion Firewall

You are running with harness, a completion firewall for AI-assisted repository work.

The plugin gates task closure behind verified critic verdicts. It also invalidates stale verdicts when files change after a PASS, and prevents premature stop when tasks are open.

## The loop

```
receive → classify (answer | mutate-repo) → plan → critic-plan PASS → execute → critic-runtime PASS → docs sync → critic-document PASS (if docs changed) → close
```

## Hook gates

| Hook | Behavior |
|------|----------|
| `SessionStart` | Load context, show open tasks |
| `TaskCreated` | Initialize TASK_STATE.yaml, HANDOFF.md, REQUEST.md |
| `TaskCompleted` | **BLOCK** (exit 2) unless all required verdicts PASS |
| `SubagentStop` | Warn if expected artifacts missing |
| `Stop` | **BLOCK** (exit 2) if open tasks remain |
| `FileChanged` | Invalidate PASS verdicts to pending |
| `PostCompact` | Re-inject open task summary |
| `SessionEnd` | Record final session state |

All hook scripts parse stdin JSON and use exit 2 for blocking.

## Hard gates (TaskCompleted)

| Requirement | When |
|-------------|------|
| TASK_STATE.yaml | Always |
| PLAN.md + CRITIC__plan.md PASS | Always |
| HANDOFF.md | Always |
| CRITIC__runtime.md PASS | Repo-mutating tasks (mutates_repo != false) |
| CRITIC__document.md PASS | When DOC_SYNC.md exists or doc files changed |
| blocked_env cannot close | Always |

## Verdict invalidation

When files change after a critic PASS:
- `runtime_verdict` resets to `pending`
- `document_verdict` resets to `pending` (if doc files changed)

This prevents stale PASS from allowing task closure after code changes.

## Specialist agents

| Agent | Role |
|-------|------|
| `harness:developer` | Generator — code implementation, updates HANDOFF.md |
| `harness:writer` | Generator — creates/updates notes, writes DOC_SYNC.md |
| `harness:critic-plan` | Evaluator — plan contract validation |
| `harness:critic-runtime` | Evaluator — runtime verification with evidence |
| `harness:critic-document` | Evaluator — doc validation, DOC_SYNC accuracy |

## Task state model

```yaml
task_id: TASK__<slug>
status: created | planned | plan_passed | implemented | qa_passed | docs_synced | closed | blocked_env | stale | archived
lane: build | debug | verify | refactor | docs-sync | investigate
mutates_repo: true | false | unknown
plan_verdict: pending | PASS | FAIL
runtime_verdict: pending | PASS | FAIL | BLOCKED_ENV
document_verdict: pending | PASS | FAIL | skipped
blockers: []
updated: <ISO 8601>
```

## Lanes

| Lane | When |
|------|------|
| `answer` | Pure question — short-circuit, no task folder |
| `build` | Feature addition |
| `debug` | Bug investigation + fix |
| `verify` | Test/QA/validation |
| `refactor` | Structural change |
| `docs-sync` | Documentation update only |
| `investigate` | Research, may transition |

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No close without required critic PASS
- `blocked_env` leaves task open — never closes
- Verdict invalidation on file changes — stale PASS does not count
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`
