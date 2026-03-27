# harness — Durable Knowledge Operating System

You are running with harness, a repo-local operating system with REQ/OBS/INF durable memory and mandatory critic gates.

## Runtime loop

1. **Classify intent** → answer, or repo-mutating work
2. **Load scoped context** → root `CLAUDE.md` registry, `doc/common/CLAUDE.md`, relevant root CLAUDE.md files
3. **Check initialization** → `.claude/harness/manifest.yaml` must exist for gated workflows
4. **Route to lane**:
   - `answer/explain` → direct response
   - everything that mutates the repo → common mutate-repo loop
   - `maintain` → maintenance loop plus critic-document for semantic changes
5. **Mutate-repo loop**:
   - REQUEST.md → PLAN.md (contract) → critic-plan PASS → developer/writer → QA__runtime.md → critic-runtime PASS → persistence (TASK_STATE.yaml, HANDOFF.md) → DOC_SYNC.md → critic-document PASS → RESULT.md → close
6. **Sync memory** → create/update REQ/OBS/INF notes, update indexes
7. **Summarize** → Changed, Validated, Recorded, Unknown, Follow-up

## Specialist agents

| Agent | Role |
|-------|------|
| `harness:developer` | Code implementation after plan-critic PASS |
| `harness:writer` | REQ/OBS/INF notes and documentation |
| `harness:critic-plan` | Validate PLAN.md contract before implementation |
| `harness:critic-runtime` | Runtime verification for code changes |
| `harness:critic-document` | Doc/note hygiene, structure governance, registry sync |

## Durable knowledge rules

- REQ: explicit human requirements only
- OBS: directly observed/verified facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF verified → create OBS + superseded_by link

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No code task closure without critic-runtime PASS
- No doc task closure without critic-document PASS
- No root expansion without critic-document PASS
- `BLOCKED_ENV` leaves task open with `status: blocked_env` — never closes the task
- Prefer existing roots over new structure
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`
