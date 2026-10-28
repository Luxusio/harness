<!-- harness:managed v1 — do not edit between the begin/end markers.
     Changes inside the managed block will be overwritten on harness upgrade.
     Project-specific guidance belongs in AGENTS.md, CLAUDE.md, or doc/;
     Harness does not create or import a local contract overlay. -->

# CONTRACTS

<!-- harness:managed-begin v1 -->

## 0. Design invariants

Two pressures govern this harness — in this order:

1. **Protocol compliance is non-negotiable.** Contracts listed below MUST be
   followed exactly. A "lighter" solution that violates a contract is not
   lighter — it is broken. Skipping the canonical loop, writing a protected
   artifact without its owner, closing without ordered review/QA PASS, or bypassing the
   prewrite gate are hard failures regardless of task size.
2. **Within that constraint, pick the lightest path that preserves throughput.**
   Fewer phases, shorter SKILL files, and fewer hooks are preferred when they
   don't break (1). Parallel agents are preferred for independent implementation
   and verification lanes because serialized work is the expensive path.
   Complexity requires justification; simplicity is default.

Resolving the tension:
- If a rule feels too heavy, **fix the rule** (edit this file, the SKILL, the
  gate) — never silently skip it.
- Prefer machine-enforced gates over prose. A prose-only rule is commentary.
- Every rule has exactly ONE authoritative location. Duplicates rot.

## 1. Contract matrix — 상황 → 규약

Lookup table. Find your current situation, apply the listed contracts.

| 상황 | 적용 규약 | 수준 |
|------|---------|------|
| Repo-mutating 태스크 시작 | [C-01](#c-01), [C-02](#c-02), [C-09](#c-09) | hard |
| 보호 아티팩트 쓰기 (TASK/PLAN/RECEIPTS/REVIEWS) | [C-03](#c-03), [C-05](#c-05) | hard |
| `wbs(action="close")` 시점 | [C-01](#c-01), [C-04](#c-04), [C-14](#c-14) | hard |
| 짧은 승인 (`ㅇㅇ`, `ㄱ`) 수신 | [C-07](#c-07) | soft |
| 답변 레인 → mutation 레인 전환 | [C-07](#c-07), [C-08](#c-08) | hard |
| develop Phase 4.5 병렬 에이전트 | [C-13](#c-13) | soft |
| 신규 훅 추가 | [C-12](#c-12) | hard |
| `doc/` 노트 freshness 점검 | [C-06](#c-06) | soft |
| `CLAUDE.md` 편집 필요 | [C-10](#c-10), [C-11](#c-11), [C-15](#c-15) | hard |
| Maintenance 태스크 (MAINTENANCE 마커) | C-01 완화, [C-05](#c-05) 유지 | — |
| Task in_progress 동안 turn 종결 시점 | [C-17](#c-17) | soft |
| 로컬 검증 경로가 존재할 때 검증 수행 | [C-14a](#c-14a) | soft |
| 브라우저 또는 full-suite 검증의 실행 위치 선택 | [C-18](#c-18) | soft |

Levels:
- **hard** — gate blocks or MCP refuses. Violation is impossible by default.
- **soft** — warning/log. Agent must self-correct.
- **auto** — runs in background (hook), advisory.

## 2. Contracts

Every contract below has exactly four fields: **When**, **Enforced by**,
**On violation**, **Why**. If you cannot fill all four, it is not a
contract — move it to design notes.

### C-01

**Title:** Canonical public loop — task start → plan → develop → QA → close.
**When:** Any task that mutates repo state (non-maintenance).
**Enforced by:** `plugin/scripts/prewrite_gate.py` (source write blocked
without PLAN.md), MCP `wbs(action="close")` (rejects pending `runtime_verdict`).
**On violation:** hard-block.
**Why:** Skipping stages loses evidence and provenance — unordered verdicts,
missing regression tests, orphan artifacts. Independent review remains a workflow requirement;
`wbs(action="verify")` measures WBS checks before close rather than a public lifecycle stage.

### C-02

**Title:** Plan-first — no source write before PLAN.md exists.
**When:** Any `Write` or `Edit` to a source file on an active task.
**Enforced by:** `plugin/scripts/prewrite_gate.py`.
**On violation:** hard-block with message pointing to plan skill.
**Why:** Implementation without a plan drifts scope and produces unreviewable
diffs.

### C-03

**Title:** Acceptance intent lives in PLAN.md.
**When:** Planning and verification describe task acceptance.
**Enforced by:** MCP `wbs(action="plan")` owns PLAN.md; existing WBS assertions own measured outcome checks.
**On violation:** hard-block when PLAN.md is missing for a standard task.
**Why:** A second mutable acceptance ledger duplicates plan state and creates reconciliation failure modes.

### C-04

**Title:** `wbs(action="close")` requires current WBS checks to PASS.
**When:** Task is about to be marked closed.
**Enforced by:** MCP `wbs(action="close")` checks current measured WBS outcomes, blocked state and declared target applicability. The task owner publishes `closed: true`.
**On violation:** hard-block.
**Why:** Failed, unrun or stale required assertions must not authorize completion. Machine verdicts identify WBS checks as their basis; substantive independent review and QA are workflow-owned requirements, not machine-attested claims.

### C-05

**Title:** Protected artifact ownership.
**When:** Any direct `Write`/`Edit`/`MultiEdit`/`apply_patch` to PLAN.md,
TASK.json, RECEIPTS.jsonl, task-local REVIEWS.jsonl, or
retired `doc/harness/goals/*.json` and the entire
`doc/harness/archive/goal-migration/` and
`doc/harness/archive/task-control-migration/` trees (explicit WBS migration only).
Batch control under `doc/harness/runtime/batches/` belongs to the batch runtime;
use its lifecycle commands rather than direct edits.
Native Codex session metadata remains runtime-owned source-authentication data; ordinary writes and aliases cannot modify it. Historical receipt/review output is read-only, without a collector or a new writer.
**Enforced by:** `plugin/scripts/prewrite_gate.py` `PROTECTED_ARTIFACTS`.
Harness does not intercept Bash/shell file mutation.
**On violation:** hard-block. Agent must route through the owning task MCP tool.
Actual review findings belong in independent agent final responses; preserved
historical receipt/review bytes are not rewritten.
**Why:** Wrong-writer mutation breaks task authority, lifecycle provenance, or
preserved historical artifact integrity.

### C-06

**Title:** Note freshness is an explicit developer check.
**When:** A writer relies on a note with `invalidated_by_paths`.
**Enforced by:** Workflow guidance and optional
`plugin/scripts/note_freshness.py --paths ...` invocation.
**On violation:** soft — the writer may rely on an outdated note. Normal
lifecycle calls do not discover paths from Git or update note freshness.
**Why:** Notes referencing changed source become dangerous if trusted.

### C-07

**Title:** Short approvals authorize only the last proposed transition.
**When:** User replies with a bare affirmation (`ㅇㅇ`, `ㄱ`, `yes`, `ok`).
**Enforced by:** Harness agent system prompt + invariant § 0.
**On violation:** soft-warn. Agent re-asks explicitly instead of expanding
scope.
**Why:** Silent scope expansion is the single most common source of
unwanted changes.

### C-08

**Title:** Lane switch (answer → mutation) must be explicit.
**When:** A conversation in answer-lane turns into a repo-mutation request.
**Enforced by:** Harness agent prompt — must open planning before writing.
**On violation:** soft-warn + force plan skill before any Write/Edit.
**Why:** Skipping lane switch produces unreviewed, unplanned source changes.

### C-09

**Title:** One repo-mutating task holds write focus at a time.
**When:** A second mutating request arrives while another open task holds this
session's write focus.
**Enforced by:** MCP `wbs(action="open")` and, on Codex, the PostToolUse task binding.
While this session's marker (or, when it names no open task, the legacy
`.active`) names another open task, `wbs(action="open")` refuses with "another open
task owns the resolvable session focus", both for a new task and for resuming
an existing task by id. Once the focused task is closed or parked with
`wbs(action="block")`, resuming an existing task by id re-points focus to it. WBS dependencies determine ready outcomes; there is no Goal child queue. When the Codex MCP process has
no `CODEX_THREAD_ID` (the usual case), `wbs(action="open")` skips this refusal;
PostToolUse then binds the thread to the returned task only when the thread
holds no other open task, and otherwise fences both tasks and clears the
thread's binding, so neither holds source-write focus while both stay open.
**On violation:** hard-block. `wbs(action="open")` returns the refusal, or on Codex the
conflicting binding is fenced; the second request is never merged into the
focused task. Close or park the focused task, then call `wbs(action="open")` again.
**Why:** Parallel mutations make task ownership and review ordering ambiguous.

**Batch clause:** Each linked git worktree is a separate checkout with its own write focus; a coordinator may run one task per worktree via `harness:batch` (see `doc/harness/REQ__parallel-tasks-via-worktree-leads.md`).

### C-10

**Title:** CLAUDE.md is self-managed via continuous maintenance.
**When:** Structural changes to rules, contracts, or the operating mode.
**Enforced by:** active harness tasks with a `MAINTENANCE` marker and close-time
Self-Healing Candidates.
**On violation:** soft-warn from `contract_lint.py`.
**Why:** Ad-hoc edits to CLAUDE.md drift away from enforcement points.

### C-11

**Title:** `CONTRACTS.md` managed block is not hand-edited.
**When:** Any change to rules between the `harness:managed-begin/end` markers.
**Enforced by:** `plugin/scripts/contract_lint.py` (setup/explicit check) —
detects marker tampering; setup regenerates from template. Authorized writers
within the managed block: active tasks with a `MAINTENANCE` marker and explicit
user-authorized contract scope; update the setup template together and preserve
the managed block markers and unrelated guidance.
**On violation:** soft-warn. Restore the managed template through setup or an
authorized maintenance task; put project-specific guidance in the runtime
project document or `doc/`.
**Why:** The managed block is upgraded atomically on harness release; manual
edits are lost.

### C-12

**Title:** Hooks must fail-safe.
**When:** Any hook command in `plugin/hooks/hooks.json`.
**Enforced by:** Convention: every hook ends with `|| true` and has
`timeout ≤ 10`.
**On violation:** hard-block at review — a new hook without fail-safe is
rejected.
**Why:** A flaky hook that blocks the main session is worse than a missing
hook. The harness must degrade gracefully.

### C-13

**Title:** Weight budget — skills bounded, agent fanout batched.
**When:** Adding or editing a SKILL.md; spawning parallel agents in a phase.
**Enforced by:** `plugin/scripts/contract_lint.py --check-weight` —
scans `<plugin-root>/skills/*/SKILL.md` and `<plugin-root>/internal-skills/*/SKILL.md`,
soft-warns any file >500 lines.
Limits: SKILL.md ≤ 500 lines; sub-files read once per phase. Develop fanout is
parallel-first: Phase 3 independent ACs, Phase 4.5-4.8 quality agents, Phase 7
QA lenses, and Phase 7.7 dogfooder follow `plugin/skills/develop/parallel-fanout.md`.
Agent batches are capped there; do not replace a required fanout with a single
coordinator lane to satisfy this weight budget. Meet the budget by deleting
duplicated generic workflow prose and retaining each role's unique gates and
rubrics; do not move duplicated prompt text into new reference files.
**On violation:** soft-warn.
**Why:** Harness instability grows super-linearly with loop size. Every
extra phase is a new failure point.

### C-14

**Title:** Actual independent review PASS precedes actual QA.
**When:** A repo-mutating task advances to QA or completion.
**Enforced by:** Canonical run/develop workflow: await independent code review and conditional security review PASS, then run applicable QA. Fix substantive failures and report actual environmental blockers. WBS verify/close enforce measured outcome checks, not review attestation.
**On violation:** Do not advance or claim completion; remediate the failed or unrun substantive work.
**Why:** Independent scrutiny and actual execution protect quality. Receipt collection, spawn-name identity, missing-attestation parking and receipt-only reruns are removed without a replacement ledger.
**Normative detail:** `doc/harness/REQ__receipt-free-execution-and-completion.md`.

### C-14a

**Title:** Highest available verification is part of the task.
**When:** A task creates, unblocks, or documents a local verification path.
**Enforced by:** `plugin/skills/develop/SKILL.md` and
`plugin/skills/develop/verification-gate.md` — Phase 7 must run the highest
available local tier instead of asking the user whether to verify.
**On violation:** soft-warn, then run the verification or document the exact
external/destructive blocker.
**Why:** Asking whether to use the verification path treats task completion as
optional extra work. It is not optional; the verifier reports the tier reached.

### C-15

**Title:** Setup must not overwrite user-owned files.
**When:** `setup` or continuous maintenance installs/updates harness files.
**Enforced by:** Skill procedure — the runtime project document gets at most a
1-line `@CONTRACTS.md` import plus the bounded Harness routing block;
`CONTRACTS.md` respects managed-block markers. Every other line in the runtime
project document remains user-owned.
**On violation:** hard-block. Any rewrite outside those two bounded,
setup-owned operations must present a diff via `AskUserQuestion` first.
**Why:** User trust is the most load-bearing contract. Surprise overwrites
break it immediately.

### C-17

**Title:** Turn-end/continuation guidance while a task is open.
**When:** A task's `.active` marker exists and work is unfinished.
**Enforced by:** `wbs(action="close")` requires current WBS checks; `wbs(action="block")` publishes a real unfinished environmental blocker. Persistent continuation across turns is native `/goal`.
**On violation:** soft — open work stays open and resumes through `wbs(action="open")`/`wbs(action="context")`. Cancel only on explicit user instruction; never offer cancellation as a routine blocker workaround.
**Why:** A turn ending is not task completion. Parking grants no PASS and does not bypass close. Resume preserves the same run and measured checks. Missing receipt recording is never a blocker; actual review PASS still precedes actual QA.

**Parking clause:** `wbs(action="block")` records unfinished work and grants no PASS under C-04. Resolve the blocker and use `wbs(action="open")` to resume the same task and run.

### C-18

**Title:** Verification delegation is workflow guidance, not a pre-tool gate.
**When:** Choosing where to run browser-driving tools or a heavy full-suite verification pass.
**Enforced by:** The develop workflow prefers the applicable `qa-*` lens when
delegation is available and isolation materially reduces context or process
load. No generic PreToolUse hook inspects or blocks browser calls. Targeted
tests, diagnostics, and browser interaction may run inline when that is the
lightest available verification path. Actual independent review PASS must precede actual QA; WBS verification measures outcome checks.
**On violation:** advisory only. Inline execution is allowed; the orchestrator
owns the resulting context growth and must still provide the required ordered
verification evidence before close.
**Why:** Browser MCP payloads (DOM snapshots, screenshots, evaluate output)
bloat main context with thousands of structured tokens per call. qa-browser
isolates browser verification and returns findings in its final response through its actual final response. Paying that cost on every
PreToolUse event to prevent occasional inline browser use was a worse default
than letting the workflow select the execution lane. Evidence: 2026-05-13
user-observed catchy-secrets session where main agent ran `chrome-devtools`
inline and stalled mid-task; user feedback 2026-05-14 narrowed scope to
MCP-only after the Bash matcher fired on legitimate `pytest`/`vitest` use.

<!-- harness:managed-end -->
