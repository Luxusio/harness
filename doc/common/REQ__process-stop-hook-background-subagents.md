# REQ - Process Stop Hook Background Subagents

## Intent
Define the Stop hook behavior when Claude has active background subagents or monitors running for an active harness task.

## Observable Behavior
- When the Stop hook receives a normal Stop payload and the active task has a current-run `started` receipt without a matching completion for the same Claude session and lifecycle identity, it waits briefly and then blocks with a background-work reason while that receipt remains active.
- A recursive Stop payload with `stop_hook_active=true` returns silent success while the same receipt-backed work remains active, preventing repeated hook-block loops.
- Valid starts older than the configured stale budget no longer block Stop; invalid or future timestamps remain active and fail closed. Old task runs and other sessions never block the current session.
- Ordinary open tasks without recursive active background work continue to block with the standard task start -> plan -> develop -> QA -> close public guidance; review and `task_verify` remain internal close gates.
- `RECEIPTS.jsonl` is the only lifecycle state. Stop handling never creates `doc/harness/runtime/background.json`, a registry lock, or diagnostic records.

## Acceptance Signals
- Start, completion, replay, concurrency, stale-start, cross-session, prior-run, recursive Stop, and normal open-task paths behave as specified using only the unified receipt stream.

## Verification Cues
- Verify with `tests/test_stop_gate.py` for normal background blocking, recursive active-background silent success, stale-start fallback, cross-session/run isolation, and ordinary open-task blocking.
- Verify direct Claude lifecycle publication, stop-only provenance, replay/concurrency idempotence, and absence of registry artifacts with the focused subagent lifecycle tests.

## Non-Goals
- This requirement does not permit abandoning an active harness task, bypassing `task_close`, or weakening receipt/runtime-verdict close gates. It does not define Claude Code internal hook scheduling beyond the `stop_hook_active` payload behavior consumed by Harness.

## Source
- created: 2026-05-26
- source: task: TASK__fix-claude-stop-hook-background-subagents
