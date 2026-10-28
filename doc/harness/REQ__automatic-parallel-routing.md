# Automatic parallel routing

Status: implemented; live native execution remains capability-dependent.

WBS dispatch routes independent pending outcomes through the existing batch
reservation, worktree, integration and recovery engine. At the actual handoff,
provide current execution ownership by pending outcome ID. This is separate
from progress planning: measurement does not prescribe implementation paths.

## WBS/MCP contract

### Ordinary linked worktrees

User-explicit bug report (2026-10-27): “linked worktree에서는 직접 배정(direct)이
있어도 모든 소스 쓰기가 막힘”; classify as managed only when a batch request
actually owns that checkout. A registered user-created linked worktree without
a matching request uses the existing direct assignment checks, including the
current task/run, admission definition and assigned source scope. An absent
batch directory or an unrelated pool must not itself prevent direct writes.

Derived preservation of existing ownership protections: a matching request,
including its reserved creation-intent worktree, retains managed task, binding,
worker and scope checks. Another task's direct assignment cannot override it.
Invalid Git registration or existing unsafe/corrupt batch metadata remains a
denial, rather than being treated as absent ownership.

User-explicit follow-up (2026-10-27): “batches가 없음” must not trigger
main-repository ancestor ownership/mode checks. When that metadata path is
absent, shared or foreign-owned main roots do not prevent the linked checkout's
existing direct admission. This includes a runtime ancestor pointing to an
empty external directory. Existing batch entries still use the batch runtime's
path validation; a final batches symlink is an existing entry, not absence.
Derived preservation: only a missing-path error permits this fallback; access
errors and other filesystem failures remain denials. This does not add a new
blanket restriction on main-root modes when batch metadata exists.

Call `wbs(action="dispatch", wbs_id=..., ownership=...)` after planning the
observable outcomes and their dependencies. Dispatch normalizes the exact
request graph, validates path ownership and unused lead identities, binds its
opaque pool ID to the owning task through immutable `owner_task_id` pool metadata, and parks the parent. Initialize WBS packs with the returned task_id as `--owner-task-id`;
standalone batches may omit this field. Explicit migration adds it to existing
pools without renaming them or changing their requests/checkpoints. Resume that exact
pool; reads never spawn, merge, or replace it. Concrete writer scopes belong
only to execution admission. The coordinator performs no source writes while
leads run.

Each lead develops, independently reviews, tests and closes in its own linked
worktree. Integrate serially through the existing batch engine and close the
pool. Only a closed pool whose every request is integrated, with unchanged
archive fingerprints, valid closed generations and commits on the expected
destination branch ancestry, releases its parent. Reopen the same WBS,
check integrated outcomes, and perform combined independent review/QA before
close. Abandoned, retained, blocked or recovery work stays unfinished. Neither
pool labels nor manually recorded completion replace proof.

Existing Goal pack data is imported only by explicit WBS migration. Pool IDs,
intake, archived evidence and execution identities are preserved. Retired Goal
tools and child queues are unavailable. See [migration](patterns/native-goals.md).

Codex workspace support must authenticate each native coordinator and its lens
children, preserve exact task/run/worktree ownership and reject stale, foreign,
unbound or sibling evidence. A workspace argument or prompt is not identity. Explicit-workspace Codex calls and main calls without an exact environment
thread identity defer binding to trusted native PostToolUse. Main calls with an
exact environment identity retain eager binding, with cross-workspace exclusion
checked under shared session locks before publication.
Conflict-history recovery also defers exact-main binding to a successful native
task result: invalidate every old registration before acknowledging fences,
then create a fresh checkpoint; parking another task alone restores no authority.
Missing native capabilities are reported explicitly; they never authorize
fabricated independent results, concurrent ownership of one task binding, or an early close.
For native shared-cwd edits, the prewrite gate resolves a validated linked
target checkout before operational-path exemptions and applies that checkout's
PLAN and protected-artifact rules. Nesting a worktree beneath `.claude/` must
not exempt its source files. Ordinary foreign repository writes retain their
existing handling; this route is limited to registered same-repository worktrees.

Within-task scheduling admits dependency-ready disjoint lanes against the actual
available agent slots. Paired implementation and test authors consume separate
slots. A completed lane permits refill while independent siblings remain live;
verification of a paired AC waits for both of its own writers. A failed lane
does not release its dependent ACs. Full review and QA still run on the completed
combined tree in the required order.

Verification: behavioral WBS/batch integration, hostile native rollout identity,
ready-lane capacity and refill tests, plus existing real-Git batch recovery,
actual review-before-QA ordering, runtime payload and contract regressions.

## Known ceiling

Known ceiling: Dispatch results are proposals; caller must reserve against fresh host inventory before spawning — upgrade when a shared host reservation API exists.

Known ceiling: Large cold ancestry scans may exceed the existing 0.5-second hook budget and fail closed — upgrade when live workloads require longer histories.

Known ceiling: Nested native ancestors must share cwd; explicit worktree targeting and separate native worktree roots are supported — upgrade when native spawning supports isolated child cwd.

Known ceiling: More than 257 conflicting task generations or 128 KiB of serialized conflict entries permanently fences that coordinator; account for the retained tasks and continue from a new coordinator.

No helper
can prove host capacity from an invented inventory. Runtime fixtures prove
protocol handling, not a live end-to-end worktree run or measured throughput.
Native ancestry validation remains bounded by depth 16 and 64 MiB rollout
inputs; worktree discovery refuses more than 256 registry entries. Long cold
ancestry reads can exceed the hook's existing registration budget and then
fail closed. Receipt watcher startup and registration are removed; native identity
and source ownership remain required.
