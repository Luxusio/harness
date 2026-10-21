---
name: batch
description: Coordinator procedure for running several independent harness tasks in parallel from one session, one harness:task-lead per linked git worktree, rebased, fast-forwarded, and verified afterward.
argument-hint: <N requests, each with a slug and declared path scope>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent, SendMessage, Skill, mcp__plugin_harness_harness__wbs
---

Run independent Claude worktree tasks in a bounded pool: fill, collect,
serially integrate, and refill without waiting for a whole wave. Codex uses
its internal batch workflow and the same pool helpers, with authenticated
worktree coordinator/lens identities. WBS dispatch owns the exact batch ID and managed worktrees; reuse its
returned handoffs and reopen the same root after all leads land.

`doc/harness/REQ__batch-state-pool-recovery.md` owns the command/state contract;
`doc/harness/REQ__parallel-tasks-via-worktree-leads.md` explains worktree shape.
`doc/harness/REQ__batch-submodule-support.md` owns the selected-module S1 contract.
Persistent records under `doc/harness/runtime/batches/<id>.json` are operational
metadata, never task authority or review/QA evidence. Never edit them by hand.

The owning WBS is the immutable pool owner. Preserve its binding on every
retry; never attach a different root to an initialized pool. Standalone CLI
recovery remains supported by the owner, outside the normal managed route.

## a) Intake

Keep WBS outcomes coarse and measurable; do not turn progress accounting into
an up-front list of files to edit. At actual dispatch, pass execution-only
`ownership={<pending outcome ID>: [literal relative scope, ...]}` covering the
pending outcomes. The owner derives request slugs, titles and dependency edges
from that same WBS; use its returned identities rather than inventing another
request graph. Declare independence only when the work can actually run apart.
Dependencies and overlapping scopes serialize until confirmed integration and
cleanup. Slugs must be distinct within the batch; task/archive identity
collisions refuse. Never replace the exact pool to bypass a retained scope.

Existing standalone owner intake remains a list of
`{slug, request, scopes: [relative paths], depends_on: [slug], submodules: [module paths]}`.
Optional `submodules` selects populated direct modules covered by literal
scopes; omission keeps default exclusions. Public WBS dispatch does not add a
separate submodule-selection field. Preserve any existing selected S1 handoff;
selected modules reserve the whole gitlink, including sibling descendant scopes.
S1 rejects recursive/unpopulated modules and topology changes; unsupported work
stays sequential. The selected-module REQ owns this opt-in contract.

The preparation preflight classifies every declared scope path. Only a
`tracked-area` or validated `selected-submodule` scope may run in a lead.
Selected modules reserve the whole gitlink, including sibling descendant scopes.
A request with an unselected scope
`inside-submodule` or `inside-ignored-nested-repo` is not batched: run it as
an ordinary harness task in the main checkout, before or after the pool, never
while leads run. An `outside-root` scope cannot run in a batch lead
at all.

## b) Managed preparation

Use the existing `wbs` MCP tool for normal WBS-owned execution, not a remembered
sequence of shell init, worktree creation, result, finish and close commands:

`wbs(action="dispatch", wbs_id=<owning WBS>, operation="prepare", ownership={<pending outcome ID>: [<scope>, ...]}, available_slots=N)`

`prepare` is the default operation. Initial preparation supplies current
execution ownership; retries of an already-bound pack may omit it. Reuse the
exact pack and batch ID.
The owner parks the parent before worktree effects, records reservations and
exact creation intent, and creates the registered worktrees plus validated
`.harness-batch-bootstrap` markers before returning handoffs. A replay returns
existing owned handoffs; matching HEAD alone never authorizes foreign adoption.
Do not run `git worktree add` or ask the host to create another checkout.

Preflight still owns control-root shape, clean root/nested repositories in strict
standalone/S1 flows and scoped disjoint user changes in managed ordinary flows,
literal source scopes, selected S1 modules, ignored worktree location and
post-checkout-hook hazards. Refusals preserve work and name the blocker. No main
checkout task may be open while leads can emit lifecycle events; dispatch parks
its own parent, never an unrelated task. `.claude/worktrees/` must be ignored.
Claude native `worktree.baseRef` does not select this MCP-owned checkout.

Count coordinators, leads and nested workers against the actual host limit.
Reserve at least one slot for independent review/QA and pass remaining slots as
`available_slots` (minimum zero) on every preparation/refill. Default lead cap
is 3, bounded by the configured batch cap and real host capacity. No free slot
means no spawn. Dependencies and overlapping scopes serialize; dependencies
release only after their predecessor integrates and cleanup completes.

## c) Bootstrap and bind before work

Spawn the independent returned handoffs in one assistant message; spawn each as a bootstrap-only `harness:task-lead`, without
native worktree isolation. Its prompt includes request, slug, exact `task_id`,
MCP-created W/branch, spawn HEAD, main checkout, batch ID, scope, off-limits,
selected modules and bounded pytest worker cap (default 4). Pass the exact
checkout explicitly; the agent verifies registration, branch/HEAD and helper
marker, then returns its identity and stops. No source or task mutation is
permitted before bind. Do not pass `isolation: worktree` or accept a substituted
host-created checkout. The marker is operational metadata, never evidence.

Obtain the actual native worker ID from the host, then call:

`wbs(action="dispatch", wbs_id=<owner>, operation="bind", slug=S, worker_id=<actual host ID>)`

The owner uses its recorded W/branch; callers do not choose them. S1 private
clone preparation belongs only to bind. Require successful running binding and
all selected modules prepared, then continue that same bound agent through
native `SendMessage` with explicit mutation permission and the prepared-module
handoff. Retry interrupted bind only with its exact identity and resources.
Missing authenticated identity or same-agent continuation is a blocker; never
replace it with a fresh isolating spawn or fabricated proof.

Use explicit working directories for every shell command and `workspace=W` for
every lead WBS call. Preserve the bootstrap marker, including blocked and
zero-source returns. It is removed only by managed disposal after harvest. Unknown marker bytes,
links, tracking or other dirty files refuse; standalone finish has no exemption.

## d) Complete, recover and refill

For a newly required scope, stop the affected lead and all its nested writers.
Use `wbs(action="dispatch", operation="extend", slug=S, worker_id=<bound ID>,
worker_stopped=true, expected_head=<observed full SHA>, expected_run_id=<same run>,
scopes=[<added literal paths>], reason=<concrete discovery>)`. The owner rejects
prior out-of-scope edits, stale identity, held conflicts and S1 pools. It keeps
the original pack, checkout, marker and evidence; queued overlapping work waits.
Update that lead's existing PLAN/current ownership and explicitly continue the
same worker only after the grant. Unrelated work need not stop. See the existing
batch-state recovery REQ for migration and refusal details.

A lead returns its actual fenced JSON object with `task_id`, `worktree`,
`branch`, `commit`, `verdict` and `blocked_reason`. Confirm through the host that
that worker and all nested writers stopped. Silence or elapsed time is no proof.
Then call:

`wbs(action="dispatch", wbs_id=<owner>, operation="complete", slug=S, worker_id=<bound host ID>, result=<actual JSON object>, worker_stopped=true)`

This records the exact result and, for genuinely closed leads, runs serial
rebase, fast-forward, evidence harvest, worktree removal and `git branch -d`.
It closes an eligible fully disposed pool without another remembered close
call. It never infers closure from narrative PASS, an archive, or an unreceipted
review result. Replayed completion must match the recorded result even when W
has already been removed. Partial success retains its checkpoint and exact
remaining action; `integrated_tip` always takes precedence over a cleanup error.
No manual force removal, stash, merge-commit fallback or forced branch deletion.

Integrate completed leads in completion order and refill through `prepare`
without a whole-wave barrier. Never run integrations concurrently. Conflicts,
fast-forward refusal or unknown outcomes halt new starts and integration;
collect other workers and preserve their results. Review the shared
`skills/run/worktree-completion.md` ownership and ignored-content requirements
before completion. Selected S1 preservation, reconciliation and guarded disposal
remain exclusively owned by the existing helpers.

On interruption, call `wbs(action="dispatch", wbs_id=<owner>, operation="status")`.
It is read-only. WBS context/next and existing session/prompt hooks also expose
pending pools without requiring an active task; advisory discovery never runs
Git cleanup or grants permission. Confirm stopped writers before either:

- `operation="recover", slug=S, worker_stopped=true`: reconcile the original
  integration/archive checkpoint and follow its exact next action. After an
  independently reviewed conflict resolution, optional `recovery_commit=<exact
  resolved branch-tip SHA>` explicitly acknowledges that tip. Only the original
  pre-integration checkpoint can accept it, preserving exact W/branch/task/run,
  close proof and destination ancestry. Never use it to adopt unexpected
  commits after integration; normal `complete` still matches the original result.
- `operation="resume", slug=S, worker_stopped=true`: reserve the same retained
  W/branch/task/run. Prefer the original agent; otherwise use non-isolating
  `harness:task-lead-resume` bootstrap-only, obtain its real ID and bind through
  MCP before continuation. Preserve existing evidence; never `fresh_run` merely
  to resume or obtain missing receipts.

Blocked/failed leads remain retained with exact blockers. Unknown liveness
never authorizes unlocking, expiry or disposal. Only an explicit user choice permits abandonment: it retains source, branch,
evidence and unresolved scope ownership, never cancels/closes the task or
satisfies the WBS. Abandonment and unused-reservation release remain exceptional
owner commands in `batch_state.py`; they require actual stopped-writer proof,
and release additionally requires confirmed no external work. Do not edit records or bypass checkpoints with direct
finish. The detailed command/state contract remains in the linked batch REQ.

## e) Integration verification and recovery

After all managed requests integrate and their pool closes, reopen the same
owning WBS in main through `harness:run`, then perform combined verification
below. No separate integration child or ledger is needed for that WBS pack.
For a standalone batch use its ordinary main integration task. First confirm
every lead and nested writer has stopped; unknown liveness forbids opening a
main task. Queued or halted work remains durable.

If integration halts, a separate recovery task may resolve the concrete conflict
after all writers stop. This repair does not complete the pack. Resolve in the
recorded lead checkout and preserve its task/run/archive; then return to the
same pack and follow managed recovery. Never claim all work integrated merely
because the recovery task closed.

1. For a conflict, rerun the rebase under that recovery task in the lead W
   (`git -C <W> rebase --no-autostash <destination-tip>`), resolve each stopped
   commit, stage only intended paths and use `GIT_EDITOR=true git -C <W> rebase
   --continue`. Independently review the resolved result, then call managed
   `recover` with `worker_stopped=true` and `recovery_commit=<exact reviewed
   resolved branch-tip SHA>`. Follow its exact next action. Existing
   owner `finish --resume` remains the recovery backend; never bypass its saved
   identities or create a merge commit. Clear the halt only through validated
   owner recovery, then complete remaining leads before combined verification.
2. Run the full suite (e.g. `uv run pytest tests/ -q`).
3. Run fresh `review-code`, conditional `review-security`, then `qa-cli` in
   receipt-backed review-before-QA order. Scope `review-code` by § Batch integration
   review scope in `plugin/skills/develop/quality-audit-pipeline.md`: give the
   reviewer each lead's `old_base`, `old_tip`, `new_base`, and `new_tip`, its
   patch-id result, and carried or residual status.
4. In this plugin source repo, before `wbs(action="close")`, run
   `python3 plugin/scripts/install_verified.py --task-dir <integration task dir>`
   — this is the only place `install_verified.py` runs in batch mode; leads
   skip it.
5. Close the integration task only after the owning pack's integrated archive,
   destination ancestry and actual owned worktree/branch absence prove disposal.
   Managed completion already closes eligible pools; inspect `status` rather
   than remembering a separate batch close. Operational closure never replaces
   review/QA or closes the WBS itself. A halted recovery task does not complete
   the pack: return to the exact pool and resolve retained work first.

## f) Host visibility

Tell the user their host git client (e.g. GitKraken over a drvfs mount) can
inspect this work through **branches and commits only**. Never instruct the
user to open a worktree folder directly on the host. Never run
`git worktree prune` or a host client's worktree cleanup while any lead
worktree exists: from the host every container worktree path looks missing,
and prune deletes the metadata of every **unlocked** one (a host/native lock, when present, protects its registered checkout; managed
worktrees must not rely on that lock always existing, and the unlock → remove
gap never has it).

## g) Report

Give the user a table: task slug → branch → verdict → the lead's returned
`commit` → integrated tip (`integrated_tip` from `batch_finish.py`). Instead
of a tip, write "kept, unmerged" for blocked/failed leads and, with the
`reason`, for closed leads the script kept before the fast-forward (merge
commit, dirty worktree, rebase failure); write "integrated, worktree kept"
with the tip and the `reason` for a `kept` result that carries an
`integrated_tip` (harvest or removal refusal), or "integrated, branch kept"
when its `cleanup.removed` is true (`git branch -d` refusal). A confirmed
`integrated_tip` takes precedence over status, including `error` or
`ff-refused`: report that integration succeeded and include the failure and
remaining cleanup. For a failed merge attempt with a null tip, inspect
`reason`: report "integration unknown" when reconciliation could not finish,
and "not integrated" only when absence was confirmed. Closed leads whose
integration was never attempted are "not integrated". Note every lead
whose result has `trailer_present: false`: its commits carry no
`Harness-Task` trailer. Add a row for every request the
preflight kept out of the pool: "excluded (ordinary task, done or pending)",
"queued (dependency or retained scope)", or "outside the root, not batchable". End with
the integration task's verdict and operational batch disposition. Label abandoned
requests as retained work or never dispatched, never as completed implementation.
Distinguish unresolved retained originals from
verified recovery: report the recovery destination/tip and actual worktree and
branch removal, or the retained path/branch, blocker and next action. Do not
claim overall completion while owned cleanup remains unresolved.

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
