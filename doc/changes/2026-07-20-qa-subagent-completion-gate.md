---
date: 2026-07-20
task: TASK__harden-qa-subagent-completion-gate
tags: [qa, subagent, verification, codex, hooks]
---

# QA subagent completion now gates task close

The previous close authority treated any hook-owned subagent start receipt as
runtime PASS. That proved only that `spawn_agent` was invoked. A still-running,
failed, blocked, generic explorer, or never-awaited subagent could therefore
let a task close.

The durable contract is now:

- Strict-compliance repositories require `task_start` before the first source
  write. Hooks do not guess intent and silently create tasks from arbitrary
  user prose; the write gate redirects the agent to the explicit MCP action.
- Codex guidance checks deferred tools such as `ALL_TOOLS` before declaring
  `spawn_agent` unavailable. Structured `task_name` is accepted as the QA lens
  when Codex does not expose an `agent_type` argument.
- While verification is pending, every UserPromptSubmit injection includes the
  executable sequence: discover visible/deferred `spawn_agent`, select lenses,
  spawn and await them, require an explicit verdict, then call `task_verify`.
- A start receipt proves delegation only.
- Every QA lens selected from manifest type and touched paths must produce a
  lifecycle completion receipt with an explicit `VERDICT: PASS`.
- A QA FAIL, BLOCKED_ENV, missing verdict, missing required lens, or source edit
  after QA prevents close.
- Claude records completion from `SubagentStop`. Codex records completion from
  supported `wait_agent` PostToolUse payloads only when the wait target matches
  a prior task-local QA start receipt. Unmatched or cross-task waits are
  ignored. If a Codex runtime omits lifecycle payloads, verification remains
  PENDING instead of guessing PASS.
- Completion receipts include the repository HEAD SHA as correlation metadata
  and retain microsecond timestamps. Freshness is enforced against
  baseline-filtered task paths and their modification times because QA may
  legitimately review an uncommitted worktree.

This deliberately does not restore the old blanket ban on inline test runners.
Targeted inline tests remain useful during development. Independent QA and the
completion-aware close gate supply the required final review boundary.
