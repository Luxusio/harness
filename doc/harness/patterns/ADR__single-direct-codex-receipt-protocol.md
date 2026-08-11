# ADR: Single Direct Codex Receipt Protocol

## Status

Accepted.

## Context

The Codex lifecycle watcher accumulated adapters for direct `collaboration`,
`multi_agent_v1`, JavaScript inside `exec`, wait/close/list outputs, XML
notifications, and prompt-derived task names. Each combination needed separate
identity, ambiguity, replay, and deduplication logic. The watcher grew to more
than 2,000 lines and the dormant PostToolUse fallback duplicated receipt
writing even though production disabled it.

## Decision

Codex receipt evidence has one owner and one supported runtime contract. The
MCP-hosted root-rollout watcher accepts:

1. Direct `collaboration.spawn_agent` with a structured, valid `task_name`.
2. Structured spawn output plus either a correlated `sub_agent_activity` start
   or one unambiguous trusted depth-1 child rollout discovered from root/child
   session metadata. Codex 0.147 currently uses this output-only path.
3. A direct child `FINAL_ANSWER` delivered to the root.
4. Matching trusted child rollout metadata and a `task_complete` final; when a
   separate child final-answer event exists, it must match task-complete.

The watcher still binds the active `TASK_RUN`, root session, child thread,
agent path, task, lens, event order, and exact verdict. It retains no-follow,
bounded-read, registration, replay, dedupe, and terminal-stream protections.

Watcher registration has two entry points: SessionStart and spawn-selective
PreToolUse. UserPromptSubmit, PostToolUse, and Stop do not recover registration.
The session-specific active-task marker plus current `TASK_RUN.json` is the sole
task binding; MCP task_start/task_context output and watcher-local task fallback
are not parsed.

`exec`, `multi_agent_v1`, wait/close/list completion reconstruction, XML
notifications, prompt-marker task names, synthetic activity, adapter diagnostic
receipts, and the synchronous PostToolUse receipt writer are not supported.

## Consequences

The production path is deletion-heavy and has one protocol state machine.
Receipt integrity for accepted events is unchanged, while parser and alias
attack surface is smaller.

An old registration version is recreated at the current rollout offset instead
of migrated. This can miss earlier events but cannot authorize a false PASS;
the missing receipt fails close. Likewise, a missing task marker is not inferred
from nearby MCP output.

A future Codex protocol change will not be guessed or silently normalized.
Required receipts stay missing and `task_verify`/`task_close` fail closed until
Harness and Codex are updated and installed together. This upgrade coupling is
an accepted maintenance tradeoff for a lighter Harness.

## Verification

- Direct spawn with activity/output/final records exactly one start and
  completion.
- Output-only spawn resolves exactly one trusted child rollout and records the
  same lifecycle; missing or ambiguous child metadata fails closed.
- Wrong task run, session, thread, agent path, lens, order, final, or verdict is
  rejected.
- Removed protocols produce no receipt authority.
- Focused watcher/hook tests and the full suite pass after verified install.
