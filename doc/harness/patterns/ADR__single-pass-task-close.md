# ADR: Single-pass task close

Status: accepted

## Context

`task_close` previously evaluated source, review, QA, CHECKS, HEAD, and receipt
state, cleared its request cache, evaluated the gates again, then compared a
third dirty-path snapshot with the first. In a workspace with multiple Git
roots this multiplied the slowest source enumeration by three. The extra work
only protected the short interval in which an external editor or process could
mutate source, HEAD, CHECKS, or receipts while the close call was running.

## Decision

The default Harness close path is single-pass:

```text
sync changed paths
→ evaluate context, freshness, and CHECKS once
→ read HEAD and receipt fingerprint once
→ publish closed state and close attestation
```

Review and QA receipts remain required and must be fresh when close evaluation
begins. Missing or invalid CHECKS, HEAD, receipt evidence, and publication
failures retain their existing blocking or rollback behavior.

Harness does not attempt to detect a mutation that races after the single gate
evaluation starts. Developers own external concurrency in their local
worktree. A subsequent lifecycle call treats the resulting Git and task state
as current truth.

## Consequences

- Multi-root close performs one dirty enumeration phase per root instead of
  three.
- The close handler and its race-only tests are substantially smaller.
- A concurrent external mutation can produce a close attestation spanning
  slightly different instants.
- Teams requiring serialized close must provide workspace/process locking
  outside Harness or restore the former revalidation loop.

## Alternatives rejected

- A separate Lite mode would duplicate lifecycle behavior and test surfaces.
- A persistent SourceEvidence schema would add migration and invalidation code.
- Raising timeouts would retain repeated work and move the failure threshold.
