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

Missing, malformed, or mismatched child evidence fails closed. A child that is
already complete when a delayed watcher replays may establish a start only when
the immutable registration offset precedes the exact root spawn and the replay
contains the matching activity, structured output, and trusted depth-1 child
rollout. Activity cannot authorize a lifecycle by itself.

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

SessionStart may create the versioned root registration. A successful Harness
`task_start` or `task_context` PostToolUse event is the binding authority because
it contains both the exact hook session id and returned task/run. It publishes
only `.active_sessions/<session-id>.json`. Eligibility is an explicit allowlist
of the bare Codex ids and exact Harness MCP-qualified ids; suffix matches from
other tool namespaces are not authority. `.session-hint`, `default.json`, and
the legacy `.active` file are never promoted into receipt authority. Root and child rollout
paths are resolved only in the UUIDv7-derived runtime-local day directory used
by Codex session storage. Spawn-selective
PreToolUse may restore a missing registration for that already exact binding
immediately before a supported spawn, beginning at the current rollout offset.
Task PostToolUse may register; UserPromptSubmit and Stop do not. A stale registration is recreated, not
migrated. Within one manager process, a successful worker suppresses only the
same exact validated registration identity; a recreated or refreshed identity
is eligible for one new worker, while a failed identity remains retryable. This
manager-local scheduling identity does not grant receipt authority or change
the registration's future-only offset.

Registration identity includes the exact task id and run id. Rebinding the same
root session to another task generation is allowed directly only after its
previous exact task/run is no longer open. Marker comparison, publication, and
watcher registration are serialized. When two different open task results
conflict, their freshness is ambiguous, so the current marker is removed and
the old watcher registration is invalidated; neither result becomes receipt
authority. A later unambiguous bind creates a new registration at the
then-current rollout offset instead of reusing events from the ambiguous
interval. The conflict marker is non-authoritative and retains only the two
exact task/run generations needed to test whether ambiguity still exists.
Neither can rebind while both remain open; once task state leaves exactly one
of them live, a valid result for that sole generation may recover authority.
Work remains fail-open while
attestation fails closed until a later unambiguous exact result rebinds the
session. An eligible new generation checkpoints the rollout at the new current
offset. A worker exits when its bound generation changes, allowing the manager
to start the refreshed generation without replaying earlier task work.

Alternate activity spellings, indirect tool adapters, status output,
prompt-marker identity, synthetic events, diagnostics, and synchronous
PostToolUse receipt writing are not receipt authorities. PostToolUse binds and
registers only; the watcher remains the sole receipt writer.

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

Registration can observe only lifecycle records after its immutable offset. A
watcher that starts late may replay a child that has since completed when the
entire correlated lifecycle follows that offset; registration after the spawn
cannot see that spawn and cannot manufacture a missing start.

## Verification

- A direct structured spawn, exact activity, matching structured output, and
  trusted child rollout record one start; matching child/root finals record one
  completion.
- Activity-only, ambiguous, malformed, mismatched, pre-registration, or
  unsupported protocol records create no authority; repeated valid replay is
  deduplicated.
- Wrong task run, repository, session, rollout, thread, agent path, task name,
  lens, order, final, or verdict is rejected.
- Focused watcher/hook tests and the full suite pass after verified install.
