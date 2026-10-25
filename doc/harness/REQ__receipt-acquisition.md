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
Retired task/Goal aliases cannot bind. Only successful open/context actions may bind; check/plan/dispatch/close/block,
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

Native ancestry authentication must finish before acquiring the shared
cross-workspace session and task-receipt locks. Publication may reuse only an
already verified parent-spawn proof whose full file generation and trusted
path still match. A stale or missing proof must fail without rereading the
parent history while those locks are held. This restriction is private to the
registration invocation and is cleared on every exit, including interruption.
Current native metadata, task/run ownership and conflict fences remain checked.
An unchanged complete descriptor generation can validate the just-read prefix
without reading the entire parent a second time. A changed generation still
requires the existing prefix-integrity check; append alone is not trusted.

This is derived from reliable acquisition above, following the user's
2026-10-25 correction, “그것도 원인 규명해야지”. The observed long-lived parent
rollout was about 104 MiB and required 3.7–4.3 seconds to authenticate. The
previous registration path repeated that work inside all-workspace locks and
a two-second hook budget. Preparation and publication therefore need separate,
finite allowances within the installed hook timeout. A timeout or changed
proof remains an explicit failure; increasing a timeout alone does not repair
the repeated locked read. No result from an unregistered past execution gains
attestation through this repair.

A parent can legitimately append after preparation and before publication.
Only the resulting typed native-ancestry-pending condition permits one fresh
full authentication outside all publication locks, followed by one final
publication attempt. Native MCP, successful task-result binding and standalone
restore share this bounded owner. Nested restore forwards the condition to its
outer owner; it never authenticates while that owner's locks are held. Restore
retains the originally resolved task/run across retry, and each publication
rechecks its ownership. Invalid proof, task conflicts and other errors do not
permit arbitrary retry. Attempt diagnostics describe only the terminal outcome;
a successful retry clears the first attempt's transient failure.

Known ceiling: At most two prepared-publication attempts share the original
absolute caller deadline. Each authentication is capped at 6 seconds; the total
allowance remains 6 seconds plus the caller's publication allowance (8 seconds
for native MCP, or the smaller remaining hook allowance). Retrying grants no
extra time. Repeated parent changes or exhausted time fail explicitly without
reading full history under locks or promoting historical results.

## Verification

Exercise a real stdio MCP open with process-owned thread identity; confirm the
persisted task/run matches TASK.json and registrations() admits it. Observe
future genuine native review and QA starts/completions through the live MCP
manager. Installer matcher tests must feed the actual hook result parser and
reject nonbinding actions. Existing watcher/worktree/security regression tests
must continue to pass.
