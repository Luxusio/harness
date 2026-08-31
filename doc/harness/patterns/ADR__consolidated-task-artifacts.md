# ADR: Consolidated Task Artifacts

## Status

Accepted.

## Normative scope

This ADR is the sole normative owner of task-control and receipt storage,
schemas, snapshots, and review/QA gate semantics. Codex acquisition, runtime identity correlation, and
completion matching are owned by
[ADR__single-direct-codex-receipt-protocol.md](ADR__single-direct-codex-receipt-protocol.md).

## Context

Separate acceptance, feedback, review, and QA artifacts duplicated state. The
receipt stream then retained derivable fields, so every consumer still carried
unnecessary schema and provenance inputs.

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
run-start cutoff used by lifecycle validation, while its random bits isolate
receipt generations. `required_lenses` is a canonical set containing
`review-code` and at least one `qa-*` lens; review and QA views are derived from
the lens prefixes. Receipt records retain the wire name `task_run_id`, populated
from `TASK.json.run_id`.

The canonical directory supplies task identity. Receipt snapshots supply
review, QA, and runtime verdicts. `BLOCKED.md` supplies blocked state. On
successful close, `close_receipt_fingerprint` becomes `sha256:<64hex>`. It must
continue to match the exact receipt bytes when Goal completion is evaluated.

Unsupported task-control and auxiliary artifacts have no readers, writers,
migration, or compatibility period. Planning decisions live in `PLAN.md`, and
environment facts are recomputed when needed. A fresh task run is required for
an unsupported task pack.

New and resumed runs use one append-only `RECEIPTS.jsonl`. It is the only
supported receipt stream and the only input to verdicts, provenance,
fingerprints, installation authority, and close authority.

Every line is a JSON object containing exactly these fields:

```text
ts, event, source, task_run_id, runtime_id, agent_id, agent_type, lens,
verdict, summary
```

`event` is exactly `started` or `completed`. Category is derived from `lens`;
lifecycle state is derived from `event`; timestamp role is derived from
`event + ts`; finding counts are parsed from the canonical completion
`summary`. A start carries an empty summary and no passing verdict. A
completion summary retains only its normalized verdict, review finding counts
when applicable, and a `DETAIL_SHA256` of the validated full final response.
The detailed response remains in the runtime transcript and is not duplicated
in the receipt stream.

Entries correlate by exact `source`, `task_run_id`, `runtime_id`, `agent_id`,
`agent_type`, and `lens`. Runtime identity is namespaced and parseable:
`claude:<session>:<agent>` or `codex:<root>:<event>:<child>`. Append position
establishes lifecycle order and review-before-QA order; wall-clock comparison
does not. Transcript paths and digests are verification inputs before append,
not persistent receipt state.

Claude runtimes that emit `SubagentStop` without a preceding `SubagentStart`
use the stop hook as the authoritative lifecycle observation only under the
full provenance boundary: exact top-level official `agent_id` and `session_id`,
the matching session marker and current `run_id`, and a stable owner-controlled
Claude transcript whose path matches that session/agent and whose recorded
`SubagentStart` attachment supplies the agent type after the UUIDv7 run cutoff.
Payload claims cannot override the transcript-derived agent type.

Two attachment shapes supply that agent type: the canonical identity banner
(`hookName: "SubagentStart"`, `content: ["Agent <type> started (<id>)"]`) and
the matcher-qualified hook-execution record (`hookName:
"SubagentStart:<type>"`), whichever is present, with the identity requirement
attaching to whichever line binds. The banner originates in a third-party
plugin's optional output and is intermittently absent; anchoring provenance to
it made PASS unreachable. See
`doc/common/REQ__process__subagent-receipt-binding.md`.

The boundary deliberately does **not** require the transcript's final assistant
text to match `last_assistant_message`. The runtime appends that text around the
instant `SubagentStop` fires, so the check rejected genuine stops about as often
as it passed them; it is pinned absent by
`test_stop_completes_when_the_final_text_has_not_been_flushed`. The completion identity is single-use. Under
those conditions the hook appends a correlated inferred `started` entry
immediately followed by the explicit `completed` entry in one task transaction.
The verdict still comes only from the unique canonical first line; missing,
foreign, stale, replayed, aliased, untrusted, or unbound stops cannot yield
PASS. Generic or indirect adapter invocation remains denied by the bound
writer, but hostile-shell execution of canonical shipped lifecycle scripts is
an accepted exposure outside Harness's integrity boundary. There is no
module-visible raw-byte append primitive. Each reviewed
Claude/Codex adapter binds its original code object, globals identity, and
provenance-dependency identities once during module import; the closure-backed
writer rejects clones, replacements, dependency mutation, and later rebinding
without depending on interpreter-specific bytecode hashes. Generic or indirect
callers fail before append. Receipt reset returns only an opaque one-shot
capability whose captured bytes remain inside the rollback closure; restoration
cannot accept caller-supplied text.
The transcript namespace and every path component/leaf are descriptor-bound,
non-symlink, owner-only provenance; direct Write/Edit/apply_patch gates deny
model-authored changes to Claude subagent transcript leaves. Shell-capable
callers are outside that enforcement boundary.
The inferred started/completed pair publishes under a receipt savepoint. An
append failure restores the prior stream and leaves the same stop retryable.
Concurrent/retried stops reuse one exact already-durable lifecycle identity
instead of appending a duplicate pair in the same transaction.

Claude Stop-hook active-work protection derives unmatched current-run
`started` receipts from this stream for the exact session, without secondary
runtime state. A valid start may age out of Stop waiting without mutating the
append-only evidence; malformed or future timestamps remain active and fail
closed.

Entries that do not match the exact `RECEIPTS.jsonl` schema are rejected with
fresh-run guidance. They are not normalized, migrated, or partially accepted.

Each receipt-consuming MCP operation creates at most one frozen
`ReceiptSnapshot` while holding an exclusive lock on the validated task-directory
descriptor without adding a lock artifact. The snapshot contains validated
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
writes no persistent install state. Every freshly verified invocation
reinstalls the complete tracked payload, including when the source worktree is
clean; dirty-path detection is not persistent installation truth. A retry after
interruption simply installs again.

Acceptance intent lives in `PLAN.md`. User corrections are promoted directly
into the plan or durable documentation.

## Consequences

The runtime has one stream, one schema, one read per operation, and one
fingerprint input. There is no compatibility period or converter. In-flight
unsupported evidence must be discarded by rotating to a fresh task run.

Owner/no-follow checks, append locking, bounded reads, terminal protection,
review-before-QA ordering, explicit verdicts, current-run binding, stateless
verified installation, and close fingerprint validation remain mandatory.

## Verification

- Writers emit exactly the listed fields and only `started|completed` events.
- New tasks emit only the four-field `TASK.json` control and unified receipt
  stream; unsupported auxiliary leaves are never read or written.
- Unsupported streams have no effect; invalid unified-schema entries fail with fresh-run
  guidance.
- Each MCP operation reads one immutable snapshot, and every consumer uses its
  entries and same-byte fingerprint.
- Review and QA gates reject missing, unordered, ambiguous, or non-PASS
  lifecycles while valid current-run evidence closes successfully.
