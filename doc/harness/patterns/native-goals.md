---
freshness: current
status: accepted
owner: harness
---

# Native Goals

## Current public model: one WBS

A native Goal supplies an objective and persistent continuation. Harness uses
that same objective's WBS root through `wbs(action="open", objective=...)`.
Ordinary requests use the same public tool and tree. There is no second Goal
baseline, manually maintained child queue or progress estimate. PLAN owns
rolling outcome intent; explicit checks earn measured weight, and internal
execution envelopes preserve existing independent review/QA and close proof.

Use wbs context/plan/check/next/dispatch/verify/close/block for that root.
Keep outcomes coarse until useful details emerge; do not specify edited files
or freeze the code layout for progress calculation. Dispatch supplies current
writer ownership to the existing isolated-worktree pool and parks the parent.
After real integration, reopen the same root for integrated checks and
independent review/QA. See ../REQ__measurable-wbs-progress.md.

## Explicit migration

Retired Goal tools and task aliases are unavailable. Use
`wbs(action="migrate")` to preview existing Goal records and old WBS pack
bindings, then `wbs(action="migrate", apply=true)` to publish the conversion.
Normal WBS operations do not interpret the retired ledger.

The importer preserves original records in an archive before publishing
hash-bound destinations and retires sources last. Retry uses that manifest and
refuses externally changed sources or destinations. Historical objectives and
child intake become provenance-labelled notes, not an executable queue or
confirmed user requirements. Existing TASK, run and pool identities remain
unchanged; completion still requires current proof. A migrated unstarted pack
has no invented task/run or measured progress. The report supplies its exact
retained pool intake for recovery. First planning waits for pack integration.

A missing legacy pool must be initialized with the exact previewed intake
before applying migration; initialized pools keep their reservations and proof.
After migration, resume the returned WBS identity through the normal workflow.
