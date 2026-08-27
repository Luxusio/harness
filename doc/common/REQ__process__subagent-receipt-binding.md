# REQ process subagent receipt binding

summary: every hook-observed subagent stop for the active task records exactly one completion receipt
status: accepted
updated: 2026-08-27
freshness: current
confidence: high
kind: process
invalidated_by_paths:
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/hooks/hooks.json

## Expected behavior

A subagent that starts and stops under an active harness task produces exactly
two `RECEIPTS.jsonl` entries: one `started`, one `completed`. Starts and
completions pair one-to-one. An unpaired `started` means either the agent is
still running or the harness declined the stop — never that a completed agent
went unrecorded.

This is load-bearing rather than cosmetic. `task_verify` derives
`runtime_verdict` from ordered hook-owned start/completion pairs (C-14), so a
completion the harness declines is indistinguishable from a review that never
happened. A loss rate anywhere above zero makes PASS a matter of luck, and at
the observed rate it made PASS unreachable.

## Observed defect (2026-08-27)

`TASK__bash-guard-unresolved-path-allow` accumulated **41 starts, 32
completions, 9 unpaired starts, 0 orphan completions**, across seven review
rounds and two days. Five reviewer agents delivered complete findings that were
acted on and committed; none recorded a completion.

Root cause: `subagent_lifecycle.py` scanned the subagent transcript for a start
attachment with `hookName == "SubagentStart"` carrying
`content == ["Agent <type> started (<agentId>)"]`, and skipped
`SubagentStart:<matcher>` attachments as duplicates "written alongside the
canonical attachment". The runtime in use emits **only** the matcher-qualified
form — a hook-execution record with `content: ''` plus
`command`/`stdout`/`stderr`/`exitCode`/`durationMs` — and no canonical
companion. The scan skipped the sole start line and rejected at
`no-canonical-start-attachment`.

## Requirements

- Provenance checks are written against the transcript shape the runtime
  **actually emits**, verified against a captured transcript. An assumed schema
  is not evidence, and a comment asserting what a runtime emits is a claim that
  needs checking like any other.
- Both start shapes bind: the canonical identity banner, and a matcher-qualified
  `SubagentStart:<agent-type>` hook record. Accepting an additional *shape* is
  not accepting an unverified one — the binding line must still carry this
  `agentId`, this `sessionId`, and a timestamp at or after the task run start.
- The identity bar attaches to whichever line actually binds. A qualified line
  may omit its own `agentId` while a canonical attachment supplies identity;
  when the qualified line *is* the binding one, its `agentId` is mandatory.
  (The tolerance and the bar were both fixes for real outages, in opposite
  directions.)
- Rejections stay fail-closed and keep their `provenance_reason`. Every reason
  code keeps a test, so widening acceptance cannot silently retire a check.
- A declined stop is logged with its reason. `background_hook:binding-miss`
  records in `doc/harness/learnings.jsonl` are the diagnostic surface —
  note the field is `source`, not `key`.

## Diagnosis notes

`RECEIPTS.jsonl` has three states, not two. Absent means hooks are not running
at all; partial means some path is failing; **declined** means the hooks ran,
saw the stop, and refused to bind it. Declined is the only one that leaves a
`binding-miss` breadcrumb, and it is invisible if you only compare receipt
counts to expectations.

Do not diagnose from `hook_tree_health.py` alone: it reads the registered
`installPath` from `installed_plugins.json`, which can point at a cache tree
(`plugins/cache/harness/harness/<version>`) while the live hooks run from a
different directory. It reported `RECEIPT_HOOKS_UNAVAILABLE` throughout this
outage while receipts were in fact being written and then declined.

## Never

Receipts are never hand-written or retroactively created, under any
circumstances, including to unblock a task whose work is genuinely complete. A
fabricated receipt makes `task_verify` pass while proving nothing, which is the
precise failure C-14 exists to prevent. If receipts cannot be recorded, the task
does not close.

See [[REQ__receipt-capability-diagnosis]] for the wider diagnosis flow.
