# REQ - Process Subagent Lifecycle Cleanup

status: accepted

## Intent

Harness workflows that spawn subagents must also own their lifecycle. A
subagent start is not complete until the orchestrator has consumed or declined
its result and closed the agent session when the runtime exposes an explicit
close tool.

Completed agents can continue to count toward runtime concurrency limits until
closed. Leaving them open causes later harness work to fail for capacity reasons
even though the useful work already finished.

## Observable Behavior

- For every `spawn_agent` call, the orchestrator tracks the returned `agent_id`.
- When a spawned agent completes, fails, is cancelled, or is no longer needed,
  the orchestrator calls `close_agent` when that tool is available.
- Before final response, `task_close`, or handoff, the orchestrator closes every
  agent it spawned for the workflow unless the user explicitly asked to leave a
  still-running agent open.
- Reading a result and closing the agent does not delete or replace the
  hook-owned start receipt in `SUBAGENT_RECEIPTS.jsonl`.
- If close is unavailable or fails, the orchestrator reports the concrete
  blocker instead of silently leaving completed agents open.

## Acceptance Signals

- Codex run/develop guidance states that spawned agent ids are tracked and
  closed with `close_agent`.
- Claude run/develop guidance states the same lifecycle responsibility for
  runtimes that expose explicit close tools.
- Prompt regression tests cover the cleanup wording so future subagent-routing
  edits preserve it.

## Verification Cues

- `tests/test_codex_run_subagent_routing.py` checks the run/develop prompt
  surfaces for `close_agent`, final-response/`task_close` cleanup timing, and
  concurrency-limit rationale.

## Non-Goals

- This does not require hooks to close agents automatically.
- This does not remove hook-owned subagent start receipts.
- This does not require closing a still-running agent that the user explicitly
  asked to keep open.

## Source

- created: 2026-06-25
- source: user feedback that completed agents had been left open often enough
  to hit agent/concurrency limits.
