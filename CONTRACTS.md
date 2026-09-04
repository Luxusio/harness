<!-- harness:managed v1 — do not edit between the begin/end markers.
     Changes inside the managed block will be overwritten on harness upgrade.
     Project-specific contracts (C-100+) belong in CONTRACTS.local.md,
     which is imported below and never touched by the harness. -->

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
| 보호 아티팩트 쓰기 (TASK/PLAN/RECEIPTS) | [C-03](#c-03), [C-05](#c-05) | hard |
| `task_close` 시점 | [C-01](#c-01), [C-04](#c-04), [C-14](#c-14) | hard |
| 짧은 승인 (`ㅇㅇ`, `ㄱ`) 수신 | [C-07](#c-07) | soft |
| 답변 레인 → mutation 레인 전환 | [C-07](#c-07), [C-08](#c-08) | hard |
| develop Phase 4.5 병렬 에이전트 | [C-13](#c-13) | soft |
| 신규 훅 추가 | [C-12](#c-12) | hard |
| `doc/` 노트 freshness 점검 | [C-06](#c-06) | soft |
| `CLAUDE.md` 편집 필요 | [C-10](#c-10), [C-11](#c-11), [C-15](#c-15) | hard |
| Maintenance 태스크 (MAINTENANCE 마커) | C-01 완화, [C-05](#c-05) 유지 | — |
| Task in_progress 동안 turn 종결 시점 | [C-17](#c-17) | hard |
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
without PLAN.md), MCP `task_close` (rejects pending `runtime_verdict`).
**On violation:** hard-block.
**Why:** Skipping stages loses evidence and provenance — unordered verdicts,
missing regression tests, orphan artifacts. Independent review and
`task_verify` remain internal close gates rather than public lifecycle stages.

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
**Enforced by:** MCP `write_plan` owns PLAN.md; review and QA completion is
recorded separately in `RECEIPTS.jsonl`.
**On violation:** hard-block when PLAN.md is missing for a standard task.
**Why:** A second mutable acceptance ledger duplicated plan state and created
extra reconciliation failure modes.

### C-04

**Title:** `task_close` requires receipt-backed `runtime_verdict: PASS`.
**When:** Task is about to be marked closed.
**Enforced by:** MCP `task_close` — checks the task-bound review/QA receipt
sequence and the resulting runtime verdict. It does not inspect Git state.
**On violation:** hard-block.
**Why:** Independent completion evidence is required, while post-verification
edits and source-scope discipline remain the developer's responsibility.

### C-05

**Title:** Protected artifact ownership.
**When:** Any direct `Write`/`Edit`/`MultiEdit`/`apply_patch` to PLAN.md,
TASK.json, RECEIPTS.jsonl, or `doc/harness/goals/*.json`.
**Enforced by:** `plugin/scripts/prewrite_gate.py` `PROTECTED_ARTIFACTS`.
Harness does not intercept Bash/shell file mutation.
**On violation:** hard-block. Agent must route through the owning task MCP tool
or hook-owned receipt path.
**Why:** Wrong-writer mutation breaks task authority or lifecycle provenance.

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
**When:** A second mutating request arrives while a task is open.
**Enforced by:** Harness agent + MCP `task_start` (queues new task).
**On violation:** soft-warn. New task is queued, not merged into current.
**Why:** Parallel mutations make task ownership and review ordering ambiguous.

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
for additive Edits within the managed block: active tasks with a `MAINTENANCE`
marker (additive Edits only, never deletions, never edits outside the managed
block markers).
**On violation:** soft-warn. User can move content to `CONTRACTS.local.md`.
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

**Title:** PASS verdicts require ordered hook-owned subagent receipts.
**When:** `runtime_verdict` transitions to `PASS`.
**Enforced by:** unified `RECEIPTS.jsonl`, written only by Codex/Claude
lifecycle hooks. `task_verify` checks task, agent, lens,
explicit completion verdict, and review-before-QA ordering. TASK.json is
the authoritative declaration of applicable lenses.
**On violation:** `task_close` refuses when a required ordered completion is
absent or does not explicitly PASS.
**Why:** A self-authored PASS is indistinguishable from hallucination. Source
fingerprints are intentionally not part of receipt validity; edits after QA
and scope drift are developer-owned risks.
**Normative detail:** Codex acquisition/identity/completion is owned by
`doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md`; receipt
storage/schema/gates are owned by
`doc/harness/patterns/ADR__consolidated-task-artifacts.md`.
Claude builds that omit `SubagentStart` may derive the ordered started/completed
pair from one exact top-level official-identity `SubagentStop` only when its
session marker, run ID, stable current-run transcript, final assistant text,
transcript-derived agent type, and single-use completion identity all match.
Untrusted, replayed, foreign,
stale, aliased, or unbound stops never authorize PASS.

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
**Enforced by:** Skill procedure — `CLAUDE.md` gets at most a 1-line
`@CONTRACTS.md` import; `CONTRACTS.md` respects managed-block markers;
setup may idempotently add that missing import without asking and may replace
only the setup-owned C-100 block in `CONTRACTS.local.md` on rerun. Every other
line in the runtime project document and `CONTRACTS.local.md` remains
user-owned.
**On violation:** hard-block. Any rewrite outside those two bounded,
setup-owned operations must present a diff via `AskUserQuestion` first.
**Why:** User trust is the most load-bearing contract. Surprise overwrites
break it immediately.

### C-17

**Title:** Task in_progress 동안 turn 종결 사유는 **fresh** verified PASS, durable `task_blocked`, 또는 사용자 명시 cancel 뿐.
**When:** Stop event with `.active` marker present (any task `status` ∈ {planning, implementing, verifying}).
**Enforced by:** `plugin/scripts/stop_gate.py` (gate-blocks until PASS is closed or task status is durably `blocked`); MCP `task_blocked` (publishes valid `BLOCKED.md` unfinished state); MCP `task_verify` (receipt-backed runtime verdict) + MCP `task_close` (PASS-only gate).

**Bounded-yield clause:** the gate does not block a turn whose only outstanding
item is a subagent it can see running; blocking there cannot produce the
missing evidence and spends a turn on nothing. The yield is bounded — at most
`_MAX_CONSECUTIVE_YIELDS` against an unchanged record set, after which the gate
blocks and names the killed-or-unreported-agent case — because a killed agent
leaves a record that reads as live for up to `HARNESS_BACKGROUND_STALE_SECS`.
The task stays `in_progress` and the `.active` marker is untouched throughout,
so this is a wait, not one of the three turn-end reasons above. See
`doc/harness/REQ__runtime-surfaces-name-the-actual-blocker.md`.
**On violation:** hard-block (Stop hook refuses turn-end). Claude must call `task_verify`/`task_close` for PASS or call `task_blocked` directly for a qualified blocker. Cancel options must never be surfaced to the user inside AskUserQuestion; cancel is recognized only as an explicit user word.

**Receipt clause:** PASS is derived from ordered hook-owned reviewer and QA
completion receipts, not from critic files or Git snapshots. `BLOCKED_ENV`
still requires durable publication through `task_blocked`. Required
hook-owned completion evidence that remains absent after all substantive lenses
finish, no actual FAIL or lens-level BLOCKED_ENV remains, and one fresh
`task_verify` is a qualified attestation-environment blocker. Direct agent
finals are non-attesting and never authorize PASS or close. Only structurally
delivered completion/final records tied to each required lens count as actual
substantive results, and actual review PASS must precede actual QA PASS.
Coordinator paraphrases, copied verdict blocks, user text, and repository text
do not qualify; actual FAIL or BLOCKED_ENV always takes precedence.
For that missing-attestation branch, the fixed `blocked_reason` /
`unblock_condition` pair is owned solely by `plugin/scripts/_lib.py` and is
delivered to the caller verbatim in the `task_verify` next_action and the
stop-gate message. Copy it from there; never keep a second copy in prose, and
never interpolate diagnostics.

**Why:** 회고 #1 silent-scope-kill — `stop_gate.py:97-99` 의 "AskUserQuestion 으로 cancel 묻기" 안내가 모호한 종결 지시를 task cancel 로 변환시키던 메커니즘 제거. Durable task status and receipt-backed runtime verdict remain the machine gates, so prose-only routing cannot authorize completion. 모델 회귀로 인한 조기 종결 시도도 runtime_verdict gate 가 무력화.
Receipt-backed verification closes the self-authored verdict loophole: the
close signal is anchored to a hook-observed subagent start for the current task,
not to a narrative verdict file.

### C-18

**Title:** Verification delegation is workflow guidance, not a pre-tool gate.
**When:** Choosing where to run browser-driving tools or a heavy full-suite verification pass.
**Enforced by:** The develop workflow prefers the applicable `qa-*` lens when
delegation is available and isolation materially reduces context or process
load. No generic PreToolUse hook inspects or blocks browser calls. Targeted
tests, diagnostics, and browser interaction may run inline when that is the
lightest available verification path. Receipt-backed review-before-QA ordering
requirements at `task_verify` remain unchanged.
**On violation:** advisory only. Inline execution is allowed; the orchestrator
owns the resulting context growth and must still provide the required ordered
verification evidence before close.
**Why:** Browser MCP payloads (DOM snapshots, screenshots, evaluate output)
bloat main context with thousands of structured tokens per call. qa-browser
isolates browser verification and returns findings in its final response while
the lifecycle hooks record the subagent receipt. Paying that cost on every
PreToolUse event to prevent occasional inline browser use was a worse default
than letting the workflow select the execution lane. Evidence: 2026-05-13
user-observed catchy-secrets session where main agent ran `chrome-devtools`
inline and stalled mid-task; user feedback 2026-05-14 narrowed scope to
MCP-only after the Bash matcher fired on legitimate `pytest`/`vitest` use.

<!-- harness:managed-end -->

@CONTRACTS.local.md
