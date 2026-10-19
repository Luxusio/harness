# Measurable WBS progress

One WBS tree contains any objective and its outcome breakdown. `wbs` is the
only advertised Harness MCP entry point: open, context, plan, check, next,
dispatch, verify, close and block operate on that same root. Native Goals
provide the objective and continuation context, not a second planning ledger.
Internal tasks retain execution identity, receipt ownership and compatibility
with existing runs; their old handlers remain callable but are not advertised.

All work reports verified outcome weight from the declared WBS,
never an agent's percentage, elapsed time, task count or scheduling status.
PLAN.md owns task intent in one `wbs` JSON fence. Each leaf names an observable
outcome and positive weight. Choose its assertion command as argv when ready
to verify, not necessarily at intake. WBS must not prescribe edited files,
directories or implementation structure for progress calculation. Existing
acceptance criteria may be the WBS leaves: do not maintain two descriptions
of the same work. One leaf is enough for a small task.

The control-plane check operation executes only the selected declared assertion
in the selected checkout. Its result binds the leaf definition, current task
run and an internally observed checkout fingerprint. A failed, stale, missing or timed-out assertion
earns no credit. A passing assertion is evidence of that assertion, not proof
that the chosen assertion covers the user's intent; independent review and QA
remain responsible for adequacy. Verified weight and task/Goal completion are
separate fields. Receipt-backed close and integrated batch proof remain the
completion authority. Unknown historical baselines report a null percentage.

There is no separate Goal baseline, task queue or manually duplicated outcome
mapping. Worktree execution envelopes attach to outcomes inside this tree.
Adding an execution envelope or changing its caller status earns no progress.
Baselines preserve existing identities and weight envelopes. Keep the early
WBS coarse and refine it as work reveals useful next steps. Pending outcomes,
dependencies and assertion choices may adapt without predeclaring the code
layout; changing an outcome/check invalidates its old proof rather than
pretending the new criterion was already verified.
Pending leaves may become parents of more concrete leaves, conserving weight;
completed leaves cannot be subdivided or reweighted to manufacture credit.
Newly discovered outcomes may expand scope: the denominator grows, the scope
revision is visible, and neither earned weight nor its clock advances. Pending
implementation details stay adaptable; the percentage describes the current
declared outcome scope rather than a prediction of effort or time remaining.

After 7,200 seconds without an increase in verified weight, the next active
workflow boundary identifies unfinished work for useful decomposition. Plan
rewrites, retries, polling and splits do not reset this clock or earn credit.
The agent refines only work whose next action or check is too coarse, using
`write_plan`; externally blocked work instead names its blocker and unblock
condition. This is activity-driven, with no background daemon, forced idle
updates or recursive bookkeeping splits. Preserve the user's actual goal.

Dependency-ready, independently owned substantial WBS work uses the existing
batch pool and one linked worktree per request. Develop, review, test and close
each lead in its own checkout. Integrate completed leads through the pool's
serialized rebase/fast-forward, evidence harvest and cleanup, then review and
test the combined destination. Never run concurrent target-branch finish
operations. Dependencies release only after integration. Keep blocked work and
conflict recovery intact. Small or dependent work stays in one checkout.
Declare concrete ownership only at the actual worktree handoff, separately
from measured WBS outcomes. It protects concurrent writers and must follow
the current code and discoveries, not prescribe an early implementation layout.

Dispatch uses current execution ownership, checks the existing clean-destination
boundary and durably parks the parent before admitting any lead. Its coordinator
performs no source writes while parked. Reopen the same WBS after the pool
finishes and check integrated outcomes, then apply independent review/QA.
A parent awaiting worktree results remains unfinished. No new worktree manager
or verification ledger substitutes for existing batch/task authority.

Verification: WBS regressions cover weighted credit, stale source/run evidence,
invalid graphs, stable splits, two-hour boundaries, unknown baselines and unified
tool routing. Existing batch tests cover isolation, actual closed leads, archived
evidence, ancestry, conflicts and serialized integration.

The internal checkout fingerprint is conservative: source changes invalidate
old current evidence until assertions are rerun. Reads execute no assertion.
Fingerprint scanning is bounded to 4,096 files and 32 MiB; above that budget
progress is explicitly unavailable rather than estimated. This avoids making
progress calculation an unbounded repository traversal.
