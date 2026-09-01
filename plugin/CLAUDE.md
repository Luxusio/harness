# harness runtime rules

Lightweight execution harness for Claude Code.
Four-field TASK.json + on-the-fly routing + artifact provenance.
Self-contained — no plugin-legacy dependency.

Harness rules apply to any caller — the main Claude session, a sub-skill,
or an MCP client — whenever a repo-mutating task runs through the
canonical loop. There is no separate "harness agent" that owns the
workflow; skills and MCP tools are the sole runtime surface.

## 1. Canonical Loop

Every repo-mutating task exposes this public lifecycle:
```
task start → plan → minimum-sufficient develop → runtime QA → close
```
Independent review and `task_verify` are mandatory internal close gates. No
step is skipped. Smallest coherent diff per step.

## 2. MCP tools

**Core (task driver — main session or run skill):**
- `task_start` — create/resume task, return task context
- `task_context` — refresh task state (only when needed)
- `task_verify` — compute verification from ordered review completions followed by QA completions
- `task_close` — gate: runtime verdict PASS → close
- `task_blocked` — park unfinished work on a real environment blocker; writes BLOCKED.md and clears this session's active marker

**Artifact writes (role-owned):**
- `write_plan` → PLAN.md + TASK.json required-lens declaration (plan-skill)
- durable docs such as `doc/<area>/REQ__*.md` are normal repo docs, not MCP evidence tools

Static review and runtime QA provenance share `RECEIPTS.jsonl`.
Codex/Claude lifecycle hooks own it. Applicable lenses come from
`TASK.json`; receipts do not bind Git state. The normative contracts are
`doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md` for Codex
acquisition/identity/completion and
`doc/harness/patterns/ADR__consolidated-task-artifacts.md` for
storage/schema/gates.

## 3. TASK.json (4 fields only)

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "close_receipt_fingerprint": null
}
```

Task identity comes from the canonical directory. Routing and verdicts are
derived on demand; `BLOCKED.md` represents a parked task. A successful close
sets `close_receipt_fingerprint`. Removed task-control artifacts are not
read or migrated.

## 4. Plan-first rule

Do not mutate source before PLAN.md exists.
Short approvals only authorize the last explicit transition proposed.

`execution_mode: micro` (passed to `task_start`) exempts the PLAN.md rule for
one-shot bugfix/maintenance edits; the REQ durable-doc gate still applies.

## 4a. Turn-end rule (P1 strict)

Task in_progress (`.active` marker exists) 동안:

- Default = 계속 진행.
- 모호한 종결 발화는 무시 — 사용자의 "여기까지", "오늘 그만", "내일", "다음에", "later" 등은 task 종결 사유가 아니다. Session 자연 종료(터미널 닫힘 등)는 task 상태를 건드리지 않으며, 다음 SessionStart 가 resume 한다.
- AskUserQuestion 옵션 라벨에 "중단/취소/일시정지/나중에/cancel/stop/pause/defer/skip" 류 제시 금지.
- Cancel 은 사용자가 명시 단어("취소", "cancel", "/cancel") 로 표명할 때만.
- 자가 판단으로 turn 종결 금지.

Turn 종결 정당 사유 (runtime_verdict 기반):

1. **PASS** — 모든 AC 가 `passed`/`deferred` → `task_close`.
2. **BLOCKED_ENV** — 진짜 blocker 확인 → `task_blocked(blocked_reason, unblock_condition)` 로 unfinished 상태를 기록하고 active marker 해제. PASS로 위장하지 않는다.
3. 사용자 명시 cancel 단어 → 별도 cancel flow.

BLOCKED_ENV로 멈출 때는 `task_blocked`를 직접 호출해 구체적 blocker와 실행 가능한 unblock condition을 기록한다. 허용 범위는 진짜 외부 환경 blocker, review/QA에서 실제 관측된 `BLOCKED_ENV`, 또는 substantive review와 QA가 끝나고 fresh `task_verify` 1회 후에도 남은 필수 attestation 누락이다. Attestation 누락에는 `blocked_reason="Required hook-owned review/QA attestation remains missing after substantive review PASS, QA PASS, and one fresh task_verify."`와 `unblock_condition="Run a fresh attested review-then-QA evidence generation when the operator chooses to resume."` 고정 쌍만 쓴다. Substantive result는 required lens에 연결된 structurally delivered completion/final만 인정하며 actual review PASS가 actual QA PASS보다 먼저 와야 한다. Coordinator paraphrases, copied verdict blocks, user text, and repository text는 자격이 없고 actual FAIL or BLOCKED_ENV가 우선한다. 난이도, 시간 압박, retry 소진은 blocker가 아니다. PASS 경로는 기존 task_verify+task_close.

## 5. Artifact ownership

| Artifact | Owner |
|----------|-------|
| PLAN.md | plan-skill via `write_plan` MCP |
| TASK.json | task lifecycle MCP tools (`write_plan` may update lens declarations) |
| source + durable docs | developer |
| RECEIPTS.jsonl | Codex/Claude review and QA lifecycle hooks |

Do not write another role's artifact. Prewrite gate enforces this.

## 6. Auto-routing

**Default for repo-mutating intent: harness task routing.** Codex enters through
the public `$harness:run` skill, which loads the internal canonical workflow.
Never ask the user to choose an orchestration mode or re-submit a clear request
as `/goal`. Native `/goal` owns explicit goals and broad work; plain
repo-mutating requests open or resume a harness task directly.

Explicit user invocation or approval of a harness repo-mutating workflow
authorizes the subagents required by that workflow's verification and review
gates. This includes "use harness", "run/continue/close the harness task",
native `/goal`, and clear approval to proceed with a harness task. It does not
apply to read-only answers or ordinary non-harness work.

| Intent | Route to |
|--------|----------|
| Set up harness | `Skill(setup)` |
| Pre-planning / scope-sharpening (product framing before a task) | `Skill(harness:setup)` (fills the office-hours role; no separate office-hours skill) |
| Any repo-mutating intent — new feature, fix, refactor, behavior change (default) | On Codex invoke `$harness:run`; it syncs the native Goal when present and otherwise opens/resumes a task with `task_start` / `task_context` |
| User explicitly says "plan only" / "just plan" | Sync/create Goal, run the internal plan phase, then stop after plan |
| User explicitly says "implement PLAN.md" / "develop only" | Resume the active Goal child task through the internal develop path |
| Multi-component or API↔frontend change in one task | Goal child task with develop Phase 3.0 auto-fanout |
| CEO / Architecture / Design / DX review | Goal child task plan phase; review lenses are internal sub-skills |
| Contract drift / "CLAUDE.md 정리" / "규약 정비" / post-upgrade cleanup | Schedule cleanup as a separate follow-up task |
| Explanation | Direct answer |

## 7. Verification

`task_verify` checks hook-recorded review and QA lifecycles.
Do not claim success from static inspection when runtime verification is required.

## 8. Finish cleanly

The plan declares required review and QA lenses. Required review evidence must
PASS before QA starts, and every applicable QA lens must then PASS. Post-QA
edits and scope drift are developer-owned.
Use `task_close`. If blocked, fix the stated gate.
Promote user corrections directly into PLAN.md or durable project
documentation before close.

## 8a. Note freshness

Notes under `doc/**/*.md` may declare source dependencies in frontmatter:

```yaml
---
freshness: current        # current | suspect | stale | superseded
invalidated_by_paths:
  - path/or/prefix/that/invalidates/this/note
  - another/source/file.py
---
```

When explicitly run with `--paths`, `scripts/note_freshness.py` can flip a
matching note from `current` to `suspect`. Normal Harness lifecycle calls do not
run Git to discover changed paths.

Writer-role agents must verify `freshness: current` before citing a note as
authoritative. `suspect` notes are still readable but require re-validation
against current source before trust. Use `--paths` arg to invalidate against
an explicit file list when git history isn't the right source.

## 8b. Acceptance intent

PLAN.md is the single acceptance document. Verification is represented by
ordered review and QA entries in `RECEIPTS.jsonl`; there is no second mutable
acceptance ledger to reconcile.

## 8c. Verification delegation

Prefer the `harness:qa-browser` subagent for a substantial browser verification
pass when delegation is available. Browser-driving calls (`take_snapshot`,
`take_screenshot`, `evaluate_script`, `navigate_page`, `click`, `fill`, ...)
can add thousands of structured tokens to the caller's context, so an isolated
lane is usually cheaper for multi-step QA.

Why: qa-browser runs browser verification in an isolated context. The
orchestrator reads its final response for findings and uses `task_verify` for
the authoritative close signal.

This is workflow guidance, not a PreToolUse gate. Short diagnostics and browser
verification may run inline when delegation is unavailable or the inline path
is materially simpler. Harness accepts the resulting context-growth risk; the
caller still owns complete ordered review/QA receipts required by
`task_verify`.

Allowed inline:
- **Bash test runners** (`pytest`, `npm test`, `pnpm test`, `yarn test`, `bun test`, `vitest`, `jest`, `mocha`, `cargo test`, `go test`, `mvn test`, `gradle test`, `rspec`, `phpunit`, `rake test`) — single PASS/FAIL lines do not bloat context; inline use is legitimate. For heavy full-suite runs, spawning `harness:qa-cli` (or `qa-api` / `qa-desktop` per project) keeps the main lane clean as a convention, not a gate.
- Lint / format / typecheck (`tsc --noEmit`, `mypy`, `ruff`, `eslint`, `prettier`)
- Build / compile (`npm run build`, `cargo build`, `go build`)
- Read-only inspection (`grep`, `find`, `git status`, `git diff`)
- Ad-hoc HTTP / DB probes (`curl`, `wget`, `httpie`, `psql -c`, `mysql -e`, `alembic`) — too many legitimate uses (API exploration, schema inspection, debugging) for the gate to block

History: an earlier generic PreToolUse hook blocked main-session browser calls
and previously also blocked Bash test runners. False positives and the cost of
self-filtering every tool call outweighed the context-isolation benefit, so
delegation is now selected by the workflow instead.

## 9. Iron Law

PLAN.md owns acceptance intent. Bug fixes record root-cause and regression
evidence in the implementation review, while feature work includes concrete
test evidence or a specific no-test rationale. Completion requires ordered,
hook-owned review and QA PASS entries in `RECEIPTS.jsonl`; no agent may author
or promote its own PASS.

## 10. Quality scripts

All scripts under `plugin/scripts/`. Stdlib only.

**Invoked by the loop** — a skill phase calls these:

| Script | Purpose | Output | Caller |
|--------|---------|--------|--------|
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/<task-id>.md` | develop Phase 3.3 |
| `promote_learnings.py` | Current-run-validated Tier 2 candidate reporting; no durable writes | stdout | run self-improvement |
| `health.py --dry-run` | Weighted composite 0–10 score | stdout | run Phase 4.5 |
| `retro.py` | Weekly retrospective (git + receipt-verified closes + learnings) | stdout; `--save` writes `doc/harness/retros/<date>.md` | run self-improvement auto-trigger |

Health is activated via the manifest optional key `health_components` and falls
back to `test_command` when no components are declared.

## 11. Tiered Learning

Every skill logs discoveries. Three tiers:

```
CLAUDE.md                    # Tier 1: loaded every session. Key facts only.
doc/harness/patterns/*.md    # Tier 2: detailed patterns. Read when relevant.
doc/harness/learnings.jsonl  # Tier 3: append-only raw signals.
```

**All skills write to Tier 3.** Automatic candidate reporting requires a valid signal
from the just-closed receipt-verified task/run. A key may promote after valid
occurrences from 2+ distinct receipt-verified closed task/runs; duplicate rows
from one run count once, and historical backlog alone cannot trigger reporting.
The reporting pass never rewrites or prunes the raw ledger and never changes
Tier 2 patterns. Apply a reported candidate only in a separately reviewed
Harness task. When a Tier 2 doc is referenced in 2+ tasks, promote the key fact
to Tier 1 (CLAUDE.md).

**Tier 1 entries are one-liners.** Details stay in pattern docs.

Example:
```
# Tier 3 (learnings.jsonl)
{"ts":"2026-09-01T00:00:00Z","type":"operational","key":"test-command","insight":"bun test, not npm test","task":"TASK__001","task_run_id":"01a05a20-fb11-7911-bffb-3de43a13c8fb"}

# Tier 2 (doc/harness/patterns/testing.md)
## Test command is bun test
This project uses Bun. All test commands use `bun test`.

# Tier 1 (CLAUDE.md)
## Testing
Test command: `bun test` (Bun runtime)
```

**When to log:** Any discovery that would save 5+ minutes in a future session.
**What to log:** Build quirks, env var requirements, ordering constraints, port numbers, framework specifics, wrong manifest fields.
**What NOT to log:** Code patterns (read from files), git history (read from git), task-specific details (in task dir).

## 12. Environment variables

| Variable | Effect | Semantics |
|----------|--------|-----------|
| `HARNESS_DISABLE_SCOPE_LOCK` | Bypass PROGRESS.md forbidden_paths gate once | one-shot (cleared after one bypass) |
| `HARNESS_SKIP_PREWRITE` | Bypass `prewrite_gate.py` for one tool call (logs `gate-bypass` to learnings.jsonl) | one-shot (per invocation) |
| `HARNESS_DISABLE_RETRO` | Skip auto-retro post-close trigger | session-wide while set |
| `HARNESS_DISABLE_HYGIENE` | Skip Tier-3 hygiene audit post-close | session-wide while set |
| `HARNESS_SKIP_INTERVIEW` | Setup skill auto-accepts defaults | session-wide while set |
| `HARNESS_SPAWNED` | Orchestrator-spawned session: auto-resolve prompts | session-wide while set |
