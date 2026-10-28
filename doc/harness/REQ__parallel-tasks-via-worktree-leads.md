---
tags: [harness, lifecycle, worktree, batch, mcp, contracts]
summary: 한 세션이 MCP가 생성·기록한 worktree에서 리드를 병렬 실행한다. 두 런타임 모두 실제 worker 신원을 바인딩하고 MCP가 통합·증거 보존·정리까지 소유한다. 상위 WBS는 정리와 통합 검증 후 완료한다.
updated: 2026-10-20
freshness: current
invalidated_by_paths:
  - plugin/mcp/harness_server.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/scripts/batch_harvest.py
  - plugin/scripts/batch_finish.py
  - plugin/scripts/batch_state.py
  - plugin/scripts/batch_submodules.py
  - plugin/scripts/setup_finalize.py
  - plugin/scripts/batch_preflight.py
  - plugin/agents/task-lead.md
  - plugin/skills/batch/SKILL.md
  - CONTRACTS.md
freshness_updated: 2026-10-19T06:06:13Z
---

# REQ — parallel tasks in one session via worktree leads

## Standing user requirements (2026-09-27)

- Parallelism happens inside **one** Claude Code session. Multiple terminals or
  sessions, `claude -p` background sessions, and agent teams are not the
  answer for this user, even though other tools run many agents that way.
- The repository runs in a container at a different path than the Windows
  host, where it is inspected with GitKraken. The user explicitly chose
  **not** to use relative-path worktrees and accepted "work in a worktree,
  then merge", with the host looking at branches and commits only.
- (2026-09-28) Lead work lands on the main branch by **rebase and
  fast-forward**, not by merge commits: "batch작업 worktree로 분할하고
  합칠때 그냥 rebase fast-forward로 해줘". The main branch history stays
  linear.
- (2026-09-28) Lead agents keep their prompt cache for one hour through the
  per-agent `experimental: { cacheTtl: 1h }` in `plugin/agents/task-lead.md`
  (wave 1 measured 89% of lead cache writes as 5-minute expiries; see
  `doc/harness/GUIDE__how-harness-works.md` §10.6). A
  session-wide `subagentPromptCacheTtl` was rejected; add none anywhere.
  Tests pin the frontmatter text only: Claude Code ignores an unknown
  frontmatter key, so nothing here proves the TTL takes effect at runtime.

## Worktree completion intent and derived mechanism (2026-10-20)

User source: current conversation, “야 지금 작업하면서 자꾸 worktree제대로 정리 안 하는 현상 있는데? 이것도 고쳐야겠는데?” The requested outcome is completion and cleanup of task-owned temporary worktrees while preserving their intended changes.

Derived implementation decision: authenticate process-owned Codex native identity before eager workspace binding and reuse the existing binding/registration owners. This is an engineering mechanism for the requested completion outcome, not a claim that the user requested a particular authentication design. Retention of unresolved work remains required; successful integration and evidence harvest precede disposal.

## Managed lifecycle approval and derived API (2026-10-20)

User-explicit source: “worktree정리 안되는문제를 구조적으로 해결할 방법 없냐? 너의 기억에 의존하는거 말고” and “애초에 워크트리 만들고 시작하는거자체를 mcp로 넣어놔야하나?”.
User confirmation: “ㅇㅇ 그렇게 가자” approves the preceding proposal that MCP
own creation through integration/disposal and restart recovery. Owning WBS
completion must include disposal; conflicting or unique work must be preserved.

Derived mechanics: reuse `wbs(action="dispatch")` and existing batch records,
with `operation=prepare|status|bind|complete|resume|recover`, rather than add a
second ledger or service. MCP creates the exact checkout before worker spawn;
Claude lead isolation must be absent so it cannot create a duplicate. Bind the
real host identity before mutation. Actual stopped-worker completion validates
closure and runs existing serial integration, archive and removal; retries use
original checkpoints. A derived optional `recover recovery_commit=<exact SHA>`
acknowledges an independently reviewed conflict-resolved tip only at its original
pre-integration checkpoint, with stopped writers, unchanged W/branch/task/run
and valid close proof, plus destination ancestry. It does not rewrite the lead
result; normal `complete` accepts only that original result.
Hooks/context discover pending work, never authorize or
perform cleanup. The [batch recovery contract](REQ__batch-state-pool-recovery.md)
owns exact inputs and failure semantics. Unrelated repair tasks are not blocked.

## Requirement

- One Claude Code session can run several harness tasks at once. The main
  session is the coordinator. It spawns one `harness:task-lead` subagent per
  task, and each lead receives its exact MCP-created linked checkout under
  `<repo>/.claude/worktrees/<name>`. Claude does not request native isolation;
  both runtimes verify and reuse the managed handoff (see "Worktree location").
- Codex uses the same pool with MCP-created registered worktrees and
  native lead agents. Its task tools pass canonical workspace while native cwd
  may remain main; the shared binding owner binds the authenticated coordinator to the
  worktree. See `plugin-codex/internal-skills/batch/SKILL.md` and the
  [receipt-free execution contract](REQ__receipt-free-execution-and-completion.md).
- Each linked worktree is a separate checkout with its own write focus (C-09).
  Task state under `doc/harness/tasks/` is gitignored, so every worktree has
  its own task namespace, focus markers, and measured WBS checks.
- A lead runs the normal lifecycle (`wbs(action="open")` → plan → develop → review/QA
  → `wbs(action="close")`) inside its worktree and commits on its branch, in one commit
  carrying a `Harness-Task: <task_id>` trailer (`git commit --trailer`), so
  the task stays traceable after the rebase gives that commit a new id. It
  never asks the user; undelegated material decisions go back to the
  coordinator.
- The coordinator integrates lead branches into the main checkout one at a
  time. For each closed lead it rebases the lead branch onto the main
  checkout's current HEAD inside the lead's worktree
  (`git -C <W> rebase --no-autostash <main HEAD>`), fast-forwards the main
  checkout (`git merge --ff-only <branch>`), harvests the lead's task
  evidence, then removes the worktree and branch;
  MCP `dispatch operation="complete"` runs that whole step for one lead via
  `batch_state.py` and `batch_finish.py`, then closes an eligible disposed pool. No
  merge commit is created. After every lead integrates and cleanup completes,
  reopen the same owning WBS in main for the full suite, review, QA and — in
  the harness source repo — verified install. A standalone batch uses its
  ordinary integration task. Leads never run `install_verified.py`.
- The coordinator persists requests, reservations, worker/worktree identity and
  integration checkpoints through `batch_state.py`. It refills available slots
  without waiting for a whole wave; dependent requests wait for confirmed
  integration and cleanup. MCP creates the validated retention marker; bootstrap-only leads verify it
  and return identity before `bind` permits their normal lifecycle. The
  marker preserves operational ownership and earlier retained native worktrees
  until durable harvest; it is never staged or treated as task authority. Exact restart, retained-scope reservations,
  cap selection and abandonment semantics are owned by
  [Batch state, bounded scheduling and recovery](REQ__batch-state-pool-recovery.md).
- The coordinator procedure (intake, preflight, spawn, rebase/conflict path,
  harvest, integration) is owned by `plugin/skills/batch/SKILL.md`; the lead's
  rules and its JSON return shape by `plugin/agents/task-lead.md`.
- While any lead runs, no harness task may be open in the main checkout for the
  same session. A late lens stop from a lead whose worktree was already
  removed resolves its `cwd` up to the main checkout, and it must find no open
  task there to bind to.

## Integration by rebase and fast-forward

- The rebase runs inside the lead's worktree because the lead branch is
  checked out there; git refuses to rebase it from the main checkout.
- `batch_harvest.py` accepts a lead only when the worktree HEAD is on a
  branch and is an ancestor of the main HEAD. After the rebase and
  fast-forward the two are equal. After a completed rebase without the
  fast-forward, or after a cherry-pick into main, the worktree HEAD is off
  the main history and harvest refuses. A rebase that stopped midway
  (conflict, signing failure) leaves the worktree HEAD detached; stopped at
  its first commit, that HEAD is the main HEAD itself, which passes the
  ancestor check, so harvest also refuses any detached HEAD.
  The lead's evidence is never archived for commits that did not land.
- A lead branch must hold no merge commit. A lead may not merge, but nothing
  else enforces it, and a rebase linearizes a merge and silently drops any
  change made in the merge commit itself; every later guard then passes. The
  coordinator checks `git rev-list --merges <main HEAD>..<branch>` first and
  keeps such a lead unintegrated. `batch_finish.py` (below) performs this
  check and the rebase, abort, and classification steps of the next bullets.
- The rebase targets the main HEAD sha, not a branch name, because the main
  checkout's branch name differs between projects. `--no-autostash` makes a
  dirty lead worktree stop the rebase instead of being stashed and replayed.
- A failed rebase is aborted at once (`git -C <W> rebase --abort` restores
  the lead's exact tip and a clean tree, and only prints "No rebase in
  progress?" when the rebase never started). A conflict and a dirty-worktree
  refusal both exit 1, so the coordinator tells them apart by the conflicted
  paths (`git -C <W> diff --name-only --diff-filter=U`) listed before the
  abort. A conflict stops the wave and is carried into the integration task,
  which reruns the rebase, resolves in the worktree, and continues with
  `GIT_EDITOR=true git -C <W> rebase --continue` once per stopped commit;
  without an editor, `--continue` fails. Any other failure (dirty worktree,
  an untracked file the rebase would overwrite, a hook or signing failure)
  keeps only that lead, because leads are independent, and the wave
  continues. An `--ff-only` refusal means the main checkout moved; the
  coordinator stops and reports it.
- Known limit: a rebase treats ignored files as expendable. If the main
  history tracks a file at a path that is ignored task evidence in a lead
  worktree (only possible by force-adding it), the rebase overwrites that
  evidence silently before harvest copies it.
- Every lead after the first gets new commit ids. Those rebased commits are
  not the exact tree that lead's reviewers and QA saw; the integration task's
  full suite, review, and QA run on the combined result, as they did for merge
  commits before. The lead's returned `commit` is its pre-rebase tip, so the
  coordinator records the main HEAD after each fast-forward, and the report
  lists both.
- `plugin/scripts/batch_finish.py --repo --worktree --branch --task-id
  --commit` owns SKILL step d for one closed lead. Before it changes
  anything it checks that the worktree is a registered linked worktree of
  the repository with no rebase in progress and the branch checked out, that
  the returned commit is the branch tip, that
  `git rev-list --merges <main HEAD>..<branch>` is empty (the check must run
  before the rebase, after which the merge is gone and every later guard
  passes), that the lead worktree is clean, and that the main checkout is
  clean and on a branch (on a detached HEAD the fast-forward would move only
  that HEAD, and `git branch -d` would then delete the one branch holding
  the lead's work). Then it rebases with `rebase.updateRefs=false` (no other
  branch moves), fast-forwards `refs/heads/<branch>` (a tag with the
  branch's name would win a bare-name lookup), harvests through
  `batch_harvest.py`, runs `git worktree unlock` only when
  `git worktree list --porcelain` shows the worktree (compared by realpath)
  locked, and ordinarily runs `git worktree remove` and `git branch -d`,
  never with `--force`. State-managed selected S1 modules have the sole guarded
  removal exception owned by `batch_harvest.py`, defined in
  [Selected submodules](REQ__batch-submodule-support.md); branch deletion stays `-d`. It aborts a failed or interrupted rebase only when
  `rebase-merge` or `rebase-apply` exists (`rev-parse --git-path`), because
  the dirty-worktree and untracked-file refusals never start one and
  `rebase --abort` then exits 128; it never aborts a rebase it did not
  start. Git calls use the trusted environment (no ambient `GIT_*`) plus the
  four `GIT_AUTHOR_*`/`GIT_COMMITTER_*` identity variables, because the
  rebase writes commits and a container or CI identity may exist only
  there. It prints one JSON object whose twelve keys are present on every
  status, a failed check and `error` included: `status`, `reason`,
  `task_id`, `branch`, `worktree` (realpath), `returned_commit` (the full
  sha once it resolves), `branch_tip`, `integrated_tip`,
  `conflicted_paths`, `trailer_present`, `harvest` (the harvest summary or
  null), and `cleanup` with the booleans `unlocked`, `relocked`, `removed`,
  and `branch_deleted` (`removed: true` with `branch_deleted: false` means
  only the branch was kept). It exits with the status: `integrated` 0,
  `kept` 3 (this lead stays, the wave goes on), `conflict` 4 (stop,
  resolve in the integration task), `ff-refused` 5 (the main checkout is
  dirty, detached, changed branch identity, or refused the fast-forward: stop), `error` 1 (an
  unexpected failure: stop). Whatever the status, a non-null
  `integrated_tip` means the commits are on the original main branch. The
  precheck captures its symbolic ref; checkout identity changes after rebase
  refuse integration and preserve the lead. Before harvest or cleanup, success
  must be verified against that captured branch, not merely whichever HEAD is
  currently checked out. A failed or interrupted merge attempt reconciles
  the lead tip against the same captured branch's history
  without continuing harvest or cleanup. If that check cannot complete,
  `reason` explicitly reports integration as unknown; a null tip alone is
  not evidence that integration did not happen. Usage errors
  exit 2 without JSON and change nothing: `--commit` not 7-64 hex
  characters, `--branch` starting with `-` or holding whitespace or any of
  ``~^:?*[\``, `--task-id` not matching `TASK__[A-Za-z0-9._-]+`, a
  relative `--worktree`, or a missing required flag (`--repo` defaults to
  the checkout found from the current directory). When the bad value came
  from a lead's result (for example `"commit": null`), only that lead is
  affected.
- `--resume` serves integration-task step e.1: after the coordinator reran
  the rebase, resolved each stopped commit, and ran
  `GIT_EDITOR=true git -C <W> rebase --continue`, the branch tip no longer
  equals the returned commit. Resume only drops that tip check (the returned
  commit must still exist); the rebase is then a no-op, or replays onto a
  main HEAD that moved meanwhile with the normal failure handling. The same
  flag finishes a lead after `ff-refused` once its branch was rebased, or
  after a `kept` whose cause was fixed while its worktree still exists.
  Resume does not relate the tip to
  the returned commit, and a rebase drops a commit that becomes empty, so
  the coordinator checks the resolved branch before resuming.
- Cleanliness checks explicitly include untracked files, irrespective of
  `status.showUntrackedFiles`. Native worktree removal also overrides that
  setting, so an untracked file introduced after the check still refuses
  removal. Ignored task evidence is harvested before removal as above.
- When `git worktree remove` refuses or raises after the script released the
  agent lock, it attempts to restore the original lock reason on a retained
  registered worktree. The original failure and any failed restoration are
  reported; restoration is best effort, not a guarantee against pruning.
- `trailer_present` is true when every commit between the main HEAD and the
  lead tip carries `Harness-Task: <task_id>`, false when one does not, and
  null when that range is empty or was not read. It is reported, not
  enforced: a lead without the trailer still integrates.
- Known limit: the repositories' own hooks run as for any rebase and
  fast-forward (`pre-rebase`, `post-rewrite`, and `post-checkout` in the
  lead worktree, `post-merge` in the main checkout); a hook failure during
  the rebase is a `kept` rebase failure.
- Known limit: the script cannot tell whether a lead is still running. Run
  for a running lead, it would release Claude Code's lock and remove the
  worktree under it; the coordinator runs it only for leads that returned.
- Known limit: harvest checks its inputs (links, non-regular files, an
  archive collision) only after the fast-forward, as the prose step did. A
  refusal there, or a removal refusal, returns `kept` with `integrated_tip`
  set: the commits are on the main branch and only the worktree is kept,
  which a `--resume` rerun finishes once the cause is fixed. A
  `git branch -d` refusal comes after the worktree is removed, so a rerun
  fails its registration check; the reason says to run `git branch -d` by
  hand once the cause is fixed.
- Known limit: `ff-refused` also covers a main checkout that is not clean
  or not on a branch before anything changed, because the coordinator's
  action is the same: stop integrating and report.
- Checkout identity changes during integration also return `ff-refused`.
  If the original branch already received the lead before a hook changed
  checkout, `integrated_tip` still confirms that fact and cleanup stays stopped.
- In batch surfaces, "merge" and "post-merge" name this fast-forward
  (`git merge --ff-only`); no batch step creates a merge commit.

## MCP `workspace` argument

`wbs` open/context/plan/check/dispatch/verify/close/block actions accept an
optional `workspace` string. It must name a registered
linked worktree of the repository the MCP server controls:

- absolute and already canonical (`realpath(workspace) == workspace`);
- `<workspace>/.git` is a regular (non-symlink) gitfile whose `gitdir:` target
  sits directly under `<control>/.git/worktrees/`;
- that target's `gitdir` back-pointer names `<workspace>/.git`;
- the workspace carries its own tracked harness manifest, so
  `harness_root_resolution(workspace)` returns the workspace itself.

Anything else is refused with `WORKSPACE_NOT_REGISTERED_WORKTREE` and nothing
is written; a non-string value is refused with `reason: wrong_type`. A
`workspace` equal to the control root resolves to that root. Codex echoes an
explicit workspace. An ordinary shared MCP host without authenticated native
identity defers binding to genuine PostToolUse. A process-owned exact thread
may bind an explicit workspace eagerly only after trusted native metadata, its actual native cwd,
and every parent spawn edge authenticate that actor. An environment ID alone
is insufficient. Omitting workspace selects the control root. Both direct
and hook binding use the existing cross-root session locks and conflict fences;
neither may acquire simultaneous authority for different open tasks. The
native session cwd may remain main while the canonical task root is a linked
worktree. Invalid workspace proof publishes no new exact binding or registration.
Existing exact main-call behavior retains its shared session-lock protection.

Task paths, focus markers, scaffolds, and `request_file` resolve under the
workspace. Session identity (the Claude session hint) and
gate-warn learnings stay on the control root, because only the main checkout
receives `UserPromptSubmit` and every Claude lead shares the main session id.
Codex leads instead have distinct authenticated native coordinator threads;
their exact task binding markers live under their own task workspaces.
For a process-owned exact Codex actor, close and block snapshot, remove and
validate cleanup of that same actor’s marker, even when an inherited session
ID names its parent. Failure restores that actor’s marker; it must not alter
the parent’s marker. Existing conflict fences remain intact.

Claude hooks need no argument: they already resolve the repo from the payload `cwd`,
and a nested reviewer/QA subagent spawned by a lead runs with the worktree as
its cwd, so its source ownership stays in the worktree task.
Codex instead uses the exact echoed workspace/task/run and native hook identity;
the authenticated lead's direct reviewer/QA children inherit that binding.
Native cwd alone does not select a Codex worktree task.

## Host visibility

The repository may be mounted into a container at a different path than the
host sees (e.g. `/project/...` over a Windows drvfs mount). Worktree metadata
stores absolute container paths, so:

- inspect lead work on the host through **branches and commits only**;
- never open a worktree folder with a host git client;
- never run `git worktree prune` (or a host client's worktree cleanup) while
  any lead worktree exists. From the host every container path looks missing,
  and prune deletes the metadata of every **unlocked** worktree. Claude Code's
  agent lock protects a running lead and, while the coordinator session runs,
  a returned one; kept blocked/failed worktrees lose it once that lock is
  released (for example after the coordinator session ends), and the moment
  between `git worktree unlock` and `git worktree remove` never has it.

Relative-path worktrees (git ≥ 2.48 `extensions.relativeWorktrees`) are out of
scope by the user's decision; host clients that lack the extension also cannot
open the repository at all.

## Worktree location

- Managed dispatch chooses and records the lead checkout under
  `<repo>/.claude/worktrees/<name>` before spawning either runtime. A lead
  does not use native isolation or the Codex app's automatic worktree location.
  Existing registered retained checkouts remain recoverable at their recorded
  paths; preparation must not create substitutes.
- Harness is location-agnostic: it validates registration, not path. The MCP
  `workspace` check (`resolve_registered_worktree`) and `batch_harvest.py`
  accept a linked worktree because its gitdir is registered under
  `<control>/.git/worktrees/` with a matching back-pointer, wherever the
  checkout sits; `batch_preflight.py` recognizes lead worktrees by the paths
  `git worktree list --porcelain` registers, not by where they live. The only
  location-specific check is `batch_preflight.py`'s `worktrees_ignore` (batch
  SKILL managed preparation), which checks that the managed
  `.claude/worktrees/` is gitignored where git sees it.
- Placing worktrees under `.git` (for example `.git/harness-worktrees/<name>`)
  was evaluated and rejected:
  - Claude Code treats any absolute path with a `.git` segment as protected,
    so every lead Edit/Write prompts (default, acceptEdits), goes to the
    classifier (auto), or is denied (dontAsk); only bypassPermissions lets it
    through, and neither a settings allow rule nor a PreToolUse `allow`
    bypasses it.
  - Claude only relocates a worktree through a WorktreeCreate hook. A
    hook-made worktree loses the agent lock, the creation marker that keeps
    the periodic sweep away from it, `.worktreeinclude`, and `baseRef`; the
    hook replaces worktree creation for the whole settings scope, and it
    cannot keep C-12's `|| true` fail-safe because a failing hook aborts
    creation.
  - The Codex workspace-write sandbox makes `<writable root>/.git` read-only.
  - `.git` and `.claude` sit on the same 9p mount, so there is no speed gain.
  - None of the surveyed prior-art tools (eleven) puts worktrees under `.git`;
    they use a dedicated in-repo directory, a sibling, or a tool-owned
    directory outside the repository.
- The cost of the kept location: `.claude/worktrees/` must be ignored, and
  `git clean -ffdx` in the main checkout deletes it, lead work included,
  despite the ignore. Never run it while any lead worktree exists.

## Multi-repo and submodules

Batch uses a single git control root. Its own tracked area and explicitly
selected populated direct submodules can participate in leads. Without an S1
selection, requests inside submodules or nested repos remain ordinary sequential
tasks outside the wave. The complete opt-in contract is owned by
[Selected submodules](REQ__batch-submodule-support.md). Three related shapes exist:

- (a) **Submodules**: mode-160000 gitlinks, normally declared in
  `.gitmodules`.
- (b) **Ignored nested repos inside a git root**: directories holding their
  own `.git` that the superproject does not track (for example sibling
  service repos under an ignored `repos/`).
- (c) **A non-git parent with child repos**: no control root at all.

Repository hazards without controlled preparation:

- `git worktree add` never initializes submodules (it has no recursive option
  and `submodule.recurse` does not change that), and neither Claude Code nor
  Codex does, so every submodule is an empty directory in a lead worktree —
  unless the repository's post-checkout hook initializes them, which Claude
  Code does not suppress.
- An ignored nested repo does not exist in a lead worktree at all. Its only
  copy is in the main checkout, which no lead may write, and a plain
  `git status --porcelain` there does not report its changes.
- (c) cannot host a batch: managed worktree creation requires a Git control
  root, and the workspace validator refuses a non-Git parent. A control root
  that is itself a linked worktree, an absorbed submodule checkout (whose
  `.git` is a gitfile), or a separate-git-dir checkout is refused by the
  validator too, because it requires `<control>/.git/worktrees`; a submodule
  checkout with an embedded `.git` directory passes the validator, so only the
  preflight's superproject check refuses it.

Default rules and selected S1 extension:

- `plugin/scripts/batch_preflight.py` runs at intake and preflight for exactly
  the wave about to be spawned, and the coordinator spawns only on exit 0
  (`verdict: ok`). It refuses a control root whose shape is
  `submodule-checkout`, `linked-worktree`, `non-git`, or `separate-git-dir`;
  refuses when `git status --porcelain` is not empty in the main checkout
  (submodule changes included, whatever `submodule.<name>.ignore` says), in
  any populated submodule, or in any ignored nested repo; and refuses when a
  post-checkout hook (the default hooks dir or `core.hooksPath`) mentions
  `submodule` in a repository with submodules. It also refuses when
  `.claude/worktrees/` is not gitignored at the path git sees (see
  "Preconditions and limits"). It fails closed: a directory
  it cannot read while looking for nested repos or placing a declared scope,
  a tracked or untracked path git reports it could not open (git itself only
  warns and exits 0), a submodule directory it cannot inspect, and an
  unreadable or non-regular `.gitmodules` all refuse instead of being
  skipped, because each could hide a nested repo, a submodule, or
  uncommitted work. It never writes: every git call uses the trusted
  environment, `--no-optional-locks`, and `core.fsmonitor=false`.
- Verdict and exit status: the script prints one JSON report whose `verdict`
  is `ok` (exit 0), `adjust` (exit 1: a request is listed in
  `excluded_requests` or two requests are listed in `overlaps`), or `refuse`
  (exit 1: anything in `refusals`), with precedence refuse > adjust > ok.
  Every failure the report depends on — a failed git command, an unreadable
  directory, hook, or `.gitmodules`, a nested repo or submodule whose `.git`
  resolves to another work tree, or an unexpected crash — yields a `refuse`
  report, never a pass. A malformed `--request` (no `SLUG=PATH`, a slug
  outside `[A-Za-z0-9._-]+`, an empty path, or a glob pattern or comma list,
  meaning a value with `*?[` or a comma that names no existing path; an
  existing path such as `app/[locale]` is fine, and each path gets its own
  `--request`) is a usage error: exit 2 and no report. `ok` covers only these
  checks; the coordinator's other preflight conditions (no open
  main-checkout task, the recorded HEAD) still apply. The
  coordinator acts on `verdict` and on `refusals`, `excluded_requests`,
  `overlaps`, and `off_limits`, and reruns the script after adjusting a wave;
  `control_root.shape` is informational (it reads `non-git` with an empty
  reason when shape detection itself could not run). Every report key
  (`verdict`, `control_root`, `submodules`, `nested_repos`, `off_limits`,
  `post_checkout_hooks`, `worktrees_ignore`, `dirty`, `scopes`, `overlaps`,
  `excluded_requests`, `refusals`) is always present, even on an early
  refusal or a crash. S1 reports additionally expose `selected_modules`,
  `off_limits_by_request` and `ownership_scopes`; default reports keep their
  existing shape. `dirty` lists at most 20 status entries per repo alongside the full `count`.
  Hook detection reads the first 256 KiB of each post-checkout hook, matches
  `submodule` case-insensitively, and ignores the executable bit.
- No refusal advises deleting anything, with one exception: when git cannot
  open a nested repo or populated submodule because its `.git` is a gitfile
  naming a missing `.../worktrees/<name>` registration (a leftover worktree
  whose metadata was pruned), the refusal says to copy out anything still
  needed and then remove the directory. Every other failure, including an
  unreadable directory inside a nested repo that holds uncommitted work,
  gives no delete advice: it names the path and, where one applies, a
  non-destructive remedy (for example commit or stash, make it readable or
  move it out of the checkout, run from the main checkout, make the hook skip
  linked worktrees or run the requests one at a time as ordinary tasks). The
  first read failure stops further inspection, so `refusals` may name only
  that one and later report sections stay empty; fix it and rerun. When the
  control-root shape is not `ok`, `scopes` and `excluded_requests` are empty.
- Registered linked worktrees of the repository under the root (kept lead
  worktrees) are not nested repos: they are left out of `nested_repos` and
  `off_limits` and not status-checked, so a kept lead's uncommitted work does
  not refuse a new wave; a scope inside one is still `inside-ignored-nested-repo`.
- Each declared scope is classified on its symlink-resolved path:
  `outside-root` when it lies outside the root or under its `.git/`;
  `inside-submodule` when it equals or lies under a submodule path (populated
  or not); `inside-ignored-nested-repo` when an existing directory between the
  root (exclusive) and the scope (inclusive) holds its own `.git` entry, which
  also covers a scope inside a registered lead worktree under the root; and
  `tracked-area` for every other path inside the root. `tracked-area` is
  therefore broader than its name: it includes untracked, ignored (for
  example `build/`), and not-yet-existing paths, and a scope that contains a
  submodule or nested repo (`libs` around `libs/sub`); `off_limits` is what
  keeps a lead out of the repos inside such a scope. A separate class for
  ignored non-repo scopes is deferred: a lead's edits there are never
  committed, which is a task-planning problem rather than a repo-shape hazard.
  `tracked-area` may run in a lead. Explicit per-request `--submodule SLUG=PATH`
  selection additionally admits `selected-submodule` scopes after S1 validation.
  An unselected submodule or ignored nested repo runs as an ordinary task in the main checkout,
  outside the wave; an `outside-root` scope cannot run in `harness:batch` at
  all. `excluded_requests` keeps every exclusion reason of a request.
- Two requests overlap when one of their symlink-resolved scopes equals or is
  a path-segment ancestor of one of the other's (`a/b` and `a/bc` do not
  overlap; `.` overlaps everything); a request's own paths are never paired,
  and neither is an excluded request, because it never runs in a wave.
  Selected modules reserve the whole gitlink, so sibling descendant scopes also
  overlap. Overlapping requests move one of them to a later wave.
- The default report's `off_limits` includes every submodule and every nested
  repo found by the scan. With S1 selection, `off_limits_by_request` removes only
  that request's validated selected modules; `ownership_scopes` separately
  reserves whole gitlinks without changing the declared scope list. The
  coordinator passes the appropriate exclusions into each lead prompt.
  Leads never initialize/deinitialize/sync modules or enable recursion. Only
  coordinator-prepared selected modules permit scoped edits and commits on their
  supplied named branches; commit each module before the outer gitlink commit.
  Unselected modules, nested repos and other off-limits paths stay forbidden.
- Ordinary worktrees use plain `git worktree remove`; a refusal retains the tree.
  Only state-managed S1 can use the guarded removal owner described in the
  selected-module REQ. A lead cannot grant itself force-removal permission.
- Residual risk: `git status` in a nested repo or submodule runs that repo's
  configured clean filters, as any status there does. Nested repos in the
  user's project are trusted like the main checkout; refusing filter configs
  would falsely refuse git-lfs repos.
- Preflight inventories explicitly include untracked files in the
  main checkout, submodules and nested repos, overriding
  `status.showUntrackedFiles=no`.
- Standalone preflight checks repository shape, scopes, and
  cleanliness, not HEAD state or earlier waves. A main checkout on a detached
  HEAD or in the middle of a rebase, merge, cherry-pick, revert, or bisect
  is not refused for that state (it gets `ok` when the tree is otherwise
  clean; unmerged entries still refuse as dirt), and kept blocked/failed lead
  worktrees are not compared with the new wave's scopes by that standalone script.
  The pool's `batch_state.py claim` additionally checks destination branch,
  in-progress Git operations and retained scopes across recorded batches.
  At integration, `batch_finish.py` also refuses a detached main HEAD
  (`ff-refused`) before changing anything.

Verified hazards (git 2.43, reproduced in the 2026-09-27 investigation):

- Once a submodule was initialized in a worktree, plain `git worktree remove`
  refuses that worktree forever, clean or even after `deinit`, because the
  per-worktree module store (`.git/worktrees/<wt>/modules/`) exists and the
  index holds a populated gitlink.
- `git worktree remove --force` then deletes that module store together with
  any submodule commit that was never fetched or pushed elsewhere, and it
  skips the superproject's dirty and untracked check.
- `git submodule deinit` in a lead removes the `submodule.*` entries from the
  `.git/config` shared by the main checkout and every lead; afterwards
  `git submodule update` in main exits 0 and does nothing.
- Two leads that bump the same submodule to sibling commits always conflict
  on merge.
- `git merge` does not update the submodule checkout, and a later
  `git commit -a` records the stale checkout and silently reverts the bump.
- Git's own documentation (git-worktree, BUGS): "It is NOT recommended to make
  multiple checkouts of a superproject."

S1 handles these hazards through coordinator-owned private named-branch clones,
whole-module ownership, durable local object pins, linear integration and
immediate checkout reconciliation. Its writeahead witnesses and guarded disposal
are specified once in [Selected submodules](REQ__batch-submodule-support.md).
Recursive graphs, unpopulated modules and ignored nested-repository cloning
remain outside S1 and use the ordinary sequential route.

## Guards observed (live probes, 2026-09-27)

- Receipt binding (AC-003 gate, run before the lead agent and batch skill were
  written): a real `isolation: worktree` subagent at
  `.claude/worktrees/agent-aeae57aab7846dbfb` started `TASK__worktree-probe`
  through the `workspace` handler and spawned one nested
  `harness:code-reviewer`. That worktree task's `RECEIPTS.jsonl` then held
  exactly one `started` and one `completed`/`PASS` row for
  `runtime_id claude:2774b58d-f281-4368-8f64-34972245bcc3:adfefaec07fe5afcf`
  under `task_run_id 01a0e225-300e-7298-940b-c47ffdf31b0d`, and
  `task_context` reported `review_verdict: PASS`. The probe worktree was
  removed afterwards without harvest, so the rows themselves are gone; the
  historical receipt regression was tests/test_worktree_workspace.py::test_nested_lens_start_and_stop_in_worktree_bind_to_worktree_task (retired with receipt collection on 2026-10-28).

- Historical native-isolation probe: Claude Code refused `Write` from an
  `isolation: worktree` lead to a
  main-checkout path ("This agent is isolated in the worktree ..."), and the
  same refusal was observed for its nested subagent without isolation. This
  native guard is not claimed for the new non-isolating managed leads.
  Bash writes are not guarded by it, which matches C-05 (Harness does not
  intercept shell mutation).
- Historical native-isolation probe: after a lead returned, its worktree stayed locked
  (`locked claude agent <name> (pid ...)`) while the coordinator session
  runs, so the coordinator runs `git worktree unlock` before
  `git worktree remove`. Never unlock a worktree whose lead is still running.
- `git worktree remove` deletes gitignored files, which is why harvest runs
  first.

## Preconditions and limits

- The older native-isolation route used project `.claude/settings.json`
  `"worktree": {"baseRef": "head"}` so
  leads branch from the coordinator's local HEAD, not the remote default
  branch. Each lead refuses to start when its HEAD differs from the
  coordinator HEAD it was given. Managed dispatch now pins its recorded spawn
  HEAD directly; native baseRef is no longer a managed-creation precondition.
- `.claude/worktrees/` is gitignored in every harness project: manifest
  version 7 adds it to setup's managed operational ignores
  (`doc/harness/REQ__versioned-project-file-migrations.md`).
  `batch_preflight.py` still checks it (batch SKILL managed preparation) and refuses
  when it is not ignored, for example in a project not yet migrated to v7;
  the refusal tells the user which path to add, and a later
  `--migrate-harness-version` moves the line into the managed block.
- The check runs where git sees the directory, as `setup_finalize.py` does
  for the v7 entry: it reuses that script's `_git_visible_path` and
  `representative_path` and runs `git check-ignore --no-index` on
  `.claude/worktrees/__harness_probe__` with symlinked parents resolved. A
  `.claude` (or `.claude/worktrees`) symlink resolving inside the repository
  is checked at its target, so the literal `.claude/worktrees/` entry does
  not satisfy it there; one leading outside the repository (or looping)
  passes, because git never lists what lies beyond it. Plain
  `git check-ignore .claude/worktrees/x` exits 128 ("beyond a symbolic
  link") through either link, which is why the old prose step could not be
  used in those layouts. Report key `worktrees_ignore`:
  `{"path", "git_path", "status"}`, with status `ignored`, `not-ignored`,
  `outside-repo`, or `unchecked` (control root not `ok`, or check-ignore
  failed or `setup_finalize` could not be loaded, both of which refuse).
- Default concurrency is 3 leads; `batch.max_leads` or explicit `--max-leads`
  selects a bounded cap under the linked pool requirement. Each
  worktree builds its own `.venv`, and on a 9p/drvfs mount pytest `-n auto` per
  lead oversubscribes CPU and IO, so leads pass `-n 4`.
- Leads run the installed harness plugin, not the `plugin/` tree in their own
  worktree.
- Both runtimes use MCP-created registered checkouts. Claude leads receive
  explicit cwd/workspace; Codex additionally uses authenticated native
  coordinator ancestry with explicit workspace binding.
  The Codex internal batch workflow requires actual spawn/continuation
  capabilities and reserves room for independent review/QA. Leads operate
  only their worktree WBS; close never updates a second Goal ledger.
  A task-owned work pack releases its parent only after all requests integrate
  and the exact pool closes. Codex MCP returns canonical workspace and either
  defers to genuine PostToolUse or binds its process-owned actor after native
  metadata/cwd/full-ancestry authentication under the shared binding owner.
  Missing or unverified identity never grants source authority or successful close.
- Cross-checkout protection: when a lead's Edit/Write target lies outside its
  worktree, the prewrite gate resolves the target's own harness root and
  applies the C-05 protected-artifact rules there, so the main checkout's or a
  sibling worktree's `TASK.json`/`PLAN.md`/`RECEIPTS.jsonl`/`REVIEWS.jsonl`,
  goal state, and focus markers are denied from a lead. Validated same-repository
  worktree targets receive that checkout's source gates as well, even when native
  cwd remains main; the original symlink escape denial still takes precedence.
  Other foreign-checkout source targets remain outside the C-05 fallback,
  and Bash remains unguarded. Remaining
  limits are listed in `doc/harness/patterns/prewrite-gate.md`
  ("Cross-checkout protection").
- A failed or blocked lead's worktree is kept and reported while its work is
  unresolved, never force-removed. A later verified recovery may finish its
  disposition under the completion rules below. Evidence harvest
  (`plugin/scripts/batch_harvest.py`) copies
  `<worktree>/doc/harness/tasks/<task_id>` to
  `doc/harness/archive/batch/<task_id>/` and appends the lead's learnings
  before `git worktree remove` and `git branch -d`. It refuses to replace an
  existing archive that holds different evidence for the same task id.

## Temporary-worktree completion and interrupted recovery

Temporary worktrees explicitly owned or adopted by the task are development
resources: completion includes integrating their intended changes by rebase
and fast-forward only, then removing the worktrees and disposable branches.
No merge-commit fallback is allowed. Ordinary closed batch leads retain the
existing `batch_finish.py` sequence: rebase, fast-forward, harvest, remove;
the coordinator's integration task subsequently reviews and tests the combined
result before reporting completion. Leads never remove their own checkout.

Both runtimes' normal develop completion and run recovery completion load
`plugin/skills/run/worktree-completion.md` (projected into Codex internal
skills). That shared procedure owns the execution details. The coordinator
records each owned source path/branch, intended destination, worker and final
disposition. Unrelated worktrees, running or unknown workers, unintegrated
changes, failed archives and cleanup refusals remain intact with a concrete
remaining action; a closed implementation task does not imply cleanup passed.

Resume interrupted work in its original worktree where possible. An unsupported
runtime is a blocker to that lifecycle, not permission to silently copy changes
to main and declare completion. When a separate recovery already copied work,
the coordinator must account for every original change against a committed,
independently reviewed and QA-passed destination, preserve byte-verified source
and non-cache ignored evidence outside the removed tree, and recheck ownership,
stopped writers, branch identity and the exact inventory before narrow cleanup.
An archive alone or an ancestor branch alone does not prove dirty changes were
integrated. Unaccounted changes remain protected. Original task status and
receipts are not rewritten or relabeled as closed; recovery evidence is separate.
Verified, fully accounted originals are then removed rather than kept forever.

For WBS-owned packs this is enforced by managed dispatch and the owning WBS
completion proof: archive/ancestry alone are insufficient while an owned
worktree or disposable branch still exists. Lead source close remains the
current measured WBS task gate; it does not remove its own checkout. Standalone
temporary-worktree recovery still follows the shared coordinator procedure.
Actual completion reporting names the integrated destination and removed
resources, or exact retained work and blocker.

## Verification cues

- `tests/test_worktree_completion_contract.py`: normal and recovery caller
  routing in both runtimes, safe disposal and recovery boundaries, and the
  actual built Codex payload's shared completion procedure.

- `tests/test_worktree_workspace.py`: accepted registered worktree; refusals for
  plain directory, other repository, relative/symlinked path, missing manifest,
  forged back-pointer and non-string value; ordinary Codex routing defers exact
  binding; control root
  treated as omitted; marker file named after the control-root session hint;
  `task_blocked` and full-lifecycle `task_close` leave the main checkout
  byte-identical; a lead close never touches coordinator state; source ownership from
  `cwd=<worktree>` remains scoped to that worktree task.
- Native binding tests cover authenticated workspace open/context, invalid
  proof without fresh authority, main cwd distinct from target workspace and
  cross-root conflicts/fences without lifecycle collection.
- `tests/test_mcp_tool_name_contracts.py`: the sole WBS tool declares optional
  `workspace`; retired tool aliases are rejected.
- `tests/test_batch_finish.py` (scratch repos, lead worktrees under
  `<main>/.claude/worktrees/`): two leads integrated in order onto a linear
  history (the second rebased, the first unchanged), harvested, unlocked,
  removed, and their branches deleted; the trailer reported, not enforced,
  false unless every lead commit carries it, and null for a lead with no
  commit; a merge-commit lead, a dirty lead (tracked change or untracked
  file), a returned commit that is not the tip or does not exist, an
  unregistered worktree, and a wrong branch are kept with nothing changed;
  a dirty main checkout and a detached main HEAD are `ff-refused` before any
  change; a conflict (merge and apply backends) is aborted back to the exact
  tip with the lock kept, a failed abort is reported without claiming the
  rebase was aborted, and a failure while diagnosing a failed rebase still
  aborts it; a pre-rebase hook
  failure is `kept` without conflicted paths; a rebase already in progress
  is never aborted; a main HEAD that moves before the fast-forward is
  `ff-refused`, and `--resume` then lands the rebased branch; a harvest
  refusal (archive collision, or a non-UTF-8 learnings file) after the
  fast-forward is `kept` with `integrated_tip` and the lock untouched, and a
  `--resume` rerun finishes it once fixed; a crash after the fast-forward
  is `error` that still carries `integrated_tip`; unlock runs only for a
  locked worktree; a removal refusal after unlock relocks with the original
  reason, and a returned relock refusal says the worktree is unlocked;
  exceptional relock failures preserve both causes and report inability to
  confirm restoration. Hidden untracked files in the lead, main checkout,
  or introduced after harvest survive despite `status.showUntrackedFiles=no`.
  A real post-merge hook timeout retains the integrated tip without cleanup;
  unavailable reconciliation reports unknown integration. Removal exceptions
  restore the original lock when possible; a removal completed before an
  exception records removal and retains the branch. A `branch -d`
  refusal is `kept` after removal and names the manual `git branch -d`;
  `--resume` integrates a lead resolved by
  hand while the plain call keeps it; an identity only in the environment
  reaches the rebase; CLI JSON with exit codes 0/3/4/5, usage errors exit 2
  without JSON, an unexpected failure is `error` with exit 1; an ambient
  `GIT_DIR` cannot redirect it.
- `tests/test_batch_harvest.py`: copy, append, idempotence; two locked leads
  rebased and fast-forwarded in order, harvest refused after the rebase and
  before the fast-forward, then accepted, unlock/remove/`branch -d`, and a
  history with no merge commit; a conflicting rebase aborted back to the
  lead's exact tip with main unchanged; a rebase stopped at a conflict
  refused as a detached HEAD, then resolved with
  `GIT_EDITOR=true rebase --continue`, fast-forwarded, harvested, and
  removed; refusal for an
  unregistered or unmerged worktree, links/FIFOs, and an archive collision; an
  ambient `GIT_DIR` cannot redirect the merged check; a non-canonical
  `--worktree` path is canonicalized and accepted.
- `tests/test_batch_preflight.py` (scratch repos): the five control-root
  shapes; submodules from gitlinks and `.gitmodules`, populated or not; deep,
  gitfile, and ignored nested repos, but not a registered lead worktree; the
  four scope classes including symlink escapes; dirt in the root, a nested
  repo, and a submodule hidden by `ignore=all`; broken nested `.git` metadata
  refuses, and the delete hint appears only for a gitfile naming a pruned
  worktree registration; an unreadable directory (scan and scope), an
  unreadable tracked directory, an unreadable directory inside a nested repo
  (no delete hint), an untraversable populated submodule, git's unreadable-path
  warning on the untracked listing, and an unreadable or non-regular
  `.gitmodules` refuse; every exclusion reason of a request is kept, and an
  excluded request is never paired in `overlaps`; overlaps and
  refuse-over-adjust precedence; post-checkout hooks in the default dir and
  `core.hooksPath`; byte-identical tree (index included) after a run; no
  `core.fsmonitor` launched (root, submodule, or nested repo); ambient
  `GIT_*` ignored; CLI exit codes and JSON, including usage errors for globs
  and comma lists that name nothing while existing paths with `,` or `[` are
  accepted; an unexpected crash still prints a `refuse` report; the
  `.claude/worktrees/` ignore refused when missing and accepted from
  `.git/info/exclude`, a `.claude` or `.claude/worktrees` symlink leaving the
  repository accepted where plain check-ignore exits 128, a `.claude`
  symlink inside the repository checked at its target, a check-ignore
  failure and an unloadable `setup_finalize` refused with a report, and the
  key `unchecked` when the control root is not ok.
- `tests/test_batch_skill_contract.py`: lead frontmatter retains `model: inherit`
  and per-agent cache TTL, while native worktree isolation is absent. Both
  runtime workflows use managed prepare/bind/complete/recover/resume/status,
  real worker identity, bootstrap verification before mutation, bounded slots,
  explicit checkout/workspace and preserved S1 module handoffs. Owner helpers
  retain linear rebase/fast-forward, no-force ordinary disposal, exact result
  and evidence preservation requirements. Existing C-09 and ignore contracts
  continue to apply; native baseRef no longer selects managed creation.
- Live probe: a real worktree subagent starts a task with `workspace` and its
  nested reviewer works in the registered worktree and returns actual findings.

## Durable dirty-original isolation

The user's CCC preservation request authorizes managed ordinary work with
unrelated original edits. Derived enforcement compares literal path ownership
at admission and the actual rebased changed-path subset at integration. A
clean isolated lead may develop while unrelated staged, unstaged and untracked
original state remains preserved. Overlap, rename endpoint collisions, selected
submodule dirt, unknown Git operations and native FF refusal retain resources.
No stash, reset, custom index rewriting or forced disposal is allowed.
The existing batch recovery contract owns detailed failure and ancillary
preservation rules. Durable work never relies on a /tmp-only checkout.

## Immediate assignment and per-lane completion (2026-10-21)

User-explicit source: latest message in the current conversation, quoted by the
coordinator (message identifiers unavailable): “준비된 독립 작업은 즉시 배정”;
“실행 가능한 작업이 둘 이상이고 슬롯이 있으면 병렬 배정. 순차 진행에는 파일 충돌·선행 작업·공유 자원 같은 구체적인 이유를 기록.”;
“A가 끝나면 A 리뷰를 시작하고 B 개발은 계속 진행. 전체 구현 완료를 기다리지 않음.”;
“변경되지 않은 코드의 검증은 재사용하고, 수정된 범위와 영향받는 부분만 다시 검증.”;
“공유 자원만 순차 사용”; “통합 담당자 지정”;
“작업 완료 이벤트마다 다음 작업을 즉시 배정.” The user explicitly prefers
existing WBS/dispatch over a separate large management framework and forbids
implementation file lists in progress WBS.

Derived mechanics: public `dispatch prepare` takes a caller-observed `host`
snapshot (capacity, live_agents, reserved_slots, verification_reserve,
spawn_available, continuation_available) plus execution-only ownership. These
field names and the stored `execution_admission` are engineering choices, not
user-requested API requirements. Unique live/reserved IDs include one actual
coordinator; supported occupied review/QA lenses fulfill the mandatory reserve
without double counting. Unknown roles consume capacity without claiming
verification capacity. This is not authentication or a reservation service.

Owner publication binds assignment to the task run, current WBS graph, checks,
blocks and selected execution ownership. Changing source bytes alone does not
stale assignment. Prewrite checks current direct assignment or existing exact
managed lead binding, including micro/maintenance; PLAN/REQ preparation remains
possible. Routing display reads stored state and does not authenticate runtime
or acquire integration locks. Missing managed binding is not a permission PASS.

Derived admission measurement boundary (2026-10-22): ordinary source prewrite
checks stored current and historical work-pack structure without rehashing
archived evidence. This keeps the source-assignment check within the existing
hook budget; it does not skip that check or permit unfinished packs. Final
verification and close still freshly measure archive custody and reject altered
evidence. Stored admission alone never grants completion or fresh proof.

A disjoint ready pair wins over a conflicting broad first request while the
full request graph remains intact. Serial facts name actual predecessors,
conflicting outcomes/scopes or observed capacity/capability. Actual pool
handoffs identify selected managed reservations; parent source remains gated.
Each lead owns its base commit, branch, checkout, scopes, dependencies and
acceptance independently. Finished A starts review then QA while B develops.
One coordinator integrates stopped closed leads serially. A successful partial
completion explicitly requests fresh host inventory and immediate prepare of
ready successors; the workflow consumes it without a whole-wave barrier.
External capacity changes cannot be independently detected by the MCP: the
host workflow must refresh after completion, release and capability changes.

Existing target applicability and execution result reuse preserve unaffected
proofs; existing resource/output leases serialize only conflicting VM/display/
port/build outputs. They do not block independent development or create native
actual review/QA results. Unmeasured outcomes request measurement, not invented new
implementation or a replacement pool after integration.

Coverage: `test_public_dispatch_admission_and_completion_refill` exercises real
public A/B/C lifecycle with isolated task-close fixtures, not native attestation.
`test_public_direct_assignment_is_fresh_control_not_source_fingerprint` checks
actual prewrite denial, micro/maintenance, REQ preparation, input changes and
stale control/ownership. `test_public_wide_first_preserves_full_intake_and_selects_disjoint_pair`
checks public pair selection without intake truncation. Host inventory reserve
and unknown-role checks are pinned by
`test_observed_host_capacity_reserves_real_verification_without_double_count`.
