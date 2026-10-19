# Reliable Codex receipt acquisition

## User-sourced requirement

Origin: user_explicit. Source: follow-up messages in this task, “증거수집도 버그 고쳐” and “이거 자꾸 안되더라?” Repair recurring evidence collection failures.

## Derived implementation constraints

These mechanisms are implementation choices, not additional user requests.

Successful registration must retain the exact task and run from the current
session binding even when its caller adds an extra binding validator. Reporting
registration success while publishing a blank generation leaves the manager
unable to start its worker. The native thread-bound MCP eager path and native
PostToolUse binding must produce registrations accepted by the same reader.

Installed PostToolUse routing includes the exact public `wbs` tool names.
Only successful open/context actions may bind; check/plan/dispatch/close/block,
foreign tools and failed results cannot reactivate or redirect the coordinator.

A shared MCP host without CODEX_THREAD_ID needs an actual native PostToolUse
binding event. A nested code-mode call may not deliver that hook. For explicit
runtime repair, a real thread-scoped stdio MCP process can inherit the host's
unchanged CODEX_THREAD_ID and use existing eager binding. Keep its manager alive
while fresh native subagents run. Do not substitute CODEX_SESSION_ID (a child
may inherit its parent's value), global hints, caller-selected session IDs,
synthetic hook events or handwritten receipts.

The existing lifecycle parser, native metadata, activity/output correlation,
immutable future-only offset and review-before-QA completion gate remain the
receipt authorities. Historical unrecorded results are not backfilled. Tests
cover the real production callback route, not just a fake successful registrar.

## Verification

Exercise a real stdio MCP open with process-owned thread identity; confirm the
persisted task/run matches TASK.json and registrations() admits it. Observe
future genuine native review and QA starts/completions through the live MCP
manager. Installer matcher tests must feed the actual hook result parser and
reject nonbinding actions. Existing watcher/worktree/security regression tests
must continue to pass.
