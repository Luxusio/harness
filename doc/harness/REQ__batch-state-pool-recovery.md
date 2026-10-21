# Batch state, bounded scheduling and recovery

## Managed MCP lifecycle: intent and derivation (2026-10-20)

User-explicit source: this conversation, “worktree정리 안되는문제를 구조적으로 해결할 방법 없냐? 너의 기억에 의존하는거 말고”.
User-confirmed source: “ㅇㅇ 그렇게 가자” approves the immediately preceding
proposal that MCP own creation, integration, disposal and restart recovery,
with owning WBS completion requiring disposal and unresolved work preserved.

Derived engineering contract: extend the existing `wbs(action="dispatch")`
with lifecycle operations using the existing batch record and lock. Persist
exact creation intent before Git effects. Repeated preparation returns existing
handoffs; it never adopts a foreign path or branch because HEAD happens to match.
Bind the actual native worker before mutation permission. Completion requires
the coordinator's explicit stopped-writer assertion and the existing exact
result/task/run/close proof, then runs integration, archive and disposal without
a separate remembered finish call. Partial success remains retryable from its
original checkpoint. No timeout proves that a worker stopped.

Read-only WBS/session/prompt discovery exposes pending operational work even
without an active task. Hooks neither mutate Git nor authorize cleanup.
Only the owning WBS is gated by its pool; unrelated repair tasks remain usable.
Completion proof includes actual absence of the owned worktree and disposable
branch, alongside existing archive and destination-ancestry evidence.
No second ledger, daemon, forced deletion or fabricated task closure is added.

The coordinator records a batch before dispatching any lead. Its operational
record survives interruption at `doc/harness/runtime/batches/<id>.json`; it is
not task control or review/QA evidence. Only the existing task lifecycle can
publish a closed task. A returned `closed` result must match its recorded
worker, worktree, branch, task and current run, with valid close evidence.

## Managed WBS interface

Normal coordination uses the same public `wbs` tool, with
`action="dispatch"` and the owning `wbs_id`. `operation` selects:

| Operation | Inputs and observable behavior |
|---|---|
| `prepare` (default) | Initial `ownership` maps pending outcome IDs to literal scope lists; bound-pack retries may omit it. Optional `available_slots` limits admission. Bind the exact WBS pack, park its parent, initialize/reserve and create MCP-owned W/branch plus marker before returning handoffs. Existing reservations survive retries. |
| `status` | Read stored state and current observations, including pending integration/disposal and exact next actions, without mutation. |
| `bind` | `slug`, actual host `worker_id`; use recorded W/branch, prepare selected S1 modules, and return confirmed running binding. No caller-chosen checkout. |
| `complete` | `slug`, bound `worker_id`, actual `result` object, explicit `worker_stopped=true`; validate result and task/run/close proof, then integrate, harvest, dispose and close an eligible pool. Blocked/failed work stays retained. |
| `resume` | `slug`, explicit `worker_stopped=true`; reserve the same retained W/branch/task/run for authenticated continuation. |
| `recover` | `slug`, explicit `worker_stopped=true`; reconcile original checkpoints. After conflict resolution under a recovery task, optional `recovery_commit` explicitly acknowledges the exact reviewed resolved branch-tip SHA. |

Public WBS preparation derives requests from pending outcomes and their
dependencies plus execution-only ownership. It does not turn source scopes into
progress milestones or add a separate submodule-selection field; preserve
existing explicit S1 owner metadata without inventing a selection.

Preparation persists exact resource intent before Git effects and rechecks
foreign ownership before reuse. A crash after creation must not require a new
checkout or lose the original reservation. Completion retries first compare the
recorded result; disposal of W does not make replay fail merely because W is
gone. Result, finish and eligible pool close are one managed operation, using
existing batch locks/checkpoints, not one atomic Git transaction. Partial
failure names the exact retained resource and recovery action. No elapsed time
or silence substitutes for the coordinator's observed stopped-writer assertion.

Derived conflict-continuation mechanism: `recovery_commit` is an explicit
acknowledgment of the independently reviewed resolved tip, not a replacement
lead result. Accept a changed tip only from the recorded pre-integration
checkpoint, with stopped writers, exact W/branch/task/run and close proof
preserved, and the recorded destination proven to be an ancestor. Already
integrated checkpoints never adopt a different tip. Normal `complete` always
compares the original result and cannot authorize unexpected new commits.

Context/next and the existing session/prompt context hooks project pending pools
even without an active task. Discovery is bounded, read-only and advisory;
unsafe/corrupt state produces a bounded warning, never hidden success. The
owning WBS refuses completion until archive/ancestry and actual absence of its
owned W/branch prove disposition. Other repair tasks remain usable.

## Owner CLI and exceptional recovery commands

These existing commands remain the internal owner and explicit recovery
interface. They are not the normal WBS coordinator's remembered call chain.

Use `python3 plugin/scripts/batch_state.py --repo ROOT --batch-id ID COMMAND`.
The runtime-installed script uses the same interface.

| Command | Observable behavior |
|---|---|
| `init --requests-file FILE [--max-leads N]` | Store distinct requests, scopes, dependencies, destination branch and capacity. Refuse conflicting existing state. |
| `status` | Read reported state and current observations without writing; show retained work and next actions. |
| `claim` | Atomically reserve eligible requests up to capacity and return current spawn HEAD and off-limits paths. |
| `bootstrap --slug S --worktree W --branch B` | Create only the validated Git-visible retention marker in the reserved checkout; do not write task evidence or batch records. |
| `bind --slug S --worker-id ID --worktree W --branch B` | Bind a reservation to its registered checkout; prepare selected modules before returning `running` and the bound `request`. |
| `result --slug S --worker-id ID --result-file FILE` | Record a structurally matched actual lead result; validate close authority for `closed`. |
| `finish --slug S [--resume]` | Serialize existing rebase, fast-forward, harvest and cleanup with durable checkpoints. |
| `recover --slug S --worker-stopped` | Reconcile interruption using exact saved integration and archive evidence; uncertainty stays visible. |
| `resume --slug S --worker-stopped` | Reserve capacity for the same retained worktree/task generation and provide its handoff. |
| `abandon --slug S --worker-stopped --reason TEXT` | Record explicit abandonment while preserving work, evidence and unresolved scope ownership. |
| `release --slug S --worker-stopped --no-external-work` | Release an unused reservation only after the coordinator confirms no external work exists. |
| `close` | Refuse unresolved active work or cleanup; distinguish retained abandonment from integrated completion. |

Requests are a JSON list of objects with `slug`, `request`, `scopes` and
optional `depends_on` and `submodules` (selected direct module paths). Scopes are literal paths, never shell expressions.
Unknown dependencies, dependency cycles, duplicate slugs and reused task/archive
identities refuse intake. A batch has at most 100 requests. Selection, private preparation and guarded
disposal follow [Selected submodules](REQ__batch-submodule-support.md). Batches
with a nonempty selection use schema 2; new ordinary batches use schema 3. The
reader accepts schemas 1/2/3. Ordinary legacy schema 1 is read without rewriting
and migrates to 3 only on successful owner publication; schema 1 cannot contain
execution extensions. Older owners refuse schema 3 rather than ignore its
additional ownership. Exact init
idempotence includes normalized selection; records cannot silently downgrade it.

The effective cap is explicit `--max-leads`, then optional manifest
`batch.max_leads`, then 3. Positive integers above 8 clamp to 8. Invalid explicit
values fail; invalid manifest values fall back to 3 with the source reported.
Booleans and quoted strings are not integers. Setup need not write this key.

## Pool and ownership

Reserved and running requests consume slots. A completed lead releases its
worker slot; the coordinator serially integrates closed results and immediately
refills eligible slots from the current destination HEAD. A dependency becomes
ready only after its predecessor is integrated and cleaned up. Overlapping
scopes cannot run concurrently; blocked, failed, kept and abandoned sources
continue to reserve their scopes across batches while unresolved. Selected
module ownership reserves the whole gitlink separately from the original literal
scopes; sibling paths in one module serialize, and distinct modules may run in
parallel.

Only one pool may have reserved/running/integrating requests in a control root.
All mutations use the same persistent advisory lock. Status does not wait for
that lock and never rewrites reported state from observations. A busy lock is
reported separately from invalid data. External Git writers do not obey this
lock, so current branch identity, Git operation state, cleanliness and ownership
must be rechecked at their actual use boundaries.

Dispatch is a handshake: MCP reserves and creates the exact checkout and
marker, then a bootstrap-only agent verifies its worktree/branch/HEAD and stops.
Bind its actual native identity before authorizing that same agent to begin.
Bootstrap must not edit source, recreate the marker or start a task before
binding. MCP creates `.harness-batch-bootstrap`, a nonignored untracked regular file containing
the exact `{batch_id,slug,spawn_head}` reservation payload. Bind accepts only
that validated operational marker as a bootstrap change and rejects halted or
unresolved integration, including reservations made before the halt. Never expire
a reservation solely because time passed. For S1, bind also persists preparation
intent and private-store identity, then prepares selected modules before granting
running permission. An interrupted preparation retains the exact reservation;
retry through the same initial `bind` never replaces an unknown partial clone.
A successful selected bind returns `request.submodules` and
`request.submodule_manifest`; resume returns those fields inside `handoff`. A failed spawn requires explicit
stopped/no-external-work confirmation before release.

Both runtimes receive the same MCP-created checkout, never a second native
isolation worktree. The marker remains through bootstrap, blocked/failed returns
and closed tasks without source changes. It also protects earlier retained
native checkouts from unchanged-worktree cleanup. Never stage, edit or remove it
in a lead. Only this validated operational marker is excepted from source
cleanliness; implementation changes still require commits. Continue the exact
bound native agent with `SendMessage` on Claude or the actual same-agent
continuation capability on Codex. Managed completion validates the marker and
removes it only after durable harvest, immediately before ordinary or guarded
S1 removal. Unknown marker bytes, links or tracking never permit a dirt bypass.

No main-checkout Harness task is open while live worktree leads can emit
lifecycle events. After all writers stop, the coordinator runs the integration
task's independent review, QA, verified installation and close. Operational
batch closure never substitutes for those gates.

## Interruption and retained work

`batch_finish.py` remains the integration owner. Its optional checkpoint
callback does not change ordinary callers. Before unlock/removal, persist the
exact post-rebase integrated tip, intended destination ref, task/run/close
identity and harvested archive tree fingerprint. Atomic replacement includes
file and directory synchronization. Before publishing the cleanup checkpoint,
synchronize the archived files, directories, their publication parents and
preserved learnings. Any synchronization or checkpoint failure stops cleanup.

For S1, persist the destination/module witness before fast-forward and update
selected main checkouts immediately after landing. Recovery consumes that witness
before the ordinary dirty guard; only exact old-or-target clean module states
qualify. Missing pins, changed destination identity or unrelated dirt retain work.
The same batch record owns preparation, preservation and integration checkpoints;
there is no second state journal.

Recovery compares saved facts against current Git registration, branch,
ancestry, task closure and archive contents. It never guesses a rebased tip
from the old returned SHA, treats an archive's existence as PASS, or replays
receipts. Missing evidence leaves a recovery-required result. If only branch
deletion remains, report that branch rather than a nonexistent worktree.
Conflicts, fast-forward refusal or unknown integration halt further dispatch
and integration until explicit validated recovery.

Resume accepts blocked/failed work and interrupted running workers whose writers
the coordinator has confirmed stopped. It does not require a fabricated failed
result. If the running worker's generation was never observed, validate and pin
the current open/blocked task run, or explicit task absence, before handoff.
Previously recorded task absence must still hold. Bind rechecks this identity
and refuses a task that appears or changes after handoff. Resume and bind both
refuse while a main-checkout task is open. A failed continuation bootstrap keeps
its bound reservation and retries in the same worktree after stopped-writer
confirmation; releasing a bound worktree as unused is forbidden.

Resume uses the original native agent when available; otherwise use the
non-isolating `task-lead-resume` agent in the exact existing worktree. No
continuation may allocate another checkout.
Call `wbs(action="open", workspace=W, wbs_id=existing_id)` without `fresh_run` so
existing evidence survives. A changed task generation or branch refuses.

Abandonment is an explicit user choice. It does not delete unintegrated source,
force-delete branches, modify task status, or satisfy the Goal. Retained work
and the next disposition action remain visible. Queued requests, including
dependents of an abandoned request, can be explicitly abandoned without ever
being dispatched; report this separately from retained external work. A stopped
integration failure can be abandoned only after reconciling its exact destination
and branch facts as confirmed unintegrated with no Git operation in progress.
Unknown post-effect outcomes or pending integrated cleanup remain recoverable
and cannot be hidden by abandonment. Recompute the halt only after a safe
disposition. Verified integration cleanup
continues to use the shared temporary-worktree completion procedure.

## Ignored content at the ordinary disposal boundary

Derived preservation guard for the approved safe managed-disposal outcome:
ordinary automatic removal checks the ignored inventory immediately before
removal. Unique or unknown ignored content refuses disposal and retains W,
branch and the bootstrap marker. Git cleanliness alone does not prove safety.
Canonical task evidence and learnings must be byte-preserved outside W before
removal; arbitrary ignored user data is not automatically archived or discarded.

Cache exclusions are actual directory trees named `.venv`, `node_modules`,
`__pycache__`, `.pytest_cache`, `.mypy_cache` or `.ruff_cache` only. A same-named
regular file or symlink is not a cache; nested `.git` inside a cache refuses.
The exact canonical owning-task tree must match its archive and checkpoint
fingerprint; every raw learning row must have an outside-W preserved copy.
Only strict five-field session markers for that same closed task/run, a legacy
`.active` pointing to that exact closed task, a `.session-hint` naming its
validated session, and empty regular Harness operational lock metadata are
discardable controls; unknown
controls retain W. No broad `*.log`, `.cache` or private-directory exemption is
permitted. An existing ignored Native checkpoint
is a historical snapshot, never a cache: require outside byte verification and exact
accounting before removal. The typed ancillary preservation path below handles
known checkpoint and diagnostic shapes; unknown contents retain W. The stricter selected S1 inventory and
preservation guard remains unchanged. No second ledger or new MCP argument is
introduced.

## Trust, delivery and verification

State records are versioned and bounded. Unknown schemas, malformed records,
unsafe state parents, symlinks, non-regular or multiply linked files and foreign
ownership refuse mutation. Excess history refuses rather than silently dropping
old scope reservations. Worker identity and stopped-writer flags are assertions
by the coordinator based on host results, not an operating-system liveness
proof. No generic process-killing or agent-spawning service is introduced.

The installed script, workflow and resume-agent definitions ship together.
Both Claude and Codex use registered-worktree lifecycle calls. Codex requires
process-owned native identity/cwd/full-ancestry proof under the shared binding
owner; an unauthenticated shared MCP host does not gain authority from prompt
text or explicit workspace alone. Only explicitly
selected direct modules use S1; unselected modules and ignored nested repositories
remain ordinary sequential tasks.

Verification uses real Git repositories for pool refill, dependencies, retained
scope collisions, concurrent claims, exact resume, rebase/fast-forward and
checkpoint crashes before and after removal. Corrupt task closure and archive
proof must refuse. Status and abandonment tests compare task/evidence bytes to
prove they remain untouched. Retention tests cover the documented Git-visible
host cleanup condition; durability tests assert synchronization order and
retention on sync failures, without claiming to simulate power loss. Contract tests check workflow routing and both
runtime payloads; full QA and native receipt gates remain required.

## Known ceiling

The newly managed native Claude bootstrap/bind/continuation path is not proven
by CLI fixtures in the local Codex development runtime. The same installed helpers are tested with real Git and
process interruption; validate native dispatch when a live Claude runtime is
available. A host without a continuation channel retains the reservation and
worktree and reports that blocker; it does not authorize an unbound worker.

## Derived readonly-view and partial-removal repair

Derived from the approved structural cleanup request, not an additional user
requirement: Git may remove linked-worktree registration before failing on a
sealed verification source directory. After the existing full removal proof and
durable archive checkpoint, only typed archived verification source directories
may gain owner write permission for disposal; their file bytes and the archive
remain unchanged. A Git error remains visible even if marker restoration fails.

Supported ordinary recovery also accepts unregistered partial residue only after
exact integrated tip, task/run/close and archive fingerprint checks. Both Git
registration and administrative back-pointers must be absent. Every remaining
regular file and directory must belong to the integrated commit or exact archived
owning-task tree; learnings require exact outside preservation. Unknown, changed,
linked or special content refuses before mutation. Recheck identities/inventory
at deletion boundaries; this is not a hostile same-UID sandbox. Delete the
retained branch only after the worktree is absent; missing branches remain
retryable. Selected-submodule disposal retains its independent existing proof.

Disposal authenticates historical typed verification copies against the exact
preserved archive, without promoting them to the current run's acceptance
evidence. Learning-ledger preservation uses bounded exact raw-row inclusion in
the destination ledger; unrelated prior rows do not invalidate preserved rows.
Malformed or missing rows still refuse disposal.

An ordinary unregistered residue may contain an uninitialized gitlink
placeholder only when the integrated commit records that exact gitlink path
and the remaining entry is a plain empty directory. Both pre-mutation inventory
passes require emptiness; any entry, including hidden module metadata, refuses
without descending into the module. Populated and selected modules retain their
existing independent preservation requirements.

Partial ordinary residue retains the same existing `ORDINARY_CACHES` directory
categories as registered removal, only beneath an already proved ancestor.
Every cache directory and regular file participates in the bounded identity
inventory and deletion rechecks. Links, special entries and nested `.git`
refuse; an unknown parent never becomes disposable because it contains a cache.
The empty-gitlink requirement takes precedence over cache recognition.

### Derived execution ownership adaptation

When development discovers a required change outside the assigned ownership,
the coordinator stops that lead and its nested writers, then uses the existing
WBS dispatch owner with `operation="extend"`. The grant requires the exact
worker, observed HEAD and task run, literal added scopes and a bounded reason.
The same worktree, branch, task/run, original request/spec and marker remain.
Only current execution ownership changes; no progress or acceptance is earned.

A running owner resumed from a blocked or failed return may retain that
historical nonaccepting result while receiving a grant. The owner requires an
actual resumed binding and preserves the old result, return timestamp, run and
receipt bytes. A closed result, any integration checkpoint or finish result,
or current close proof still refuses extension. Retaining failure history must
not make supported continuation impossible or turn that history into acceptance.

Before granting, the owner checks prior committed, staged, working and untracked
changes against the old effective ownership, including both rename endpoints.
It rechecks pending path contents, modes and index entries before publication;
only enumerated dirty/untracked files are streamed, not the whole checkout.
Each observation refuses beyond4096paths,64MiB or10seconds; a changed or unsafe
file refuses the grant without rewriting the pool.
Metadata-only index inspection refuses `assume-unchanged` or `skip-worktree`
flags that could conceal prior edits; the owner never clears those flags.
Unknown or stale identity, prior out-of-scope work, held ownership collisions,
integration/close effects and selected-module pools refuse without a grant.
Queued overlaps wait on effective ownership; unrelated work can continue.
Exact retries preserve the original grant. Ordinary records migrate to schema3
on owner publication so older owners refuse rather than ignore added ownership;
read-only inspection does not migrate records. Selected-module schema2 does not
gain scope extension. This is a derived repair of the existing user-requested
adaptive work flow, not an additional product requirement or completion claim.

Public WBS `check` accepts an explicit assertion timeout of1..300seconds,
default60. This accommodates real public lifecycle assertions under normal
test-session guards without a custom test driver. Input-measurement budgets
remain separate; `verify` does not accept a timeout argument.



## Scoped ordinary admission and durable resume (2026-10-21)

User-explicit outcome: preserve unrelated user changes while developing in a durable isolated checkout; never stash, reset or overwrite original work. Derived mechanism: only managed ordinary requests may admit destination dirt disjoint from all admitted ownership scopes. Standalone and selected-submodule paths retain strict cleanliness. Compare literal NUL-delimited Git paths, including rename endpoints and ancestor collisions. Actual integrated changed paths are a subset of ownership, not an equality requirement. Recheck the rebased delta and destination dirt immediately before native fast-forward; native Git refusal retains the lead with a concrete recovery action. No custom index restoration is permitted.

Managed repositories and unintegrated commits stay under the repository; temporary caches may be recreated. Resume checks registered W, ref, commit and original task/run. Missing resources retain surviving branches/evidence and report targeted recovery; stored worker state does not establish process liveness. Missing build artifacts do not erase source review results; their applicability belongs the verification-target owner.

Known owning-task checkpoint and watcher diagnostics may be preserved byte-for-byte in a separate ancillary archive before disposal. They never authorize closure or modify closed evidence. Producer diagnostics/checkpoints lack task/run attribution; sidecar task/run names identify disposal context only, never provenance or verification evidence. Only exact known-schema, ownership/type-checked preserved entries are accounted for; unknown ignored user data retains the worktree.

Partial ordinary residue uses that same typed ancillary validation in read-only
matching mode against its already-preserved disposal-context sidecars. Closed
control comes from the immutable task archive when source control has already
been removed. Missing or different sidecars, unknown contents and unsafe
entries refuse before mutation; recovery never creates a new preservation
copy to retroactively authorize removal.
