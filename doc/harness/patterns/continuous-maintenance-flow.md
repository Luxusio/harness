# Continuous Maintenance Flow

Standalone maintenance is a fallback, not the primary way harness knowledge
should improve. Durable maintenance should happen during the same task that
discovers the friction whenever the fix is safe and scoped.

## Principle

When a task discovers repeated harness friction, the agent should either fix it
in committed artifacts before close or ask the user where the follow-up belongs.
Private memory and gitignored queues are staging only; they are not shared
maintenance.

## Maintain Responsibility Map

| Former maintain responsibility | Continuous home | Required behavior |
| --- | --- | --- |
| Tier C contract drift confirmation | Close-time Self-Healing Candidates or setup/update task | Ask one explicit user question before applying or deferring a risky contract change. Deferred items must name `user_decision`, `reason`, and `proposed_artifact` or `proposed_task`. |
| Runbook candidate approval | Close-time self-healing for setup/runtime discoveries | If the run command was proven during the task, update committed runbook/docs/scripts. If it is too broad, ask the user whether to create a separate task. |
| Pending state rewrite/reporting | Harness close gates and prompt memory | The close gate validates committed HANDOFF evidence. Prompt memory surfaces remaining pending items at the next session. |

## Close-Time Self-Healing

Every HANDOFF should include `Self-Healing Candidates` with one of these
statuses:

- `none`: no durable improvement was discovered.
- `applied`: this task changed a committed skill, script, test, workflow,
  manifest, or durable doc to prevent recurrence.
- `deferred`: useful, but too large or risky for the current task. The agent
  must ask the user before close and record `user_decision`, `reason`, and a
  `proposed_artifact` or `proposed_task`.
- `rejected`: investigated and intentionally not automated because it is
  one-off, noisy, or not worth the maintenance cost.

This is the replacement path for most historical `harness:maintain` usage:
discover friction, classify it, fix it when scoped, or ask the user before
deferring.

## Runtime Notes

Claude should use `AskUserQuestion` for large/risky deferrals. Codex should use
`request_user_input` when available; otherwise it must ask in conversation and
wait for the user's reply before recording a deferred self-healing item.
