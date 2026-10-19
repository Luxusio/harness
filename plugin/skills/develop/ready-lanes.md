# Ready-lane dispatch

## WBS worktree routing takes precedence for independent development

At active phase boundaries read `wbs(action="context").progress`. After two hours
without earned weight, refine only unclear unfinished WBS outcomes through
`write_plan`, preserving weight while adapting actual next steps. Report real external blockers
instead of recursively splitting waits. Use `wbs(action="check", node_id=...)` for ready outcomes. Report measured weight and concrete remaining
outcomes with integration/review/QA separately; task additions, plan rewrites
and splits earn nothing. One leaf is enough for small work. WBS exists to help
delivery, not to generate progress-reporting work.

Build dependencies from WBS outcomes; choose current ownership only when
actually dispatching concurrent work, never to prescribe a layout for progress. Substantial
independent outcomes run in separate worktrees through `harness:batch`, with
development, independent review, QA and task close inside each lead. Use
`wbs(action="dispatch", ownership={"<outcome id>":["<current scope>"]})`
to bind the exact work pack for an already-open
ordinary execution envelope. Dispatch parks the parent for the actual
worktree handoff before pool admission; do no parent source writes
while leads run. `task_start` refuses resumption until that exact pool is
integrated and closed. Then resume the same parent, verify WBS assertions in
the integrated checkout and run combined review/QA. Never treat a proposed
pack or a lead's narrative PASS as integrated progress.

When independence is known at intake, prefer declaring/running the work pack
before opening its integration task. Native Goals use the same WBS root and dispatch; keep no second Goal queue. Use the shared pool's capacity,
scope reservations, dependency-after-integration rule and serialized finish;
do not run concurrent merges into one target branch. Refill as leads finish.
Do not create worktrees for tiny or dependent work merely to satisfy a count.
The scheduler below remains useful for bounded role pairing within a worktree
and for small direct work; it does not authorize independent source writers
sharing the parent checkout instead of this worktree route.

## Bounded role admission

Use `scripts/parallel_dispatch.py` on both runtimes to calculate which declared
ACs can start. It returns proposals; the coordinator owns actual tool calls and
must reserve the whole returned group against fresh host inventory before
spawning. It never writes task state or receipts.

Build an ephemeral JSON snapshot from PLAN and observed worker state:

```json
{
  "repo": "/absolute/checkout",
  "host_capacity": 4,
  "ac_cap": 4,
  "acs": [
    {"id": "AC-001", "files": ["src/a.py"], "tests": ["tests/test_a.py"], "depends_on": []},
    {"id": "AC-002", "files": ["src/b.py"], "tests": [], "depends_on": []}
  ],
  "progress": {},
  "live_agents": [{"id": "root", "role": "coordinator"}]
}
```

Run `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parallel_dispatch.py --input <snapshot>`.
The snapshot can live in an ignored task work directory. Do not add fields to
TASK.json or PROGRESS.md: this is disposable scheduling input, not new task
authority. Use literal repo-relative paths and pass the checkout to resolve
aliases. Unknown dependencies, cycles and unsafe paths refuse dispatch.

Read `develop.fanout_cap` once: absent/invalid -> 4; positive integer -> min(8,
value), excluding booleans. Record the effective cap/source in the lane table.
The host's actual total agent limit is a second, stricter bound. Inventory
includes the coordinator, all live workers/reviewers, and reserved-but-not-yet-
spawned slots. When finished agents still consume host capacity, keep them in
inventory until the host releases them. If the host exposes no numerical
capacity, do not invent a numeric snapshot: bypass parallel admission and run
one scoped worker at a time (or inline if spawn is unavailable), explicitly
unpaired, reporting the capability fallback. Never retry a failed spawn by
blindly spawning more.

For each `dispatch` group, reserve its `slots` before issuing worker calls.
`paired` requires an implementation worker and test-author together; prompts
give each only its returned paths. With only one usable host worker slot, the
explicit `unpaired` fallback assigns both sets to one worker. A temporarily busy
host waits for pair capacity rather than silently weakening pairing.

After a worker result, release only slots the host has actually freed. Mark the
AC `verifying` until its own implementation and test author finish; run scoped
checks, then mark `completed` with `verified: true, workers_released: true`.
Full-tree verification commands wait until all relevant writers stop. Do not
wait for unrelated siblings before refilling ready lanes. Failed/blocked ACs
keep dependents waiting; retained live writers keep their paths reserved.
`reservation_failed` never triggers another spawn until the coordinator has
observed what started, released only unused reservations, and reset the lane.

Recompute after each completion, failure, release or host-capacity change.
Keep running groups in inventory when a limit falls; start nothing above it.
The coordinator remains the sole progress writer. Review-before-QA order and
independent close evidence are unchanged; scheduler output is never a verdict.

Nested workers need explicitly allocated spare slots from the coordinator's
same inventory. A lane cap does not grant each worker its own fresh host cap.
No assigned spare slots means no nested fanout.
