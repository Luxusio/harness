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
   artifact without its owner, closing with stale PASS, or bypassing the
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
| 보호 아티팩트 쓰기 (PLAN/CHECKS/HANDOFF/DOC_SYNC/CRITIC__qa) | [C-03](#c-03), [C-05](#c-05) | hard |
| `task_close` 시점 | [C-01](#c-01), [C-04](#c-04), [C-14](#c-14) | hard |
| 짧은 승인 (`ㅇㅇ`, `ㄱ`) 수신 | [C-07](#c-07) | soft |
| 답변 레인 → mutation 레인 전환 | [C-07](#c-07), [C-08](#c-08) | hard |
| develop Phase 4.5 병렬 에이전트 | [C-13](#c-13) | soft |
| 신규 훅 추가 | [C-12](#c-12) | hard |
| `doc/` 노트 파일 변경 | [C-06](#c-06) | auto |
| `CLAUDE.md` 편집 필요 | [C-10](#c-10), [C-11](#c-11), [C-15](#c-15) | hard |
| Maintenance 태스크 (MAINTENANCE 마커) | C-01 완화, [C-05](#c-05) 유지 | — |
| `doc/changes/` 또는 `doc/common/` 자동 정리 | [C-16](#c-16) | auto |
| Task in_progress 동안 turn 종결 시점 | [C-17](#c-17) | hard |
| 메인 세션이 브라우저 MCP 도구 (`mcp__chrome-devtools__*`) 호출 시도 | [C-18](#c-18) | soft |

Levels:
- **hard** — gate blocks or MCP refuses. Violation is impossible by default.
- **soft** — warning/log. Agent must self-correct.
- **auto** — runs in background (hook), advisory.

## 2. Contracts

Every contract below has exactly four fields: **When**, **Enforced by**,
**On violation**, **Why**. If you cannot fill all four, it is not a
contract — move it to design notes.

### C-01

**Title:** Canonical loop — plan → develop → verify → close.
**When:** Any task that mutates repo state (non-maintenance).
**Enforced by:** `plugin/scripts/prewrite_gate.py` (source write blocked
without PLAN.md), MCP `task_close` (rejects pending `runtime_verdict`).
**On violation:** hard-block.
**Why:** Skipping steps loses evidence and provenance — stale verdicts,
missing regression tests, orphan artifacts.

### C-02

**Title:** Plan-first — no source write before PLAN.md exists.
**When:** Any `Write` or `Edit` to a source file on an active task.
**Enforced by:** `plugin/scripts/prewrite_gate.py`.
**On violation:** hard-block with message pointing to plan skill.
**Why:** Implementation without a plan drifts scope and produces unreviewable
diffs.

### C-03

**Title:** CHECKS.yaml updates go through `update_checks.py` only.
**When:** Any AC status transition after plan close (develop, verify).
**Enforced by:** `plugin/scripts/prewrite_gate.py` (direct Write/Edit of
`CHECKS.yaml` is blocked); the CLI bypasses the gate via atomic rename.
**On violation:** hard-block.
**Why:** `reopen_count`, `last_updated`, `evidence` stay consistent.
Hand-edits break audit trail and close-gate accounting.

### C-04

**Title:** `task_close` requires fresh `runtime_verdict: PASS`.
**When:** Task is about to be marked closed.
**Enforced by:** MCP `task_close` — re-syncs touched paths, rejects stale
PASS after any file change post-verify.
**On violation:** hard-block.
**Why:** A PASS issued before the last edit proves nothing about the current
state of the repo.

### C-05

**Title:** Protected artifact ownership.
**When:** Any `Write`/`Edit` to PLAN.md, CHECKS.yaml, HANDOFF.md,
DOC_SYNC.md, or CRITIC__qa.md — and any `Bash` mutation (sed -i,
redirect, cp, mv, tee, python -c open(…,'w'), …) targeting the same basenames.
**Enforced by:** `plugin/scripts/prewrite_gate.py` `PROTECTED_ARTIFACTS`
(Write/Edit/MultiEdit surface) + `plugin/scripts/mcp_bash_guard.py`
(Bash surface; same helper classifiers).
**On violation:** hard-block. Agent must route through the owning skill or CLI.
**Why:** Provenance is derived from artifact existence. Wrong writer = wrong
provenance = broken audit chain. The Bash surface was added in PR1
(`TASK__gate-reliability-pr1`) to close the `sed -i PLAN.md` / `echo >> CHECKS.yaml` bypass.
**Note (AC-019):** `doc/changes/**` and `doc/common/**` writes by
`hygiene_scan.py` and `doc_hygiene.py` are authorized via C-16. These paths
are NOT in `PROTECTED_ARTIFACTS`; their protection is via `hygiene.yaml`
validation + observer phase + `maintain_restore.py` reversibility.

### C-06

**Title:** Note freshness — `invalidated_by_paths` flips `current → suspect`.
**When:** Any SessionStart after files changed in the last commit.
**Enforced by:** `plugin/scripts/note_freshness.py` (SessionStart hook).
**On violation:** auto — stale notes keep `freshness: current` until the
next hook run. Writer-role agents must verify `freshness: current` before
citing a note as authoritative.
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
**Why:** Parallel mutations corrupt touched-path tracking and verdicts.

### C-10

**Title:** CLAUDE.md is self-managed via the `maintain` skill.
**When:** Structural changes to rules, contracts, or the operating mode.
**Enforced by:** `plugin/skills/maintain/SKILL.md` — the only skill that
edits CLAUDE.md's harness-managed section.
**On violation:** soft-warn from `contract_lint.py`.
**Why:** Ad-hoc edits to CLAUDE.md drift away from enforcement points.

### C-11

**Title:** `CONTRACTS.md` managed block is not hand-edited.
**When:** Any change to rules between the `harness:managed-begin/end` markers.
**Enforced by:** `plugin/scripts/contract_lint.py` (SessionStart hook) —
detects marker tampering; `maintain` skill regenerates from template.
Authorized writers for additive Edits within the managed block:
`maintain` skill (all changes) and `hygiene_scan.py` (additive Edits only,
never deletions, never edits outside the managed block markers).
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
scans `plugin/skills/*/SKILL.md`, soft-warns any file >500 lines.
Limits: SKILL.md ≤ 500 lines; sub-files read once per phase. Develop fanout is
parallel-first: Phase 3 independent ACs, Phase 4.5-4.8 quality agents, Phase 7
QA lenses, and Phase 7.7 dogfooder follow `plugin/skills/develop/parallel-fanout.md`.
Agent batches are capped there; do not replace a required fanout with a single
coordinator lane to satisfy this weight budget.
**On violation:** soft-warn.
**Why:** Harness instability grows super-linearly with loop size. Every
extra phase is a new failure point.

### C-14

**Title:** PASS verdicts require structured evidence.
**When:** `runtime_verdict` transitions to `PASS`.
**Enforced by:** `CRITIC__qa.md` schema — must contain specific
test/screenshot/log references, not a bare verdict.
**On violation:** soft-warn. `task_close` additionally demands the file
exists and is fresh (C-04).
**Why:** A PASS without evidence is indistinguishable from hallucination.

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
**When:** `setup` or `maintain` skill installs/updates harness files.
**Enforced by:** Skill procedure — `CLAUDE.md` gets at most a 1-line
`@CONTRACTS.md` import; `CONTRACTS.md` respects managed-block markers;
`CONTRACTS.local.md` is never touched once created.
**On violation:** hard-block. Any rewrite of user-authored content must
present a diff via `AskUserQuestion` first.
**Why:** User trust is the most load-bearing contract. Surprise overwrites
break it immediately.

### C-16

**Title:** Auto-hygiene — observer-mode content-signal doc classification + contract drift auto-apply.
**When:** SessionStart (automatic, gated by `observer_until_session`) and whenever `Skill(maintain)` is invoked.
**Enforced by:** `plugin/scripts/hygiene_scan.py` (SessionStart hook, after
`contract_lint --quick`); `plugin/scripts/doc_hygiene.py` (called by
hygiene_scan); `doc/harness/hygiene.yaml` (config + canonical disable path).
**On violation:** auto — hygiene is advisory; failure degrades to no-op.
**Why:** Without automatic cleanup, `doc/changes/` and `doc/common/` accumulate
indefinitely. Institutional memory erodes when the signal-to-noise ratio drops.
Frontmatter signals: `superseded_by` and `distilled_to`; observer mode is
controlled by `observer_until_session`.

**Tier A/B/C mapping (contract drift):**
- `[INFO]` (Tier A): auto-applied as additive Edit within managed-block markers. No deletions.
- `[SOFT]` additive (Tier B): auto-applied if action is matrix-row addition or contract heading addition only. Modifications/deletions deferred.
- `[HARD]` (Tier C): deferred. Entry written to `.maintain-pending.json`; user confirms via `Skill(maintain)`.

**Commit timing:** `doc_hygiene.py` stages archive moves at SessionStart but
does NOT commit. Batch commits happen only via `Skill(maintain)` user
invocation (see `plugin/skills/maintain/SKILL.md` Phase 2.5). Rationale:
a SessionStart hook running `git commit` without an internal timeout can
be SIGKILL'd by the outer hook timeout mid-write, leaving a stale
`.git/index.lock`.

**KEEP-on-doubt rule:** absence of `superseded_by` or `distilled_to` frontmatter
fields NEVER alone classifies a doc as REMOVE. Cold-start docs (no new frontmatter)
always classify as KEEP or REVIEW.

**Observer phase:** first `observer_until_session` sessions (default 14) run
in observer-only mode — no archive writes, no contract edits. Intentions logged
to `doc/harness/.maintain-observe.log`.

**Restore:** `python3 plugin/scripts/maintain_restore.py <archive-path>`.
Archive commit message always embeds the copy-pasteable restore command.

**Frontmatter fields (optional, added to individual doc files):**
- `superseded_by: <path>` — this doc is replaced by `<path>`; if target exists
  AND `reference_count == 0`, classify REMOVE.
- `distilled_to: <path>` — key content promoted to `<path>`; if target exists
  AND `reference_count == 0`, classify REMOVE.

### C-17

**Title:** Task in_progress 동안 turn 종결 사유는 **fresh** verified runtime_verdict 또는 사용자 명시 cancel 뿐.
**When:** Stop event with `.active` marker present (any task `status` ∈ {planning, implementing, verifying}).
**Enforced by:** `plugin/scripts/stop_gate.py` (gate-blocks unless `runtime_verdict ∈ {PASS, BLOCKED_ENV}` AND the verdict is FRESH per `_lib.runtime_is_stale`); `plugin/agents/stop-judge.md` (the only authorized writer of the BLOCKED_ENV transition, via `write_critic_qa(lens='stop-judge')`); MCP `write_critic_qa` (verdict enum gate, worst-wins severity merge) + MCP `task_close` (PASS-only gate, plus staleness gate).
**On violation:** hard-block (Stop hook refuses turn-end). Claude must call `task_verify`/`task_close` for PASS or spawn `Agent(subagent_type='harness:stop-judge')` for BLOCKED_ENV. Cancel options must never be surfaced to the user inside AskUserQuestion; cancel is recognized only as an explicit user word.

**Staleness clause (2026-05-14 fix):** a frozen `BLOCKED_ENV` verdict permits stop ONLY when no `touched_paths` file has `mtime > mtime(CRITIC__qa.md)`. If activity post-dates the verdict, the verdict is stale and the Stop hook MUST fall through to the block payload — the orchestrator must spawn a fresh `harness:stop-judge` (which re-assesses against current state and writes a new `CRITIC__qa.md` row) or run `task_verify` toward PASS. Staleness is computed once in `_lib.emit_compact_context` via `_lib.runtime_is_stale` and surfaced as `ctx["stale"]` / `ctx["stale_path"]`; both `stop_gate.py` and the MCP `task_close` gate read this field. The 2026-05-14 bug: a stale `BLOCKED_ENV` from codex-auth-blocked earlier in a session permitted stops AFTER develop made `implemented_candidate` progress on 3 ACs — that loophole is now closed.

**Why:** 회고 #1 silent-scope-kill — `stop_gate.py:97-99` 의 "AskUserQuestion 으로 cancel 묻기" 안내가 모호한 종결 지시를 task cancel 로 변환시키던 메커니즘 제거. Stop-judge 의 의미 판단이 runtime_verdict machine gate 의 input — prose-only 룰의 commentary 화 위험 (§0) 회피. 모델 회귀로 인한 조기 종결 시도도 runtime_verdict gate 가 무력화.
Staleness gate (2026-05-14) closes the second loophole: a verdict from an EARLIER state of the session continuing to permit stops on a LATER state. C-17 enforcement is now anchored to "what the touched_paths look like right now", not "what verdict was historically written".

### C-18

**Title:** Verification delegation — main session never drives browser MCP or full-suite verification inline.
**When:** Any `mcp__chrome-devtools__*` tool invocation from the main (orchestrator) session (e.g. `take_snapshot`, `take_screenshot`, `evaluate_script`, `navigate_page`, `click`, `fill`), or Phase 7 full-suite verification.
**Enforced by:** `plugin/scripts/qa_delegation_gate.py` (PreToolUse, no matcher — script self-filters by `tool_name` prefix `mcp__chrome-devtools__`). The gate allows delegated `harness:qa-browser` calls, then emits a deny envelope whose `permissionDecisionReason` surfaces as a system-reminder so non-delegated callers self-redirect to `Agent(subagent_type='harness:qa-browser')`. qa-browser detection prefers explicit agent fields when the runtime exposes them and falls back to a capped `transcript_path` prologue check for the qa-browser agent prompt. Bypass: `HARNESS_SKIP_QA_DELEGATION=1` one-shot. Bash test runners (`pytest`, `npm test`, `vitest`, `pnpm test`, `cargo test`, `go test`, `jest`, `mocha`, `rspec`, `phpunit`, …) are not hook-blocked because targeted per-AC and debug reruns are legitimate inline use; Phase 7 full-suite runs MUST be delegated to qa-* lenses and spawned in parallel when multiple lenses apply. Network probes (`curl`, `wget`, `httpie`) and DB probes (`psql -c`, `mysql -e`, `alembic`) also remain unblocked for targeted diagnostics.
**On violation:** soft-warn. WARN logs to `learnings.jsonl` `type=qa-delegation-warn` each fire; weekly retro reads frequency to decide v2 escalation.
**Why:** Browser MCP payloads (DOM snapshots, screenshots, evaluate output) bloat main context with thousands of structured tokens per call. qa-browser isolates browser verification and writes structured findings to `CRITIC__qa.md` — the orchestrator reads only the verdict. Evidence: 2026-05-13 user-observed catchy-secrets session where main agent ran `chrome-devtools` inline and stalled mid-task; user feedback 2026-05-14 narrowed scope to MCP-only after the Bash matcher fired on legitimate `pytest`/`vitest` use.

<!-- harness:managed-end -->

@CONTRACTS.local.md
