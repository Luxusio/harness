# REQ - Process Plain Request Task Routing

status: accepted

## Intent

Harness must not require users to invoke native `/goal` before every
repo-mutating request. Native Goal remains the preferred public container for
explicit, broad, or long-running objectives, but a plain user request is enough
for the agent to recognize that harness task machinery is needed and open or
resume a task.

Hooks may provide reminders and context, but they do not create tasks
automatically. The agent owns the routing decision and calls the MCP task tools
when the request needs the canonical plan, develop, verify, and close loop.

When the user explicitly invokes or approves a harness repo-mutating workflow,
that approval includes authorization to use the subagents required by the
workflow's verification and review gates. This follows the Codex plugin pattern
where explicit invocation of a workflow authorizes the subagent phases required
by that workflow.

## Observable Behavior

- When a user asks for a feature, fix, refactor, behavior change, or durable
  process/doc change without typing `/goal`, the agent may call `task_start` or
  `task_context` directly to create or resume a harness task.
- When native Goal context is active, the agent syncs it with `goal_start` and
  attaches/resumes child tasks through `goal_add_task` and `goal_next_task`.
- The agent must not ask the user to re-submit the same request as `/goal` when
  the work is already clear enough to route.
- Hook scripts must not auto-create task directories from `UserPromptSubmit`.
  They may inject guidance such as "plain mutating request? task_start" and may
  record feedback for an already active task.
- Read-only questions and explanations still answer directly without opening a
  task.
- Plan-first, scope-lock, verification, and close gates still apply to any task
  opened from a plain request.
- Explicit user invocation or approval of a harness repo-mutating workflow, such
  as "use harness", "run/continue/close the harness task", native `/goal`, or a
  clear approval to proceed with a harness task, authorizes required
  verification/review subagents for that workflow.
- This workflow authorization does not apply to read-only answers or ordinary
  non-harness work.

## Acceptance Signals

- Runtime routing docs distinguish "native Goal preferred for explicit goals"
  from "plain repo-mutating request can open a harness task."
- Prompt/gate guidance tells the agent to open or resume a harness task when a
  plain mutating request needs the loop, without implying that hooks create the
  task.
- Codex and Claude prompt surfaces describe the same routing behavior.
- Tests guard against regressing to native `/goal` as the only repo-mutating
  entry point.
- Tests guard that Codex prompt surfaces state the workflow-level subagent
  authorization rule.

## Verification Cues

- `tests/test_skill_visibility.py` checks the user-visible routing contract.
- `tests/test_prompt_memory.py` checks the no-active-task reminder includes both
  native goal sync and plain-request task start guidance.
- `tests/test_harness_mcp_server.py` checks MCP initialize instructions mention
  plain-request task intake.
- `tests/test_codex_run_subagent_routing.py` checks the Codex run/develop
  prompts document workflow-level subagent authorization.

## Non-Goals

- This does not remove native Goal or Goal child-task queues.
- This does not make hooks call `task_start` automatically.
- This does not expose internal `run`, `plan`, `develop`, or review skills as
  user-invocable commands.
- This does not permit source writes before planning or skip verification.

## Source

- created: 2026-06-24
- source: user feedback that plain requests should let the agent recognize the
  need for a harness task and create it, while hooks remain passive.
