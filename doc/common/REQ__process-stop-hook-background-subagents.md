# REQ - Process Stop Hook Background Subagents

## Intent
Define the Stop hook behavior when Claude has active background subagents or monitors running for an active harness task.

## Observable Behavior
- When the Stop hook receives a normal Stop payload and the active task has current background subagent records for the same task/session, it should wait briefly and then block with a background-work reason if the records remain active. When the Stop hook receives a recursive Stop payload with stop_hook_active=true and the same active background records are still in flight, it should return silent success so Claude Code can end the continuation turn without hitting repeated hook block caps. Stale background records should be ignored and should not mask the normal open-task Stop gate. Ordinary open active tasks without recursive active background state should continue to block with the standard plan -> develop -> verify -> close guidance.

## Acceptance Signals
- When the Stop hook receives a normal Stop payload and the active task has current background subagent records for the same task/session, it should wait briefly and then block with a background-work reason if the records remain active. When the Stop hook receives a recursive Stop payload with stop_hook_active=true and the same active background records are still in flight, it should return silent success so Claude Code can end the continuation turn without hitting repeated hook block caps. Stale background records should be ignored and should not mask the normal open-task Stop gate. Ordinary open active tasks without recursive active background state should continue to block with the standard plan -> develop -> verify -> close guidance.

## Verification Cues
- Verify with tests/test_stop_gate.py for normal background blocking, recursive active-background silent success, stale-record fallback to normal open-task blocking, and ordinary open-task blocking. Verify background registry matching behavior with tests/test_background_registry.py.

## Non-Goals
- This requirement does not permit abandoning an active harness task, bypassing task_close, or weakening CHECKS/runtime_verdict close gates. It does not define Claude Code internal hook scheduling beyond the stop_hook_active payload behavior consumed by harness.

## Source
- created: 2026-05-26
- source: task: TASK__fix-claude-stop-hook-background-subagents
