# Ready-lane dispatch

## Assign before source edits

A bound managed lead consumes its parent's existing exact assignment: validate
the actual session, task, run, registered workspace and assigned ownership. It
does not publish dispatch prepare or refill the parent pool. Its nested helpers
share the parent's assigned spare capacity; they do not create another pool.
The coordinator alone owns the prepare/refill operations below.

WBS describes measurable outcomes, dependencies and acceptance. Keep file lists
out of progress WBS. Choose literal execution ownership at dispatch time.
PLAN/REQ preparation stays possible before assignment; maintenance and micro
source work use the same assignment gate.

Call the public `wbs(action="dispatch", operation="prepare", ownership=...,
host=...)` with the current pending outcome graph and actual observed host:

```json
{
  "capacity": 6,
  "live_agents": [{"id":"actual-coordinator-id","role":"coordinator"}],
  "reserved_slots": [],
  "verification_reserve": 1,
  "spawn_available": true,
  "continuation_available": true
}
```

Inventory includes every still-occupied host slot, including completed agents
until the host releases them. `reserved_slots` contains distinct actual
reservations with `id`/`role`, never invented placeholders or duplicates of live
agents. Exactly one live coordinator is required. Keep at least one independent
review/QA slot; an occupied/reserved supported review or QA lens satisfies that
reserve once. Unknown roles count as occupied but do not satisfy it. The host
snapshot is caller-observed capacity, not authentication or a host reservation.
The MCP cannot independently discover a newly freed external agent slot.

Prepare derives assignment from the existing graph, ownership and capacity.
Two independent ready outcomes plus usable slots require immediate managed
parallel worktrees. It chooses a disjoint pair over a broad conflicting first
request without rewriting the full immutable intake. One ready outcome,
concrete file collisions/predecessors, unavailable capabilities and measured
capacity constraints produce structural serial facts. Do not invent a serial
reason, reduce capacity or use estimated size as a bypass.

Managed handoffs name the owner task, base commit, branch, checkout, ownership,
dependencies and acceptance command. Bind the actual worker before edits. Each
lead develops, independently reviews, verifies and closes in its own managed
checkout. Preserve user changes; use the existing batch resume/recovery flow.
A direct assignment permits only its selected ownership; changing source bytes
alone does not stale it. Changes to run, graph, checks, blocks or ownership
require a fresh prepare. Unmeasured means current evidence was not measured;
it does not request new implementation. Integrated parent verification uses
its existing pack proof rather than creating another pool.

## Completion-driven continuation

As soon as A finishes development, start A's own review then QA while B keeps
developing. No whole-wave barrier. One coordinator integrates stopped, closed
results serially through `dispatch complete`; it owns Git integration and
cleanup. For the coordinator, every relevant completion, dependency release, failure, reservation
release or host-capacity change requires a fresh actual host inventory and an
immediate `dispatch prepare` for the SAME WBS. Consume its ready handoffs before
waiting for unrelated siblings. In particular, after A integrates, prepare C
while B remains running. The completion response explicitly requests this
refill; it cannot spawn agents or fabricate a reservation itself.

Keep unaffected review/QA and execution records bound to their exact input
targets. Recheck only changed/affected inputs; documentation or missing cached
artifacts alone do not invalidate unchanged product proofs. Existing verifier
resource/output leases serialize the same VM, display, port or build output,
while independent development continues. Resource wait is not a reason to stop
unrelated source work. Never convert skip, unmeasured work or failure to
PASS. Source assignment is not review/QA evidence.

## Nested helpers and progress

The existing `parallel_dispatch.py` is a stateless proposal helper for bounded
nested roles; it grants neither new capacity nor task ownership. Reserve from
the coordinator's same observed inventory, not a fresh per-lead cap. Preserve
independent review-before-QA order. Gate authority remains with owner MCP and
prewrite; display routing uses stored state without runtime authentication.

Use `wbs check` for earned outcome evidence. Plan additions, splits and
assignment changes earn no progress. After two hours without earned progress,
refine only unclear unfinished work without changing weights; report actual
external blockers instead of recursively splitting waits. WBS serves delivery.
