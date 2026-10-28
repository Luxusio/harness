# Complete task-owned temporary worktrees

Read this at normal completion and interrupted-work recovery. A temporary
worktree is not a permanent deliverable: integrate its intended changes and
remove it and its disposable branch before reporting the overall work done.
Managed WBS packs use `wbs(action="dispatch", operation="complete")` for
integration/disposal; their owning WBS completion checks archive, ancestry and
actual absence of owned W/branch. Source-task close remains a separate measured WBS
gate. Standalone temporary checkout recovery follows the procedure below.

## Ownership and timing

- Use the task's existing PLAN/checkpoint context to identify only temporary
  worktrees created or explicitly adopted for this work: canonical path,
  repository, source branch/tip, intended destination checkout/branch/tip, and
  responsible worker. Do not adopt every entry in `git worktree list`.
- Confirm the responsible worker and all writers have stopped. Unknown
  ownership, unknown worker state, unrelated worktrees and still-running work
  must be retained. Never unlock a running worker's worktree.
- A batch lead closes its task and returns to its coordinator; it never
  integrates or removes its own worktree. The coordinator uses
  `wbs(action="dispatch", operation="complete", worker_stopped=true)` through
  the batch workflow. Its installed owner is
  `${CLAUDE_PLUGIN_ROOT}/scripts/batch_state.py` (delegating to `batch_finish.py`) for
  closed, clean leads. Preserve its order: rebase, fast-forward, harvest,
  remove, then combined review/QA in the integration task. A kept result is
  remaining work, not successful cleanup.
- For other temporary worktrees, finish the source task's required review/QA
  and close there before disposing of that checkout. Perform integration and
  cleanup from a surviving coordinator checkout; preserve original task
  evidence there and run required destination validation before disposal.
  Never delete the active lifecycle's control directory or the current working
  directory. If no surviving coordinator can complete this, retain it and
  report the concrete handoff needed.
- If implementation already closed but disposal was missed, finish the
  outstanding coordinator work. Do not repeat close or rerun lenses solely
  for cleanup. Source changes or integration conflicts still require the
  applicable fresh review/QA under an integration or recovery task.

## Normal integration and disposal

1. Account for tracked, staged and untracked work; use an explicit
   `--untracked-files=all` status check. Commit the intended source changes in
   their source worktree. Check source and destination branch identities and
   tips, clean states and absence of an in-progress Git operation immediately
   before integration. A merge commit in the source range needs resolution,
   never a lossy rebase that silently drops its changes.
2. Rebase the source branch onto the recorded destination tip with
   `git -c rebase.updateRefs=false rebase --no-autostash <destination-tip>`
   in the source worktree, then use `git merge --ff-only refs/heads/<source-branch>`
   in the destination checkout. Never create a merge
   commit or fall back to one. On conflict or refusal, preserve the work,
   resolve under the integration task and retry; never stash, force-push or
   silently replace this route with copying files onto main.
3. Confirm integration on the original destination branch, not whichever
   branch HEAD happens to name later. Record the actual integrated tip.
   Apply required integration review/QA; batch timing is defined above.
4. Preserve task evidence before removal. The batch helper harvests task
   artifacts and learnings; it does not archive arbitrary ignored files.
   Inspect ignored content and retain any unique source, evidence or data
   outside the tree before deleting it. Reproducible caches may be discarded;
   unknown content or nested repository/submodule data must be retained.
   Selected S1 module objects use the preservation proof below.
   Ordinary managed disposal checks the ignored inventory again immediately
   before removal. Unique or unknown ignored content retains W/branch/marker;
   canonical task evidence and learnings require byte-preserved copies outside W.
   Exempt only narrowly recognized reproducible caches and validated discardable
   Harness session/empty-lock metadata, never broad `*.log`, `.cache` or private
   directories. A retained Native checkpoint is unique evidence, never a cache;
   preserve and byte-verify it outside W with exact accounting before explicit
   removal. Do not automatically archive arbitrary user data to bypass refusal.
5. Recheck ownership, stopped writers, source/destination identity, committed
   ancestry and clean status. From the surviving checkout, unlock only an
   existing lock belonging to the stopped worker, use normal
   `git worktree remove <path>`, then `git branch -d <branch>`. Never use
   `--force` on this ordinary route, forced branch deletion, `git reset --hard`,
   or `git clean`. The sole S1 exception is defined below.
   On removal refusal, restore the original lock if the tree remains; report
   lock-restoration failure too. If branch deletion refuses after removal,
   report only the branch as retained, not a nonexistent worktree.

### State-managed S1 disposal exception

Only `batch_state.py finish` may delegate a single `git worktree remove --force`
to its sole owner, `batch_harvest.py`, for explicitly selected prepared S1 modules.
Require the exact durable batch/module identities and integration checkpoint,
durable byte-verified task archive, preserved module refs/tags/reflogs/history
objects with complete closure, and unchanged inventory immediately before removal.
Recheck all tracked, staged, untracked and ignored content; only exact harvested
evidence and matching learnings qualify for exemptions. Unknown data, nested
repos, unexpected modules, changed metadata/refs or missing pins retain the tree.
Restore the original lock and bootstrap marker on refusal where W still exists;
branch deletion remains `-d`. No standalone helper, lead or copied-recovery route
inherits this exception. The full S1 contract is
`doc/harness/REQ__batch-submodule-support.md`; never invoke force manually.

## Interrupted or already-copied recovery

For a managed pack inspect `dispatch operation="status"`, then use `recover` or
`resume` with actual stopped-writer confirmation. Context/next and existing
session/prompt discovery expose pending pools even without an active task. They
never clean up Git or infer stopped writers. Reuse the exact W/branch/task/run;
normal preparation and completion retries use the same durable batch record.
After independently reviewed conflict resolution, managed `recover` may receive
`recovery_commit=<exact resolved branch-tip SHA>` as explicit acknowledgment.
Only its original pre-integration checkpoint can accept the changed tip, with
exact W/branch/task/run, close proof and destination ancestry preserved. Normal
`complete` still validates the original lead result; an already integrated
checkpoint cannot adopt unexpected new commits.

Prefer resuming and finishing in the original worktree, then rebase and
fast-forward as above. If the runtime cannot operate that task, name the
runtime blocker and retain the source; unsupported workspace routing is not
permission to silently bypass the original lifecycle by copying into main.

For work already recovered into the destination, use a separate recovery task
to finish the disposition. A formerly blocked/failed lead is not kept forever
once recovery is complete, but do not fabricate a closed lead result for
`batch_finish.py`, edit original task control, or carry original PASS
evidence. No duplicate or empty commit is needed to pretend already-landed
changes went through rebase.

Before clearing any dirt or removing an original, require all of:

- Explicit adoption of these exact stopped originals, with source branch/tip
  and intended destination identity. No unintegrated unique source commits.
- Per-change disposition of tracked, staged, deleted and untracked paths:
  name the destination commit/path containing each intended change, or its
  explicit authorized omission. Fresh independent review and QA must cover
  the recovered committed result. An archive alone or an ancestor branch
  alone does not prove dirty changes were integrated.
- A readable, byte-verified archive outside every tree being removed that
  preserves the complete source inventory (including staged/worktree diffs,
  binary and untracked contents) and non-cache ignored evidence. Verify names,
  contents and symlink targets without following links into unrelated data;
  record excluded reproducible caches. Never overwrite a different archive.
  Keep sensitive recovery data local and gitignored; do not print or stage it.
- Immediately before cleanup, recheck unchanged source/destination tips,
  ownership, stopped writers, exact path inventory and archived bytes. A new
  file or changed byte invalidates the cleanup decision. Retain and reassess.

Only then restore the exact accounted, archived tracked paths to the source
HEAD and remove the exact accounted, archived untracked paths; do not clear
anything else. Recheck clean status with `--untracked-files=all`, then use the
ordinary no-force removal and branch deletion procedure above; the S1 exception
does not authorize copied-recovery disposal. Keep the archive after
cleanup. A partial cleanup failure remains resumable from the verified archive
and recorded disposition; never report an attempted removal as successful.

## Completion report

For each owned temporary worktree, report integrated destination/tip and
actual worktree/branch removal, or retained path/branch, exact blocker and next
action. Check the registered worktree list and branch existence to substantiate
removal. Do not claim overall completion while owned cleanup remains unresolved;
report implementation completion separately if its task is already closed.
Unrelated or explicitly non-temporary checkouts remain outside this procedure.
