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
migration, or compatibility period. The one supported non-control appendix is
`REVIEWS.jsonl`, defined below; it is content-addressed diagnostic evidence and
never lifecycle authority. Planning decisions live in `PLAN.md`, and
environment facts are recomputed when needed. An unsupported task pack is
refused in place; `fresh_run: true` is not repair authority for it. Recovery
uses a distinct valid task rather than deleting unreadable evidence.

One transient exception, named here so the sentence above does not read it as
debris: `.stop_yield.<session>.json` is stop-gate scratch. `stop_gate.py`
writes one per session to count consecutive turn-yields against an unchanged
background record set, and nothing else reads it. It carries no lifecycle
authority, is not evidence, and is never migrated. Deleting it resets that
session's counter, so it costs at most `_MAX_CONSECUTIVE_YIELDS` further
yields before the gate blocks again — not one. See
`doc/harness/REQ__runtime-surfaces-name-the-actual-blocker.md`.

Every task generation uses one append-only `RECEIPTS.jsonl`. It is the only
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
when applicable, a `FIRST_LINE:` slot on `PENDING` completions only, and a
`DETAIL_SHA256` of the validated full final response.

The `FIRST_LINE:` slot carries the bounded text that occupied the verdict
position when nothing bound there. It is written always on `PENDING` and never
on a bound verdict, sits immediately before the digest so the counts slot stays
line 2 and the digest stays last, and is **optional on read** — rows written
before it exist must keep parsing. Making it required would fail every
`PENDING` receipt already on disk, and `receipt_snapshot` raises on a single
bad row, so that would poison the stream of every in-flight task. Without the
slot a completion that failed to bind records that it failed but not what it
was, which is the state that made the 2026-09-09 incident undiagnosable from
the receipt alone. See
`doc/harness/REQ__verdict-binding-survives-output-framing.md` for what binds;
this ADR remains the authority for how the row is stored.

Read compatibility is not replay compatibility. A duplicate stop is recognized
by recomputing the summary from the stop message and comparing it for equality
against the stored row, so a `PENDING` row written before the slot existed no
longer matches its own replay: it differs by exactly the retained line. During
that upgrade window a second delivery of the same stop is reported as
retryable (`receipt_pending`) instead of as a duplicate. The row stays on disk
and still validates, so no verification evidence is lost, and the window closes
as soon as the row is rewritten. Tightening the comparison would cost more
machinery than the window warrants.

When a review completion binds no verdict, its counts slot carries the reason
rather than counts: `FINDING_COUNTS: INVALID` when no line-1 verdict token could
be read at all, or `FINDING_COUNTS: UNREADABLE <TOKEN>` when one was read — and
`<TOKEN>` is retained — but the counts line was missing or ambiguous.

That slot is load-bearing, not descriptive. A completion reporting a readable
`FAIL` or `BLOCKED_ENV` supersedes an earlier bound verdict even though it binds
nothing, because a reviewer saying "not done" must outlive an earlier PASS. A
completion reporting a readable `PASS` without counts is a restatement and must
not: those are the field-measured strings (`VERDICT: PASS — report complete.`)
that a lens emits when invoked a second time. Recording only that *some* token
was readable, rather than which, makes restatements evict the verdicts they
restate — the deadlock this schema exists to prevent, in a narrower form.

A real counts line survives on an unbound completion only when the report was
read well enough to be substantive: line 1 parsed, or `FIX_NOW > 0`. An
`INVESTIGATE`-only count with no readable verdict is non-blocking and stores
`INVALID` instead. Readers may therefore treat a surviving counts line as
"contradiction or blocker" without re-deriving it.

**That last rule is writer-owned.** `normalize_receipt_completion` is its only
enforcement point: the receipt keeps the *normalized* verdict, so the entry
validator cannot tell whether line 1 was readable and will accept a violating
summary. Any future writer that assembles a completion summary without going
through the normalizer breaks the invariant silently.

Streams written before these slot rules record `INVALID` for every unbound case,
or a bare counts line where the current writer would store `INVALID`. Both
residues resolve fail-closed — the first under-evicts, the second over-evicts —
and receipt selection is scoped to `task_run_id`, so either can affect at most a
task run that straddles the upgrade.
The detailed response is not duplicated in the receipt stream. For every
formal `review-*` completion, including a completion that normalizes to
`PENDING`, the runtime-owned writer stores the exact final text in task-local
`REVIEWS.jsonl` as one JSON object containing exactly two string fields:

```json
{"detail_sha256":"<64 lowercase hex>","detail":"<exact final text>"}
```

The digest is SHA-256 over the exact UTF-8 detail bytes and is the same digest
named by the compact completion summary's `DETAIL_SHA256`. The appendix is
content-addressed: an existing valid row with the same digest and body is
reused, while the same digest with a different body is an integrity failure.
It is append-only across fresh task runs and has no migration or backfill.

`REVIEWS.jsonl` is deliberately non-authoritative. Receipt snapshots,
verdicts, context, fingerprints, installation, verification, and close never
read it. A missing appendix or digest for an older receipt is ordinary
not-found and cannot invalidate that receipt. New publication does preserve
referential integrity: while holding the existing task receipt transaction,
the lifecycle writer revalidates the current run and nonterminal task, appends
and fsyncs the detail, then appends the compact receipt. Detail failure
publishes no receipt. A later receipt failure rolls the receipt stream back to
its exact pre-append bytes and may leave only an orphan detail; retry reuses it
idempotently.

The appendix is bounded to 2 MiB per UTF-8 detail and 16 MiB per task. Readers
and writers use descriptor-relative, no-follow access; require a regular,
current-owner, single-link, owner-only file; validate file identity around
I/O; and stream rows within the bound. Malformed rows, hash mismatches, unsafe
metadata, or replacement fail closed for detail access without affecting
already valid receipt authority. `review-read` selects exactly one digest and
never offers list, latest, search, or whole-log output.

The current code-reviewer prompt requires a structured third line. When that
line is present, the normalizer strictly validates its exact schema and checks
its blocker/finding-derived verdict and counts against the two-line envelope.
When it is absent, the pre-existing two-line semantics remain valid so an old
runtime can finish a task after an upgrade. This is a deliberate mixed-version
ceiling: enforcing presence would require a new generation/version authority,
which this decision rejects as state growth. As with receipt hooks, hostile
same-user shell execution of the shared writer is outside the direct-write
enforcement boundary. It can consume appendix capacity but cannot authorize a
verdict because no lifecycle reader consumes the appendix.

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
writes no persistent install state. Every freshly verified invocation builds
the canonical runtime payload from its isolated source snapshot and compares it
directly with each selected installed payload. Synchronized runtimes are left
untouched; stale runtimes are refreshed independently. Unsafe or indeterminate
comparison fails closed. This comparison covers installer-owned payload trees,
not external CLI/config/registry health; explicit `install.py --force` remains
the repair path for that integration state. A retry after interruption simply
recomputes current payload truth and skips or refreshes as needed.

Codex receipt binding canonicalizes structured review task names rather than
requiring agents to remember one word order. Both `code_review*` and
`review_code*` bind to `review-code`; both `security_review*` and
`review_security*` bind to `review-security`. Spawn preflight rejects a
review-looking name that cannot bind and reports accepted forms before the
agent starts. This is validation only: hooks and agents still never author or
backfill receipts; the lifecycle watcher remains the sole publisher.

Acceptance intent lives in `PLAN.md`. User corrections are promoted directly
into the plan or durable documentation.

`PLAN_SESSION.json` is optional transient planning scratch, not a supported
control artifact. Normal same-session planning does not create it. A planner
may use it only for cross-turn or delegated recovery and removes it after
successful PLAN.md publication; stale legacy copies are ignored rather than
migrated.

`PROGRESS.md` is the persistent scope/resume aid with seven canonical keys:
`phase`, `current_ac`, `partial_ac`, `completed_acs`, `allowed_paths`,
`test_paths`, and `forbidden_paths`. It does not duplicate task identity,
acceptance decisions, timestamps, attempts, or receipt evidence. Legacy
verbose/prose copies remain best-effort readable.

## Consequences

The runtime has one authoritative stream, one receipt schema, one read per
lifecycle operation, and one fingerprint input. The optional detail appendix
does not add a gate input or state transition. There is no converter or
backfill. In-flight unsupported evidence is refused without mutation; it is
not discarded as recovery. For a valid task, only explicit `fresh_run: true`
starts a replacement generation and clears the current receipt stream; stored
review detail is retained.

Owner/no-follow checks, append locking, bounded reads, terminal protection,
review-before-QA ordering, explicit verdicts, current-run binding, stateless
verified installation, and close fingerprint validation remain mandatory.

## Verification

- Writers emit exactly the listed fields and only `started|completed` events.
- New tasks emit only the four-field `TASK.json` control and unified receipt
  stream as lifecycle authority; the supported non-authoritative review-detail
  appendix is written and selected only under the contract above.
- A new formal review receipt is published only after its exact detail is
  durably addressable by the receipt digest; legacy receipts without stored
  detail remain valid.
- Unsupported streams have no effect; invalid unified-schema entries fail with fresh-run
  guidance.
- Each MCP operation reads one immutable snapshot, and every consumer uses its
  entries and same-byte fingerprint.
- Review and QA gates reject missing, unordered, ambiguous, or non-PASS
  lifecycles while valid current-run evidence closes successfully.
