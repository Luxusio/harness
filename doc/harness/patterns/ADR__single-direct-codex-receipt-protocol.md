# ADR: Single Direct Codex Receipt Protocol

## Status

Accepted. Extended 2026-09-29 to authenticated native worktree coordinators.

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

The MCP-hosted coordinator-rollout watcher is the only Codex receipt owner and accepts
one acquisition path:

1. The registered coordinator calls direct `collaboration.spawn_agent` with a valid
   structured `task_name`.
2. A matching exact `SubAgentActivity` start supplies the child thread identity
   and agent path.
3. The matching structured spawn output supplies the same child agent path.
4. The watcher records the start only after all three records agree.

Missing, malformed, or mismatched child evidence fails closed. A child that is
already complete when a delayed watcher replays may establish a start only when
the immutable registration offset precedes the exact root spawn and the replay
contains the matching activity, structured output, and trusted direct-child
rollout. Activity cannot authorize a lifecycle by itself.

For the ordinary root coordinator, a direct child has depth 1. A worktree lead
may itself be a native subagent: every ancestor edge must match trusted native
metadata, the parent's exact structured spawn call/output and activity, the
top-level session, immediate parent, full agent path and depth increment.
The review/QA child is direct relative to that authenticated coordinator.
Arbitrary nested paths, prompt text, and a workspace argument cannot establish
coordinator identity. Bounded ancestry validation replaces a hard-coded depth-1
assumption without adding a second receipt acquisition protocol.

The watcher binds the active `TASK.json` generation, canonical repository/worktree,
native coordinator thread and rollout, child thread, child agent path, structured task name, and derived
review or QA lens. The coordinator's exact active-task marker and current
`TASK.json` are the only task authorities. MCP output and prompt text are not
task authorities.

The persisted compact identity is one namespaced `runtime_id`:
`codex:<coordinator-thread>:<spawn-event>:<child-thread>`. Separate runtime event,
session, and thread fields are not stored. This is a storage projection only;
all bounded rollout checks above still run before a receipt is appended.

Completion requires one child `task_complete` final and one direct child
`FINAL_ANSWER` delivered to that coordinator. Their final text must match exactly. If
the child rollout also contains a distinct child final-answer event, it must
match the same text. Duplicate, conflicting, historical, cross-run, or
out-of-order boundaries invalidate the lifecycle rather than selecting a
convenient candidate.

SessionStart may create the versioned root registration. A successful Harness
`task_start` or `task_context` PostToolUse event supplies exact hook session
identity and returned task/run to the shared binding owner. A source MCP host
may also bind an explicit workspace using its process-owned exact thread only
after authenticating trusted
native metadata, the actual session cwd and the complete parent spawn ancestry.
The environment string alone, an echoed workspace, and repository hints do not
authenticate that actor. Both paths validate the canonical registered task root
and current run under the same cross-root locks and conflict fences, and publish
only `.active_sessions/<session-id>.json`. The actual native cwd remains separate
from a linked worktree task root. Invalid native proof on the workspace path
creates no fresh authority. Existing exact main-call behavior keeps its shared
session-lock protection.
A valid exact binding may survive unavailable watcher registration so a later
pre-spawn hook can restore it; this grants no receipt or close by itself. Eligibility is an explicit allowlist
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
interval. The conflict marker is non-authoritative and retains the conflicting
exact task/run generations needed to test whether ambiguity still exists,
including each newly returned candidate. The record is bounded to 257
generations and 128 KiB of serialized conflict entries; exceeding either bound
publishes a non-authoritative overflow fence and revokes registrations. An
overflowed coordinator cannot regain authority and requires a new coordinator.
None can rebind while multiple generations remain open; once task state leaves exactly one
of them live, a valid result for that sole generation may recover authority.
Recovery holds all relevant task receipt locks in canonical path order through state
revalidation, marker publication, and registration refresh.
Any published conflict fence continues to revoke pre-conflict registrations even
after competing tasks are parked. Ordinary task-close and parking marker cleanup
must preserve these non-authoritative fences. A process-owned exact Codex
actor is also the identity used for terminal marker snapshot, cleanup, cleanup
validation and rollback; an inherited parent session ID must not select a
different marker. Unidentified MCP hosts and Claude retain their existing
identity owner. Ordinary watcher restoration cannot acknowledge
that history. Only a successful exact task result for the sole surviving
generation may recover: invalidate all surviving registrations before clearing
acknowledged fences, then register at the current rollout offset. Interruption or
failed invalidation leaves the fences in force; ambiguity-interval events cannot
be replayed into receipts.

An explicit workspace binding additionally requires the successful task result
to echo the exact requested canonical workspace and the target to be a
revalidated linked worktree of the same repository. Explicit-workspace calls
without authenticated process-owned identity defer to native PostToolUse. A
process-owned actor that passes the native proof above can bind directly through
the same owner. Main calls without an exact environment thread identity also
defer; exact main calls retain their shared session-lock protection. Native cwd
may remain the shared main checkout. Both paths serialize binding across
registered workspaces and reject simultaneous open bindings for the same
coordinator. The manager discovers only validated
worktrees and applies one shared watcher-thread cap; worktree-local registrations
cannot manufacture an independent unlimited manager budget.
Work remains fail-open while
attestation fails closed until a later unambiguous exact result rebinds the
session. An eligible new generation checkpoints the rollout at the new current
offset. A worker exits when its bound generation changes, allowing the manager
to start the refreshed generation without replaying earlier task work.

An unavailable binding read is not a validated generation change. The first
unavailable read produces a retryable worker failure without an inline retry,
including at spawn and completion publication boundaries; it does not
permanently invalidate that lifecycle or grant authority during uncertainty.
The manager can retry only after independently validating the persisted
registration. This reuses the existing immutable-offset recovery, not a new
receipt authority. Known conflict invalidation and fresh-offset rebinding
remain mandatory; normal successful retirement still suppresses the same
registration generation within that manager.
The running worker pins that immutable registration generation and revalidates
it at lifecycle publication boundaries under the session transaction. A
replacement retires the old worker even if the task/run pair is unchanged;
missing or unsafe registration is unavailable authority, never a match.

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

## Operational acquisition repair

The process-owned eager MCP binding and native PostToolUse binding publish the
same exact task/run registration. An extra validation callback cannot opt the
production registrar out of generation binding. The installed hook matcher
routes public wbs open/context results to that existing owner.

A shared MCP process may lack thread identity, and nested code-mode calls may
not deliver its native PostToolUse hook. Explicit acquisition repair may use a
real thread-scoped stdio MCP host inheriting the unchanged native
CODEX_THREAD_ID. Keep the MCP manager alive for future native lifecycles; this
uses the existing eager path and is not a second receipt protocol. Do not pass
an invented hook payload or substitute inherited CODEX_SESSION_ID. Registration
still begins at its immutable current offset; no historical backfill occurs.

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
