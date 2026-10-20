# Measurable WBS progress

## Provenance

User-sourced outcomes in this document come from this task's conversation:
WBS measurement (“WBS기반으로 싹 바꿔줘”), two-hour refinement (“진행률이
2시간동안 하나도 안 바뀌면 WBS를 세부적으로 더 쪼개서”), outcome-first
scope (“주객이 전도되면 절대로 안돼”), isolated parallel development and
verification (“서로 다른 worktree를 파고, worktree에서 개발, 검증 전부 다
끝내고”), flexible implementation (“무슨 파일 무슨 파일 이렇게 건드려라
그런거까지 적으면 안된다”), and one WBS/interface (“그냥 다 wbs안에
묶어버리면 되지 않나?”, “harness mcp더 단순화 가능하지 않음?”).

Specific API actions, weight/fingerprint/storage formats, clock boundaries,
process limits and migration formats below are derived implementation
choices. Their basis is the requested measurable progress plus the existing
receipt/worktree safety contracts. They are not quoted user requirements.
See [requirement provenance](REQ__requirement-provenance.md).

One WBS tree contains any objective and its outcome breakdown. `wbs` is the
only callable Harness MCP entry point: open, context, plan, check, next,
dispatch, verify, close and block operate on that same root. Native Goals
provide the objective and continuation context, not a second planning ledger.
Internal tasks retain execution identity and receipt ownership for existing runs.
Retired Goal/task tool names are rejected. The user explicitly requested removal
of compatibility code with “호환성용 코드는 최대한 버리고싶은데... 기존꺼는
마이그레이션 하는 방향만 남겨두고.” Existing Goal records and old work-pack
bindings move only through explicit `wbs(action="migrate")` preview/apply.
Migration preserves execution/evidence identities and archives original records;
it never converts historical status labels into measured credit. Transaction
formats and recovery behavior are derived implementation choices.

### Migration on filesystems without exclusive rename

User source: the current conversation reports v9fs refusing the atomic operation
needed for WBS migration with EINVAL; the user requests “문제도 해결해”. The
observable outcome is safe explicit migration in that workspace. The mechanism
below is a derived engineering decision, not an attributed user design request.

Migration must work when exclusive rename is unsupported but same-directory
hardlinks are supported. It publishes fully written and synchronized bytes
without replacing a concurrently created destination. Permission, storage and
I/O errors remain failures; they are not permission to overwrite or skip
synchronization. If neither atomic publication primitive is supported, retain
originals and report the filesystem limitation.

A fallback interrupted between link and staging removal is recoverable using
only its exact deterministic same-parent staging name plus descriptor identity,
safe ownership/mode, bounded stable bytes and exactly two links accounting for
both aliases. The filename alone proves nothing. Existing source/archive and
manifest hash checks still apply. Legacy sources, locks and task controls keep
their strict single-link rule; arbitrary aliases, third links, symlinks and
conflicting staging bytes are refused. A pre-publication orphan stage is reused
or removed only after matching the exact intended bytes and trusted identity.

Preview can inspect a valid interrupted publication without writing. Apply
normalizes only verified publication aliases under the existing migration and
required task/batch locks, before retiring sources or returning an already
complete manifest. Archive-first ordering, the version1 manifest, exact original
bytes, conflict checks and idempotent retry remain authoritative. No additional
migration ledger or runtime compatibility interface is introduced. Verification must include actual process death after link, not only exceptions
that run cleanup, and an isolated v9fs migration; real project legacy data is
not a test fixture.


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
`wbs(action="plan")`; externally blocked work instead names its blocker and unblock
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
Fingerprint scanning is bounded to 4,096 evidence entries and 32 MiB; above that budget
progress is explicitly unavailable rather than estimated. This avoids making
progress calculation an unbounded repository traversal.

### Measurement failures and checkout boundaries

User source: the reported `WBS__native-10adcc4a4fee302320bf` incident requests
“예산 제한을 무작정 없애지는 말 것”, “계산 실패·검증 미실행·검증 실패를
구분해서 표시할 것”, and “기존 WBS ID, run ID, 항목과 검증 기록을 보존할 것”.
These are user_explicit outcomes. The mechanisms below are derived from those
outcomes and the existing requirement that stale source earns no current credit.
The supplied external checkout's precise oversize cause has not been established.

Version1 storage continues to use `nodes`; `outcomes` is a measured projection,
not a required stored field. Invalid measurement returns a null percentage and
an explicit reason. Check must not index a failed projection as measured data.
An unavailable precheck reports `execution: not_run`, `passed: null` and its
measurement error without starting the assertion or rewriting existing checks.
An executed assertion reports its actual return code and execution status;
inconclusive postmeasurement reports `passed: null`, a measurement error and
unavailable progress, preserving historical proof fields. Its selected check
records a bounded `last_attempt` containing the actual result and measurement
error; that marker withholds current credit until a conclusive rerun. Thus a
prior PASS cannot revive merely because measurement becomes available again.
No separate attempt ledger or task generation is created. A conclusive failed/start-
failed assertion or changed source earns no credit. Historical evidence never
substitutes for a current successful measurement; review/QA receipts remain
separate from WBS outcome proofs.

Evidence enumeration uses each checkout's own Git-declared tracked and
nonignored untracked paths. It must not recursively inventory arbitrary
directories, ignored build output or Git administrative data. A populated
gitlink requires its indexed identity, actual HEAD and bounded Git-declared
checkout content, including dirty tracked and nonignored untracked source;
HEAD alone cannot attest a module. Unpopulated gitlinks retain an explicit
identity/absence observation. Unsafe, unsupported or rebound directories remain
unmeasurable. Git listing output, nesting and source reads are bounded before
unbounded allocation (1 MiB aggregate Git output, eight module levels and a
30-second measurement deadline); files are hashed in bounded chunks with stable descriptor
identity checks. Resource exhaustion remains explicit rather than silently
excluding source. No manual file list, new progress ledger or schema migration
is required.

Follow-up user confirmation “수정ㄱ” applies to the immediately preceding review
finding that an ambient alternate Git index can hide changes to real-index
tracked files. As a derived implementation constraint, source enumeration uses
the checkout's own Git identity and strips ambient Git repository, index and
configuration overrides through the existing trusted environment helper. A file
tracked by the real checkout remains measured even when it matches ignore rules
and an alternate index omits it. A subsequent change invalidates its prior
assertion proof; inherited Git settings cannot keep stale progress at100%.
