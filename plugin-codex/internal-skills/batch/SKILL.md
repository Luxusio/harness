---
name: batch
description: Execute declared independent requests through the shared batch pool with Codex worktree coordinators.
user-invocable: false
---

# Codex batch

Read the shared batch procedure at
`${HARNESS_PLUGIN_ROOT}/internal-skills/batch/shared-procedure.md` once. Its
preflight, reservation, S1 selected-module preparation, result validation,
serial finish, recovery and retention rules apply. This file adapts only
Claude's spawn and same-agent continuation calls. MCP preparation pins the exact spawn HEAD for both runtimes; Claude native
isolation/baseRef does not choose a checkout. Do not execute Claude
`Agent`, `SendMessage` or `Skill` examples on Codex.

For WBS dispatch use the exact returned batch ID and request list; existing
state is resumed, never replaced with another ID. Reopen the same owning WBS
for combined verification only after the whole pool integrates and closes. No main-checkout task may be open
while leads run. Independent declared requests automatically use this route
when the host exposes spawn and same-agent continuation capabilities.

The owning WBS is the immutable pool owner. Reuse its exact pack and batch ID;
never initialize a replacement pool to bypass retained work or a halted state.
Use the shared CLI only for explicit owner recovery, not normal orchestration.

## Capacity and capability

Use the exposed `spawn_agent`, same-agent continuation (`followup_task` or the
host equivalent), and live-agent inventory. Count the coordinator, running
leads, nested workers and unreleased finished agents. Leave at least one slot
for lead review/QA; cap lead admission to the smaller of `batch.max_leads` and
free slots minus the remaining verification reserve after counting actual live
or reserved supported review/QA roles once. Pass current observed `host` capacity,
live_agents, reserved_slots, verification_reserve, spawn_available and
continuation_available to each managed `prepare`; do not invent reservations. Assign nested workers capacity from that same
inventory, never a new per-lead host limit. At a host capacity too small for
one coordinator, one lead and an independent reviewer, report the capability
blocker before reservations rather than creating an unfinishable lead.

The shared MCP must support authenticated worktree task bindings. An old installed runtime rejecting workspace, missing native
ancestry, or unavailable independent evidence is a concrete blocker. Do not
drop `workspace` or bypass task ownership to get around it. If no pool has been
declared yet, dependent or unavailable work uses the ordinary sequential task
route. Once a WBS work pack is declared, preserve its intent and report its
specific capability blocker instead of silently marking the pack complete.

## Bootstrap and bind

1. Call `wbs(action="dispatch", wbs_id=<owner>, operation="prepare", ownership={<pending outcome ID>: [<scope>, ...]}, host=<current observed host inventory>)`.
   Initial preparation needs execution-only ownership for the pending outcomes;
   retries of the bound pack may omit it. Preparation is the default operation:
   it parks its parent, reserves capacity,
   persists exact creation intent and creates registered W/branch plus helper
   marker before returning handoffs. Repeated preparation returns existing
   reservations safely. Do not run `git worktree add` or native isolation.
2. Spawn a bootstrap-only agent with a unique name; prompt it to read
   `${HARNESS_PLUGIN_ROOT}/agents/task-lead.md`, name exact W/branch, batch ID,
   slug, exact `task_id`, scope, off-limits, selected modules and spawn HEAD.
   Bootstrap verifies the MCP-owned registration/HEAD/marker, returns and stops;
   it neither starts a task nor writes a replacement marker. Record its actual
   native worker identity from the host result.
3. Call `wbs(action="dispatch", wbs_id=<owner>, operation="bind", slug=S, worker_id=<actual host ID>)`.
   MCP uses recorded W/branch; callers do not select them. S1 preparation belongs
   only to bind. Continue that same stopped agent only after successful binding,
   with explicit mutation permission and prepared-module handoff. Use
   `followup_task` when that is the available continuation tool; message-only
   delivery that cannot start a turn is insufficient.
4. Pass explicit working directories to every shell command, absolute W paths
   to edits and `workspace=W` to every lead MCP call. Never change process-wide
   cwd. A failed spawn does not prove no external work exists. Retain and
   reconcile resources with `status`; never blindly allocate replacements.

## Collect, recover and refill

When a lead discovers required scope outside its assignment, stop that lead and
its nested writers. Call the existing dispatch owner with `operation="extend"`,
the exact slug/bound worker, `worker_stopped=true`, observed `expected_head`,
`expected_run_id`, added literal `scopes` and concrete `reason`. Require its
successful effective-ownership handoff before amending that lead's PLAN and
continuing the same agent. Original pack/W/branch/run/marker/evidence stay intact;
prior out-of-scope work, held conflicts and S1 pools refuse. Independent work
continues; queued overlaps wait. The shared batch recovery REQ owns details.

Accept actual host completion only from the bound worker after all nested
writers stop. Call `wbs(action="dispatch", wbs_id=<owner>, operation="complete",
slug=S, worker_id=<bound ID>, result=<actual JSON object>, worker_stopped=true)`.
The same owner validates the result, serializes rebase/fast-forward, harvests
proof and disposes W/branch; it closes a fully disposed eligible pool. No
separate remembered result, finish or close call. Matching retries resume saved
checkpoints, including after worktree deletion. Actual closed task/run evidence
is required; narrative PASS alone never authorizes integration.

Inspect read-only `operation="status"` on interruption. After actual stopped
writer confirmation, use `operation="recover"` for integration checkpoints or
`operation="resume"` for the exact retained W/branch/task/run. After independently
reviewed conflict resolution, `recover` may acknowledge the exact resolved
branch tip with optional `recovery_commit=<SHA>`; only the recorded
pre-integration checkpoint can accept a changed tip, preserving exact identities,
close proof and destination ancestry. Normal `complete` never replaces the
original result or authorizes unexpected new commits. Then follow the
returned next action and bind real worker identity before continuation. Context,
next and session/prompt discovery surface pending pools even with no active
task; they do not mutate Git or assert worker liveness. Preserve shared S1 proof,
conflict/error/retention rules and no-force disposal. Do not rerun a lens solely
for missing recording. Recompute slots and refill via `prepare` without a wave
barrier, but never run integration concurrently or past an unresolved halt.

After all requests integrate and cleanup succeeds, reopen the same WBS root for
combined independent review/QA. Leads skip source auto-install; the root owns
verified delivery. Operational pool closure never replaces WBS verification.
Blocked/abandoned work remains unfinished; report exact retained resources and
blocker. Only the owning pack's completion is gated, not unrelated repair WBSs.

## Durable isolated work and unrelated original edits

Managed ordinary requests may use a dirty original only when user changes are
disjoint from the declared execution ownership. Preparation never stashes or
resets. Standalone and selected-submodule flows retain strict cleanliness.
Finish validates actual changed paths as a subset of ownership and rechecks
disjointness before native fast-forward; unsupported index states retain W
with recovery guidance. Keep worktrees, unintegrated commits and essential
results on persistent repository storage; use temporary paths only for caches.
Resume the exact task/run after checking actual checkout/ref/control presence;
recorded running status never proves a process is alive. Preserve known typed
ancillary metadata outside W before disposal; unknown ignored content retains W.
