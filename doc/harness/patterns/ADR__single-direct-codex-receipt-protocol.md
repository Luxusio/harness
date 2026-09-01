# ADR: Single Direct Codex Receipt Protocol

## Status

Accepted.

## Normative scope

This ADR is the sole normative owner of Codex receipt acquisition, runtime
identity correlation, and completion matching. The receipt stream, schema, snapshot, and
gate semantics are owned by
[ADR__consolidated-task-artifacts.md](ADR__consolidated-task-artifacts.md).

## Context

The Codex lifecycle watcher accumulated alternate tool adapters, payload
spellings, filesystem child inference, status polling, and prompt-derived
identity. Each path added ambiguity, replay, and deduplication branches without
adding stronger evidence.

## Decision

The MCP-hosted root-rollout watcher is the only Codex receipt owner and accepts
one acquisition path:

1. The registered root calls direct `collaboration.spawn_agent` with a valid
   structured `task_name`.
2. A matching exact `SubAgentActivity` start supplies the child thread identity
   and agent path.
3. The matching structured spawn output supplies the same child agent path.
4. The watcher records the start only after all three records agree.

Missing, malformed, mismatched, or already-completed child evidence fails
closed. Activity cannot authorize a lifecycle by itself; its path must match
the structured spawn output and the trusted child rollout.

The watcher binds the active `TASK.json` generation, canonical repository, root session,
root rollout, child thread, child agent path, structured task name, and derived
review or QA lens. The root session's active-task marker and current
`TASK.json` are the only task authorities. MCP output and prompt text are not
task authorities.

The persisted compact identity is one namespaced `runtime_id`:
`codex:<root-session>:<spawn-event>:<child-thread>`. Separate runtime event,
session, and thread fields are not stored. This is a storage projection only;
all bounded rollout checks above still run before a receipt is appended.

Completion requires one child `task_complete` final and one direct child
`FINAL_ANSWER` delivered to the root. Their final text must match exactly. If
the child rollout also contains a distinct child final-answer event, it must
match the same text. Duplicate, conflicting, historical, cross-run, or
out-of-order boundaries invalidate the lifecycle rather than selecting a
convenient candidate.

SessionStart creates the versioned root registration. Root and child rollout
paths are resolved only in the UUIDv7-derived runtime-local day directory used
by Codex session storage. Spawn-selective
PreToolUse may restore a missing registration immediately before a supported
spawn, beginning at the current rollout offset. UserPromptSubmit, PostToolUse,
and Stop do not recover registration. A stale registration is recreated, not
migrated.

Alternate activity spellings, indirect tool adapters, status output,
prompt-marker identity, synthetic events, diagnostics, and synchronous
PostToolUse receipt writing are not receipt authorities.

## Integrity boundary

Rollout and registration inputs retain bounded reads, no-follow opening,
owner/type/mode/link checks, descriptor/path identity checks, replay bounds,
deduplication, one-watcher leasing, and terminal-stream protection. Rollout
lookup does not scan session history or guess across repository boundaries.

## Consequences

Codex receipt acquisition has one protocol state machine. Runtime protocol
changes or a missing exact activity event leave evidence missing; `task_verify`
and `task_close` remain closed. The active task does not repair the watcher or
repeat a lens solely for receipt acquisition: it awaits substantive review and
QA, runs one fresh `task_verify`, and parks through `task_blocked` when required
hook-owned evidence is still absent. Any later fresh attested run is an explicit
operator choice.

Late registration can observe only future work. It cannot authorize a child
that already completed, and it cannot manufacture a missing start.

## Verification

- A direct structured spawn, exact activity, matching structured output, and
  trusted child rollout record one start; matching child/root finals record one
  completion.
- Activity-only, ambiguous, malformed, mismatched, pre-completed, replayed, or
  unsupported protocol records create no authority.
- Wrong task run, repository, session, rollout, thread, agent path, task name,
  lens, order, final, or verdict is rejected.
- Focused watcher/hook tests and the full suite pass after verified install.
