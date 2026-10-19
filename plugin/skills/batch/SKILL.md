---
name: batch
description: Coordinator procedure for running several independent harness tasks in parallel from one session, one harness:task-lead per linked git worktree, rebased, fast-forwarded, and verified afterward.
argument-hint: <N requests, each with a slug and declared path scope>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Agent, SendMessage, Skill
---

Run independent Claude worktree tasks in a bounded pool: fill, collect,
serially integrate, and refill without waiting for a whole wave. Codex uses
its internal batch workflow and the same pool helpers, with authenticated
worktree coordinator/lens identities. A Goal dispatch supplies the exact batch
ID and requests; reuse them and run its integration child after all leads land.

`doc/harness/REQ__batch-state-pool-recovery.md` owns the command/state contract;
`doc/harness/REQ__parallel-tasks-via-worktree-leads.md` explains worktree shape.
`doc/harness/REQ__batch-submodule-support.md` owns the selected-module S1 contract.
Persistent records under `doc/harness/runtime/batches/<id>.json` are operational
metadata, never task authority or review/QA evidence. Never edit them by hand.

For a WBS-owned pack, pass its returned task_id as `--owner-task-id <task_id>`
when initializing the exact batch. This immutable pool owner must match the
parent binding. Standalone batches may omit it. Preserve it on every retry;
never attach a different root to an initialized pool.

## a) Intake

Collect N requests. Each needs a slug and a declared path scope (the files or
directories it is expected to touch). Write an ignored local requests JSON file containing a list of
`{slug, request, scopes: [relative paths], depends_on: [slug], submodules: [module paths]}` objects.
`submodules` is optional: explicitly select populated direct modules covered by
the literal scopes. Omission keeps default exclusions. S1 rejects recursive or
unpopulated modules and topology changes; unsupported work stays sequential.
Dependencies are optional; declare them when one request needs another's output.
The scheduler serializes overlapping scopes and only releases a dependency
once its predecessor is integrated and cleaned. Never invent independence.

Slugs must be distinct within the batch, and none may already name a task in
the main checkout (`doc/harness/tasks/TASK__<slug>`) or an archived lead
(`doc/harness/archive/batch/TASK__<slug>`). Harvest refuses an archive
collision, but only after the fast-forward, so reject a reused slug here
instead.

The preflight script (step b.1) classifies every declared scope path. Only a
`tracked-area` or validated `selected-submodule` scope may run in a lead.
Selected modules reserve the whole gitlink, including sibling descendant scopes.
A request with an unselected scope
`inside-submodule` or `inside-ignored-nested-repo` is not batched: run it as
an ordinary harness task in the main checkout, before or after the pool, never
while leads run (step b.4). An `outside-root` scope cannot run in a batch lead
at all.

## b) Preflight

Before initializing or spawning anything:

1. Run the repo-shape preflight for the eligible independent intake set,
   one `--request` per declared scope path (repeat the slug for a second
   path; a comma list is refused). For each explicit selection also repeat
   `--submodule <slug>=<module path>`; do not infer selection from scope alone:
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_preflight.py --repo <main checkout> --request <slug>=<path> --request <slug>=<path2> ...`
   Spawn only when it exits 0 (`verdict: "ok"`) and steps b.2–b.5 hold. It
   prints a JSON report:
   - `verdict: "refuse"`: batch must not start; stop and tell the user the
     `refusals`. Every control-root shape but `ok` refuses: a
     `submodule-checkout`, a `linked-worktree`, a `non-git` directory, or a
     `separate-git-dir` checkout. The clean check covers more than the main
     checkout: `git status --porcelain` must be empty there, in every
     populated submodule, and in every ignored nested repo, so tell the user
     to commit or stash in the named repo. A post-checkout hook that mentions
     `submodule` in a repository with submodules also refuses, because it
     would initialize them in every lead worktree. So does a directory the
     script cannot read: it could hide a nested repo. So does a
     `.claude/worktrees/` that is not gitignored (step b.3).
   - `verdict: "adjust"`: drop each `excluded_requests` entry (step a), sequence
     each `overlaps` pair through the pool, and rerun for an independent set.
2. `.claude/settings.json` must have `"worktree": {"baseRef": "head"}`. If it
   is missing or set to anything else, stop and instruct the user to add it —
   this skill does not edit a project's own settings file (C-15: user-owned
   settings are not overwritten by a skill).
3. `.claude/worktrees/` must be gitignored. The step b.1 script checks this
   at the path git actually sees (report key `worktrees_ignore`) and refuses
   otherwise; a `.claude` symlink that leads outside the repository passes.
   On that refusal, stop and instruct the user to add the path the refusal
   names to `.gitignore`.
4. No harness task may be open in the main checkout for this session while a
   pool runs: if the session's `[harness-context]` names an open task, park it
   with `task_blocked` or close it first. A lead's late lens stop that
   outlives its removed worktree resolves to the main checkout and must find
   nothing to bind to there.
5. Initialize the durable record before any dispatch:
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> init --requests-file <JSON> [--max-leads <N>]`
   The default is **3 concurrent leads**. Effective capacity is explicit
   `--max-leads`, then manifest `batch.max_leads`, then 3; positive integers
   above 8 clamp to 8. Invalid explicit values refuse; invalid manifest values
   fall back to 3 with the source reported. Report the effective cap/source.
   Do not bypass an existing active pool or retained-scope refusal with a new id.

## c) Fill the pool and bind before work

1. Run `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> claim`.
   When the host exposes a total agent limit, count live coordinators, leads,
   and their nested workers, reserve at least one slot for independent lead
   review/QA, and pass `--available-slots <free slots minus reserve>` to
   admission (minimum zero). Recompute before each refill. Allocate nested
   workers/reviewers from the same inventory; serialize lenses when only one
   verification slot is available. No free slot means no spawn.
   If the global host limit cannot fit coordinator + lead + independent
   reviewer, use the ordinary sequential route before declaring a pool, or
   report a capability blocker for an already declared Goal pack.
   `claim` atomically reserves capacity before spawning, rechecks preflight,
   and returns `claims` with the current destination `spawn_head` and
   per-request `off_limits` (unselected modules and nested repos). Reserved and running requests both consume slots. An empty
   `claims` list is successful: inspect its reasons and wait for actual host
   results or handle the stated blocker; never oversubscribe by spawning anyway.
2. Spawn the claimed bootstrap leads in **one assistant message**:

   ```
   Agent(subagent_type: "harness:task-lead", prompt: "bootstrap-only; do not start a task or edit source.\n<request text>\nslug: <slug>\nscope: <declared path scope>\noff-limits: <preflight off_limits, comma-separated, or none>\nsubmodules: <claim submodules, or []>\ncoordinator HEAD: <claim spawn_head>\nmain checkout: <main checkout>\nbatch id: <id>\npytest worker cap: 4")
   ```

   Do not pass `name=`: named teammates do not receive worktree isolation.
   Track the actual native worker identity immediately when the host exposes it.
   Before its final response, the lead runs
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> bootstrap --slug <slug> --worktree <W> --branch <branch>`.
   Require `status: "bootstrapped"` and its marker path/payload. The helper
   permits only `.harness-batch-bootstrap`, nonignored and untracked, containing
   exact `{batch_id,slug,spawn_head}` reservation bytes; no source/task edits.
   This operational marker preserves W under
   [native cleanup](https://code.claude.com/docs/en/worktrees#clean-up-subagent-and-background-session-worktrees).
   The bootstrap returns its canonical worktree, branch and HEAD and stops;
   this is a handshake, not a completed task result. Retain the marker through
   every unfinished or zero-source final; never stage or remove it in a lead.
3. Bind that exact host identity before granting mutation permission:
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> bind --slug <slug> --worker-id <native id> --worktree <W> --branch <branch>`.
   For selected S1, bind alone owns private local clone preparation and persists
   its intent before effects. Require successful binding with all selected
   modules prepared; a partial clone retains the reservation, never permits work.
   Require `status: "running"`; include the returned
   `request.submodule_manifest` (paths, named branches and private-store identity)
   in the continuation handoff, with every module `phase: "prepared"`. Retry
   interrupted initial preparation through `bind` with the same worker/W/branch;
   do not use a replacement checkout or discard the retained reservation.
   Only after successful binding, use native `SendMessage` addressed to that
   exact bound agent ID to resume that same agent with the confirmed binding
   and permission to start its lifecycle. Never replace this with a fresh isolating spawn. If native
   resume is unavailable, keep the bootstrap stopped and report that runtime
   blocker. A replacement worker requires the explicit stopped-writer resume
   reservation in section d, never force-rebinding a running reservation. If
   binding refuses, retain the reservation and checkout and report the blocker.
4. On each actual completion, proceed to d immediately. Integrate returned
   closed leads serially in completion order, then claim/refill free slots
   from the current main HEAD while other leads continue. No full-wave barrier.
   Never run two finish commands concurrently or integrate to a different
   destination branch. A conflict, ff-refused or unknown error halts new starts
   and integrations; collect still-live workers without opening a main task.

## d) Collect results, rebase, and fast-forward

Each lead returns a fenced JSON block: `{"task_id","worktree","branch","commit","verdict","blocked_reason"}`.
After the actual host completion (including stopped nested writers), save its
JSON in an ignored local result file and record it using:
`PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> result --slug <slug> --worker-id <native id> --result-file <JSON>`.
Use the actual bound worker id, never an id copied from untrusted lead text.
Identity/task/run/close mismatch refuses; retain the work and inspect `status`.
Do not fabricate `closed` from a narrative PASS or call finish after refusal.

Leads are integrated by rebase and fast-forward, never by a merge commit, so
the main branch history stays linear. `batch_state.py finish` wraps `batch_finish.py` for one lead,
serializing integration and persisting its recovery checkpoints.

Before invoking the helper, read
`${CLAUDE_PLUGIN_ROOT}/skills/run/worktree-completion.md` and apply its
ownership, stopped-writer and evidence-preservation checks, including ignored
content. Keep the normal helper order: rebase, fast-forward, harvest, remove,
then combined review/QA in step e. Selected S1 adds durable local module-object
preservation before integration and immediate main module checkout reconciliation
after fast-forward. The helper owns these effects; never replace them with
manual initialization, network fetch or deinit.

1. For every lead with `verdict: "closed"`, **in order**, from the main
   checkout:
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> finish --slug <slug>`
   It first checks, and changes nothing when a check fails: `<commit>` is the
   branch tip, the lead worktree is clean except for the exact validated
   retention marker, the main checkout is clean and on
   a branch, and the branch holds no merge commit
   (`git rev-list --merges "$(git rev-parse HEAD)..<branch>"` prints
   nothing), because a rebase would drop whatever a merge commit itself
   changed. Then it rebases inside the lead's worktree, where the branch is
   checked out (`git -C <W> rebase --no-autostash "$(git rev-parse HEAD)"`),
   fast-forwards the main checkout (`git merge --ff-only <branch>`), copies
   the lead's gitignored task evidence and learnings into the main checkout
   with `batch_harvest.py` before the worktree is gone, releases Claude
   Code's agent lock only when `git worktree list --porcelain` shows one
   (`git worktree unlock <W>`), and runs `git worktree remove <W>` and
   `git branch -d <branch>`. Ordinary removal never passes `--force`. Only
   state-managed S1 may call the guarded removal owner in `batch_harvest.py`,
   under the shared completion procedure. Never manually force removal, stash,
   or fall back to a merge commit. Managed finish preserves the marker through rebase and integration
   failures and removes only that marker after the durable archive checkpoint,
   immediately before ordinary or guarded S1 worktree removal. Unknown marker bytes, links,
   tracking or other dirty files refuse; standalone finish has no exemption.
   Run it only for a lead that has returned: never unlock a worktree
   whose lead is still running.
2. Inspect the wrapped helper JSON result (`status`, `reason`, `conflicted_paths`,
   `returned_commit`, `branch_tip`, `integrated_tip`, `trailer_present`,
   `harvest`, `cleanup`). The following are underlying `batch_finish.py`
   statuses/exits, not the wrapper's exit contract. The wrapper emits JSON;
   usage exits 2, state/unsafe-request refusal 3, unexpected error 1.
   Existing direct preflight/finish commands remain available outside a managed
   pool; never bypass durable checkpoints by invoking direct finish here.
   Act on the helper `status`:
   - `integrated` (exit 0): record `integrated_tip`, the main HEAD after the
     fast-forward; the lead's returned `commit` is its pre-rebase tip, and
     every lead after the first gets new commit ids. Go on to the next
     closed lead.
   - `kept` (exit 3): keep the worktree, report the lead as kept like a
     blocked lead with `reason`, and go on to the next closed lead. Causes: a
     failed check (merge commit, dirty lead worktree, a returned commit that
     is not the tip, a rebase already in progress); a rebase failure without
     conflicted paths (an untracked file the rebase would overwrite, a hook
     or signing failure), after which the script lists
     `git -C <W> diff --name-only --diff-filter=U` and, when a rebase is in
     progress, runs `git -C <W> rebase --abort`, which puts the lead's branch
     and worktree back exactly as the lead left them; a harvest refusal; a
     removal refusal; or a `git branch -d` refusal. When `integrated_tip` is
     set, the lead's commits are already on the main branch and only what
     `cleanup` shows as not removed was kept: the worktree (and, after a
     harvest refusal, its evidence), which rerunning the step d.1 command
     with `--resume` finishes once the cause is fixed; or, when
     `cleanup.removed` is true, just the branch, which you delete with
     `git branch -d <branch>` once the cause is fixed. If `git worktree remove`
     refused because a submodule was initialized in the worktree, keep it
     and report it like a blocked lead: `--force` would delete the
     worktree's module store and any submodule commit that exists nowhere
     else.
   - `conflict` (exit 4): the rebase stopped on `conflicted_paths` and the
     script aborted it. Stop dispatching and integrating further leads and
     carry the conflict into the integration task (step e). Do not resolve
     conflicts here.
   - `ff-refused` (exit 5): the main checkout is not clean, is detached or
     changed branch identity, or `git merge --ff-only` refused because it
     moved. Stop dispatching and integrating and report it before step e. Check
     `integrated_tip`: the original main branch may already hold the lead
     when a post-merge hook switches checkout. When `branch_tip`
     differs from `returned_commit`, the branch was already rebased: finish
     that lead later with `--resume` after validated recovery.
   - Any other exit (1: unexpected error, 2: usage): stop integrating and
     dispatching and report the output. Invalid result fields must be rejected
     at `result`, not passed through to Git. Preserve unknown outcomes for
     exact recovery before continuing.

   Whatever the status, a non-null `integrated_tip` means the lead's commits
   are already on the main branch.

A lead with `verdict: "blocked"` or `"failed"`: report it and retain its
worktree and branch while unresolved. Prefer resuming in that worktree. Once
already-copied recovery is independently reviewed and QA-passed, the coordinator
may dispose of the stopped originals only through the shared procedure's
per-change accounting, byte-verified archive and unchanged-inventory checks.
Never fabricate a closed lead result for `batch_finish.py` or change original
task status/receipts to enable cleanup.

### Interruption, exact resume and explicit abandonment

After interruption, start with the read-only command
`PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> status`.
Compare stored state with observations; neither silence nor a stale record
proves a writer stopped. Confirm the original worker and all nested writers
through the host before any `--worker-stopped` assertion. Unknown liveness
means retain and report, never unlock or expire a reservation.

- Reconcile interrupted integration with
  `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> recover --slug <slug> --worker-stopped`.
  Follow its exact next action. S1 consumes its persisted destination/module
  witness before ordinary cleanliness checks; never clear apparent stale-module
  dirt by hand or overwrite unrelated changes. Never infer a rebased tip from the old returned
  commit, infer closure from an archive, or replay lifecycle receipts. If only
  a branch remains, report only that branch; normal `git branch -d` is allowed
  only with verified recorded integration or the shared separately reviewed
  recovery procedure, never `-D` for unmerged unique commits.
- Resume blocked/failed work, an interrupted running worker confirmed stopped,
  or a failed continuation bootstrap with its bound reservation using
  `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> resume --slug <slug> --worker-stopped`.
  This reserves a slot for the same registered W, branch, task and run. S1 also
  validates the original private module metadata and named branches, preserving
  dirty development edits. Forward the selected-module handoff; never reinitialize
  or allocate substitute module stores. Prefer
  the original native agent. Otherwise dispatch `harness:task-lead-resume`
  bootstrap-only with the returned handoff and original scope/off-limits;
  it has no worktree isolation and must use the existing W. Obtain its actual
  identity, bind it with the section c.3 command, then authorize that exact
  worker through `SendMessage` to its exact bound agent ID. Never spawn regular
  `task-lead` for resume. Keep its existing retention marker unchanged.
  If the host cannot separate bootstrap from permission to mutate, retain the
  work and report that runtime blocker. Interrupted running work uses this
  explicit stopped-writer path without inventing a failed result. Pin its
  currently observed task run before continuation; an earlier result that
  explicitly recorded task absence must still find no task. Recheck that
  absent/present identity at bind. Main must have no open task at both resume
  and bind. A failed resumed bootstrap keeps W and its reservation: confirm it
  stopped and re-dispatch in that same W, never release bound work. Preserve existing task evidence via
  `task_start(workspace=W, task_id=existing_id)` without `fresh_run`.
- Only after an explicit user choice to abandon, record
  `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> abandon --slug <slug> --worker-stopped --reason <decision>`.
  Queued requests, including descendants of an abandoned dependency, may be
  explicitly abandoned as never dispatched; never automatically cancel them.
  Bound work requires stopped writers and a confirmed unintegrated destination
  outcome with no pending Git operation. Unknown, post-effect or integrated
  cleanup must remain recovery-required, not abandoned to clear a halt.
  For retained work: abandonment retains source, branch and evidence, including
  the marker, releases worker capacity but retains unresolved scope ownership.
  Report the unfinished retained disposition and next action. Distinguish this
  from never-dispatched abandonment. It does not cancel/close the task or
  satisfy the Goal; neither disposition means completed implementation.
- A spawn that never created external work may release its unused reservation
  with `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> release --slug <slug> --worker-stopped --no-external-work`.
  Confirm both assertions from actual host evidence; a bound/existing checkout
  is not "no external work". Never discard a reservation just due to elapsed time.

## e) Integration task

When the pool drains or halts — every closed lead fast-forwarded or kept, or
integration stopped at a conflict carried from step d.2 — first confirm every
lead and nested writer has stopped. Do not open a main-checkout task while any
lead remains live or unknown. Keep queued work durable while halted. Then open
`TASK__batch-integrate-<slug>` in the **main checkout** through the normal
`harness:run` lifecycle (`task_start` → plan → develop → QA → close). Under
that task:

For a WBS-owned pack, reopen the owning WBS after the whole pool is
integrated and closed. If a halted pool needs a separate recovery
task after every writer stops, that repair task does not complete the pack;
return to the exact pool and finish it before opening the integration child.

1. Resolve any conflict carried from step d.2 under this task: rerun the
   rebase in that lead's worktree
   (`git -C <W> rebase --no-autostash "$(git rev-parse HEAD)"`), resolve each
   stopped commit in the worktree's files, `git -C <W> add <paths>`, and
   `GIT_EDITOR=true git -C <W> rebase --continue` (without an editor,
   `--continue` fails); repeat for each commit the rebase stops at. Then
   rerun the step d.1 command with `--resume`: the branch tip is no longer
   the `commit` the lead returned, which `--resume` accepts, and the script
   fast-forwards, harvests, and removes that lead's worktree as in step d.
   Successful explicit `finish --resume` clears the halt only when no other
   unresolved integration remains; otherwise follow `status` recovery actions.
   Then continue step d in order for every remaining closed lead of the pool, resolving any further conflicts under this same task, so every
   closed lead not kept in step d is on the main branch before the full
   suite runs.
2. Run the full suite (e.g. `uv run pytest tests/ -q`).
3. Run fresh `review-code`, conditional `review-security`, then `qa-cli` in
   receipt-backed review-before-QA order. Scope `review-code` by § Batch integration
   review scope in `plugin/skills/develop/quality-audit-pipeline.md`: give the
   reviewer each lead's `old_base`, `old_tip`, `new_base`, and `new_tip`, its
   patch-id result, and carried or residual status.
4. In this plugin source repo, before `task_close`, run
   `python3 plugin/scripts/install_verified.py --task-dir <integration task dir>`
   — this is the only place `install_verified.py` runs in batch mode; leads
   skip it.
5. Close the integration task. Only then resume dispatch of any remaining
   queued requests, so no main task overlaps live leads; perform final
   integration review/QA again after those requests finish. When every request
   has a disposition, run
   `PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py --repo <main checkout> --batch-id <id> close`.
   Operational close refuses unfinished work/cleanup and reports abandoned
   retention separately; it never substitutes for task review/QA/close.

## f) Host visibility

Tell the user their host git client (e.g. GitKraken over a drvfs mount) can
inspect this work through **branches and commits only**. Never instruct the
user to open a worktree folder directly on the host. Never run
`git worktree prune` or a host client's worktree cleanup while any lead
worktree exists: from the host every container worktree path looks missing,
and prune deletes the metadata of every **unlocked** one (the Claude lock is
what protects a running lead and, while this session runs, a returned one;
kept blocked/failed worktrees lose it once that lock is released, and the
unlock → remove gap never has it).

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
