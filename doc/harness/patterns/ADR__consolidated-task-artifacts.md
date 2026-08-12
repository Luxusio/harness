# ADR: Consolidated Task Artifacts

## Status

Accepted.

## Normative scope

This ADR is the sole normative owner of task-control and receipt storage,
schemas, snapshots, and review/QA gate semantics. Codex acquisition, runtime identity discovery, and
completion matching are owned by
[ADR__single-direct-codex-receipt-protocol.md](ADR__single-direct-codex-receipt-protocol.md).

## Context

Separate acceptance, feedback, review, and QA artifacts duplicated state. The
unified receipt stream then retained derivable fields and compatibility readers,
so every consumer still carried multiple schemas and provenance inputs.

## Decision

Task control uses one exact four-field `TASK.json`:

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "close_receipt_fingerprint": null
}
```

`run_id` is an RFC 9562 UUIDv7. Its embedded Unix-millisecond timestamp is the
run-start cutoff used by lifecycle discovery, while its random bits isolate
receipt generations. `required_lenses` is a canonical set containing
`review-code` and at least one `qa-*` lens; review and QA views are derived from
the lens prefixes. Receipt records retain the wire name `task_run_id`, populated
from `TASK.json.run_id`.

The canonical directory supplies task identity. Receipt snapshots supply
review, QA, and runtime verdicts. `BLOCKED.md` supplies blocked state. On
successful close, `close_receipt_fingerprint` becomes `sha256:<64hex>`. It must
continue to match the exact receipt bytes when Goal completion is evaluated.

`TASK_STATE.yaml`, `TASK_RUN.json`, `PLAN.meta.json`, and
`TASK_CLOSE_RECEIPT.json` have no readers, writers, migration, or compatibility
period. A fresh task run is required for an old task pack.

`AUDIT_TRAIL.md` and `ENVIRONMENT_SNAPSHOT.md` are not task artifacts. Planning
decisions live in `PLAN.md`, and environment facts are recomputed when needed.

New and resumed runs use one append-only `RECEIPTS.jsonl`. It is the only
supported receipt stream. `REVIEW_RECEIPTS.jsonl` and
`SUBAGENT_RECEIPTS.jsonl` are ignored: they do not contribute verdicts,
provenance, fingerprints, installation authority, or close authority.

Every line is a JSON object containing exactly these fields:

```text
receipt_id, ts, event, source, task_run_id, agent_id, agent_type, lens,
verdict, summary, transcript_path, transcript_sha256, runtime_event_id,
runtime_session_id, runtime_thread_id
```

`event` is exactly `started` or `completed`. Category is derived from `lens`;
lifecycle state is derived from `event`; timestamp role is derived from
`event + ts`; finding counts are parsed from the canonical completion
`summary`. A start carries no passing verdict. A completion must carry the
explicit verdict and canonical summary required by its lens.

Entries correlate by `task_run_id`, `agent_id`, `agent_type`, `lens`, and the
exact supplied runtime identity. Append position establishes lifecycle order
and review-before-QA order; wall-clock comparison does not.

Claude runtimes that emit `SubagentStop` without a preceding `SubagentStart`
use the stop hook as the authoritative lifecycle observation only under the
full provenance boundary: exact top-level official `agent_id` and `session_id`,
the matching session marker and current `run_id`, and a stable owner-controlled
Claude transcript whose path matches that session/agent, whose recorded
`SubagentStart` attachment supplies the agent type after the UUIDv7 run cutoff,
and whose final assistant text exactly matches `last_assistant_message`. Payload
claims cannot override the transcript-derived agent type. The completion identity is single-use. Under
those conditions the hook appends a correlated inferred `started` entry
immediately followed by the explicit `completed` entry in one task transaction.
The verdict still comes only from the unique canonical first line; missing,
foreign, stale, replayed, aliased, untrusted, or unbound stops cannot yield
PASS. Direct model invocation of lifecycle receipt-authoring scripts is denied.
The transcript namespace and every path component/leaf are descriptor-bound,
non-symlink, owner-only provenance; Write/Edit/apply_patch and Bash mutation
gates deny model-authored changes to Claude subagent transcript leaves.
The inferred started/completed pair publishes under a receipt savepoint. The
registry marks the stop single-use only after both entries are durable; an
append or registry-publication failure restores the prior stream and leaves the
same stop retryable. Concurrent/retried stops reuse one exact already-durable
lifecycle identity instead of appending a duplicate pair.

Old-schema entries in `RECEIPTS.jsonl` are rejected with an actionable message
to start a fresh task run or reset the unsupported stream. They are not
normalized, migrated, or partially accepted.

Each receipt-consuming MCP operation creates at most one frozen
`ReceiptSnapshot` while holding an exclusive lock on the validated task-directory
descriptor. No `.receipts.lock` leaf exists. The snapshot contains validated
ordered entries and the fingerprint of the exact bytes that produced them.
Verdict, summary, context, provenance, verified installation, and close use
that same snapshot. Unsafe ownership/type/mode/link state, path or inode
replacement, same-size mutation, truncation, malformed JSON, unknown fields,
or schema mismatch fails closed.

`task_verify` requires a correlated `started` then explicit `completed PASS`
for every plan-declared review lens, followed by the same lifecycle for every
declared QA lens. FAIL, BLOCKED_ENV, missing or contradictory verdicts,
unmatched identities, duplicate/conflicting terminals, or QA that started
before the latest required review PASS cannot yield runtime PASS.
`task_close` accepts only the current task run's PASS snapshot and writes its
fingerprint into `TASK.json.close_receipt_fingerprint`. Receipts do not bind Git HEAD, a diff, or touched
paths; source drift after evidence remains developer-owned.

Verified installation is stateless. `install_verified.py` holds only its
transaction lock and in-memory source/receipt fingerprints while it runs. It
writes no `INSTALL_RECEIPT.json`, install cache, or task-control field. Every
freshly verified invocation reinstalls the complete tracked payload, including
when the source worktree is clean; dirty-path detection is not persistent
installation truth. A retry after interruption simply installs again.

`CHECKS.yaml` and `USER_FEEDBACK.jsonl` are not current task artifacts.
Acceptance intent lives in `PLAN.md`; user feedback remains conversational or
is promoted directly into the plan or durable documentation.

## Consequences

The runtime has one stream, one schema, one read per operation, and one
fingerprint input. There is no compatibility period or converter. In-flight
old evidence must be discarded by rotating to a fresh task run.

Owner/no-follow checks, append locking, bounded reads, terminal protection,
review-before-QA ordering, explicit verdicts, current-run binding, stateless
verified installation, and close fingerprint validation remain mandatory.

## Verification

- Writers emit exactly the listed fields and only `started|completed` events.
- New tasks emit the four-field `TASK.json`; removed control, install, audit,
  environment-snapshot, and receipt-lock leaves are never read or written.
- Old streams have no effect; old unified-schema entries fail with fresh-run
  guidance.
- Each MCP operation reads one immutable snapshot, and every consumer uses its
  entries and same-byte fingerprint.
- Review and QA gates reject missing, unordered, ambiguous, or non-PASS
  lifecycles while valid current-run evidence closes successfully.
