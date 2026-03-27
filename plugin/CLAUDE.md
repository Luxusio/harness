# harness — Durable Knowledge Operating System

You are running with harness, a repo-local operating system with REQ/OBS/INF durable memory and mandatory critic gates.

## Runtime loop

1. **Classify intent** → feature, bugfix, refactor, docs, investigate, structure, maintain, answer
2. **Load scoped context** → doc/CLAUDE.md registry, relevant root CLAUDE.md files
3. **Route to lane** → plan → critic-plan → developer/writer → critic-runtime/critic-write → sync
4. **Validate** → every code change needs critic-runtime, every doc change needs critic-write
5. **Sync memory** → create/update REQ/OBS/INF notes, update indexes
6. **Summarize** → Changed, Validated, Recorded, Unknown, Follow-up

## Specialist agents

| Agent | Role |
|-------|------|
| `harness:developer` | Code implementation after plan-critic PASS |
| `harness:writer` | REQ/OBS/INF notes and documentation |
| `harness:critic-plan` | Validate PLAN.md before implementation |
| `harness:critic-runtime` | Runtime verification for code changes |
| `harness:critic-write` | Doc/note hygiene validation |
| `harness:critic-structure` | Structure change governance |

## Durable knowledge rules

- REQ: explicit human requirements only
- OBS: directly observed/verified facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF verified → create OBS + superseded_by link

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No code task closure without critic-runtime PASS/BLOCKED_ENV
- No doc task closure without critic-write PASS
- No root expansion without critic-structure PASS
- Prefer existing roots over new structure
- If `doc/CLAUDE.md` is missing, recommend `/harness:setup`
