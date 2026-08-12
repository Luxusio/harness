# ADR: Single Direct Codex Receipt Protocol

## Status

Accepted.

## Normative scope

This ADR is the sole normative owner of Codex receipt acquisition, runtime
identity, and completion matching. The receipt stream, schema, snapshot, and
gate semantics are owned by
[ADR__consolidated-task-artifacts.md](ADR__consolidated-task-artifacts.md).

## Context

The Codex lifecycle watcher accumulated adapters for direct `collaboration`,
`multi_agent_v1`, JavaScript inside `exec`, activity feeds, wait/close/list
outputs, XML notifications, and prompt-derived task names. Each adapter added
identity, ambiguity, replay, and deduplication branches without adding stronger
evidence.

## Decision

The MCP-hosted root-rollout watcher is the only Codex receipt owner and accepts
one acquisition path:

1. The registered root calls direct `collaboration.spawn_agent` with a valid
   structured `task_name`.
2. The matching structured spawn output supplies the child agent identity.
3. Bounded discovery resolves exactly one trusted depth-1 child rollout whose
   root, session, cwd, agent path, task name, and child identity match the call
   and output.
4. The watcher records the start only after all three records agree.

Zero, multiple, malformed, mismatched, out-of-bound, or already-completed child
candidates fail closed. Activity events are ignored and cannot start or repair
a lifecycle.

The watcher binds the active `TASK.json` generation, canonical repository, root session,
root rollout, child thread, child agent path, structured task name, and derived
review or QA lens. The root session's active-task marker and current
`TASK.json` are the only task authorities. MCP output, prompt text, and
watcher-local fallback state are not task authorities.

Completion requires one child `task_complete` final and one direct child
`FINAL_ANSWER` delivered to the root. Their final text must match exactly. If
the child rollout also contains a distinct child final-answer event, it must
match the same text. Duplicate, conflicting, historical, cross-run, or
out-of-order boundaries invalidate the lifecycle rather than selecting a
convenient candidate.

SessionStart creates the versioned root registration. Spawn-selective
PreToolUse may restore a missing registration immediately before a supported
spawn, beginning at the current rollout offset. UserPromptSubmit, PostToolUse,
and Stop do not recover registration. A stale registration is recreated, not
migrated.

`sub_agent_activity`, `exec`, `multi_agent_v1`, wait/close/list output,
status polling, XML notification, prompt-marker identity, synthetic events,
adapter diagnostics, and synchronous PostToolUse receipt writing are not
receipt authorities.

## Integrity boundary

Rollout and registration inputs retain bounded reads, no-follow opening,
owner/type/mode/link checks, descriptor/path identity checks, replay bounds,
deduplication, one-watcher leasing, and terminal-stream protection. Discovery
never guesses across repository or session boundaries.

## Consequences

Codex receipt acquisition has one protocol state machine. Runtime protocol
changes fail by leaving evidence missing; `task_verify` and `task_close` remain
closed until Harness and Codex are upgraded, installed, restarted, and the
review or QA run is repeated.

Late registration can observe only future work. It cannot authorize a child
that already completed, and it cannot manufacture a missing start.

## Verification

- A direct structured spawn and exactly one trusted child rollout records one
  start; matching child/root finals record one completion.
- Activity-only, ambiguous, malformed, mismatched, pre-completed, replayed, or
  unsupported protocol records create no authority.
- Wrong task run, repository, session, rollout, thread, agent path, task name,
  lens, order, final, or verdict is rejected.
- Focused watcher/hook tests and the full suite pass after verified install.
