# ADR: Consolidated Task Artifacts

## Status

Accepted.

## Context

The normal Harness flow created separate acceptance-criteria, user-feedback,
review-receipt, and QA-receipt files for every task. These files duplicated
conversation/runtime evidence and made the task pack heavier than its close
gate required.

## Decision

New tasks do not create `CHECKS.yaml` or `USER_FEEDBACK.jsonl`. Acceptance
criteria remain ordinary sections in `PLAN.md`; Harness no longer maintains or
auto-promotes a second AC ledger. User feedback is handled in the live
conversation and promoted into the plan or durable documentation only when it
changes the implementation.

Review and QA lifecycle evidence share one append-only `RECEIPTS.jsonl` stream.
Each entry retains its `kind`, lens, task-run generation, runtime identity,
status, verdict, and ordering evidence. Review and QA readers filter the shared
stream. Existing `REVIEW_RECEIPTS.jsonl` and `SUBAGENT_RECEIPTS.jsonl` files are
read for legacy tasks but are never created by the new writer.

## Consequences

A normal reviewed task generates three fewer files. The review and QA gates,
review-before-QA ordering, no-follow storage checks, receipt lock, stream
fingerprint, close attestation, and verified-install checks remain.

Harness no longer provides per-AC status/reopen counters or automatic prompt
feedback history. Those were workflow bookkeeping features, not proof of a
valid review or QA result.
