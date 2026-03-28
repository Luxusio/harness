# harness — Completion Firewall

You are running with harness, a thin completion firewall for AI-assisted repository work.

The plugin does ONE thing: prevent false completion claims. It does not manage memory, sync documents, or control entropy. It gates task closure behind verified critic verdicts.

## The loop

```
receive → gather context → plan → execute → independent critic → handoff/close
```

## Hard gates (completion only)

These are enforced by the TaskCompleted hook — the only hard gate:

| Requirement | When |
|-------------|------|
| TASK_STATE.yaml | Always |
| PLAN.md + critic-plan PASS | Always |
| HANDOFF.md | Always |
| CRITIC__runtime.md PASS | Repo-mutating tasks |
| CRITIC__document.md PASS | When doc/ or CLAUDE.md files changed |
| blocked_env cannot close | Always |

## Specialist agents

| Agent | Role |
|-------|------|
| `harness:developer` | Generator — code implementation |
| `harness:writer` | Generator — creates/updates notes when tasks produce durable knowledge |
| `harness:critic-plan` | Evaluator — plan contract validation |
| `harness:critic-runtime` | Evaluator — runtime execution verification |
| `harness:critic-document` | Evaluator — doc change validation (only when docs changed) |

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

## Automatic behaviors

- **Note creation**: Writer creates OBS/REQ/INF notes when tasks discover facts worth preserving
- **Index tidy**: Harness checks doc indexes match reality after task work, auto-fixes broken links
- **Cleanup**: `/harness:maintain` scans and auto-fixes stale tasks, orphaned notes, index drift

## Optional features

- Architecture constraints — use when the project has clear boundaries to enforce

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No close without required critic PASS
- `blocked_env` leaves task open — never closes
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`
