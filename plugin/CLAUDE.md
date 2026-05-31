# harness runtime rules

Lightweight execution harness for Claude Code.
7-field TASK_STATE + on-the-fly routing + artifact-provenance.
Self-contained — no plugin-legacy dependency.

Harness rules apply to any caller — the main Claude session, a sub-skill,
or an MCP client — whenever a repo-mutating task runs through the
canonical loop. There is no separate "harness agent" that owns the
workflow; skills and MCP tools are the sole runtime surface.

## 1. Canonical Loop

Every repo-mutating task:
```
plan → develop → verify → close
```
No step skipped. Smallest coherent diff per step.

## 2. MCP tools

**Core (task driver — main session or run skill):**
- `task_start` — create/resume task, return fresh context
- `task_context` — refresh task state (only when needed)
- `task_verify` — sync changed paths + check verification; optionally reconcile ACs from QA PASS evidence
- `task_close` — gate: runtime verdict PASS → close
- `task_blocked` — park unfinished work on a real environment blocker; writes BLOCKED.md and clears this session's active marker

**Artifact writes (role-owned):**
- `write_plan_artifact` → PLAN.md / PLAN.meta.json / CHECKS.yaml / AUDIT_TRAIL.md (plan-skill)
- `write_critic_qa` → CRITIC__qa.md + runtime_verdict evidence ledger (qa-* agents)
- `write_critic_ux` → CRITIC__ux.md (ux-* agents; no runtime_verdict mutation)
- `write_critic_document` → CRITIC__document.md (critic-document agent; fires at close when durable docs change OR `<task_dir>/USER_FEEDBACK.jsonl` is non-empty, per C-101)
- `write_req_doc` → doc/<area>/REQ__*.md scaffold for observable behavior (accepts optional `status: accepted|candidate`; critic-document retrospective writes use `candidate`)
- `write_handoff` → HANDOFF.md (develop coordinator or dedicated developer role)
- `write_doc_sync` → DOC_SYNC.md (develop coordinator)

Provenance = artifact existence. No counters.

## 3. TASK_STATE (7 fields only)

```yaml
task_id: TASK__xxx
status: created|planning|implementing|verifying|blocked|closed
runtime_verdict: pending|PASS|FAIL|BLOCKED_ENV
touched_paths: []
plan_session_state: closed|context_open|write_open
closed_at: null
updated: 2026-04-14T00:00:00Z
```

Routing is computed on-the-fly from manifest + artifacts. Never stored in TASK_STATE.

## 4. Plan-first rule

Do not mutate source before PLAN.md exists.
Short approvals only authorize the last explicit transition proposed.

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

멈추려면 `Agent(subagent_type='harness:stop-judge')` 호출. Stop-judge 가 CHECKS+transcript+work 보고 OK/NO 의미 판단을 내림. Tool 호출 카운트, prompt rule 단독, 텍스트 키워드 검사 같은 mechanical/prose-only 게이트는 사용 안 함 — stop-judge 의 의미 판단이 유일한 권한자. PASS 경로는 기존 task_verify+task_close.

## 5. Artifact ownership

| Artifact | Owner |
|----------|-------|
| PLAN.md / PLAN.meta.json / AUDIT_TRAIL.md | plan-skill via `write_plan_artifact` MCP |
| CHECKS.yaml | plan-skill (create) + update_checks.py CLI (develop/qa updates) |
| source + HANDOFF.md + DOC_SYNC.md + distilled change doc | developer |
| CRITIC__qa.md | qa-browser / qa-api / qa-cli / qa-desktop |
| CRITIC__ux.md | ux-browser / ux-api / ux-cli / ux-desktop |

Do not write another role's artifact. Prewrite gate enforces this.

## 6. Auto-routing

**Default for repo-mutating intent: `Skill(harness:run)`.** Never AskUserQuestion
to choose between plan / run / develop flows — narrower flows fire only when
the user explicitly names them in their own prompt.

| Intent | Route to |
|--------|----------|
| Set up harness | `Skill(setup)` |
| Pre-planning / scope-sharpening (product framing before a task) | `Skill(harness:setup)` (fills the office-hours role; no separate office-hours skill) |
| Any repo-mutating intent — new feature, fix, refactor, behavior change (default) | `Skill(harness:run)` |
| User explicitly says "plan only" / "just plan" | `Skill(harness:run)`; stop after plan if the user explicitly asks not to implement |
| User explicitly says "implement PLAN.md" / "develop only" | `Skill(harness:run)` resume/develop path |
| Multi-component or API↔frontend change in one task | `Skill(harness:run)` (develop Phase 3.0 auto-fanout) |
| CEO / Architecture / Design / DX review | `Skill(harness:run)` plan phase; review lenses are internal sub-skills |
| Contract drift / "CLAUDE.md 정리" / "규약 정비" / post-upgrade cleanup | Use the close-time self-improvement flow: run hygiene after task close, then schedule cleanup as a separate follow-up task when needed |
| Explanation | Direct answer |

## 7. Verification

`task_verify` syncs paths and checks verification state.
Do not claim success from static inspection when runtime verification is required.

## 8. Finish cleanly

Runtime verdict must be PASS before close.
Use `task_close`. If blocked, fix the stated gate.

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

On every SessionStart (and whenever explicitly run), the hook
`scripts/note_freshness.py` scans `git diff HEAD~1 HEAD`. If any changed path
matches a note's `invalidated_by_paths`, that note's `freshness` flips from
`current` to `suspect` and `freshness_updated` is stamped.

Writer-role agents must verify `freshness: current` before citing a note as
authoritative. `suspect` notes are still readable but require re-validation
against current source before trust. Use `--paths` arg to invalidate against
an explicit file list when git history isn't the right source.

## 8b. Acceptance Ledger (CHECKS.yaml)

CHECKS.yaml is the per-task AC ledger. Plan-skill creates each AC with
`status: open`. The develop skill promotes ACs to
`implemented_candidate` after per-AC tests pass (Phase 3), then the
verification gate (Phase 7) promotes them to `passed` — or reopens them
to `failed` (auto-incrementing `reopen_count`). Only `passed` or
`deferred` ACs satisfy the close gate.

Writes go through `scripts/update_checks.py` only. Never edit CHECKS.yaml by
hand — the prewrite gate rejects direct writes.

## 8c. Verification delegation

Main session NEVER calls browser MCP tools (`mcp__chrome-devtools__*`)
inline. Browser-driving calls (`take_snapshot`, `take_screenshot`,
`evaluate_script`, `navigate_page`, `click`, `fill`, ...) MUST be
delegated to the `harness:qa-browser` subagent. The browser MCP surface
is the actual context-bloat source — DOM snapshots, screenshots, and
evaluate output add thousands of structured tokens per call.

Why: qa-browser runs browser verification in an isolated context and
writes structured findings to `CRITIC__qa.md`; the orchestrator reads
only the verdict (`PASS` / `FAIL` / `BLOCKED_ENV`).

Allowed inline:
- **Bash test runners** (`pytest`, `npm test`, `pnpm test`, `yarn test`, `bun test`, `vitest`, `jest`, `mocha`, `cargo test`, `go test`, `mvn test`, `gradle test`, `rspec`, `phpunit`, `rake test`) — single PASS/FAIL lines do not bloat context; inline use is legitimate. For heavy full-suite runs, spawning `harness:qa-cli` (or `qa-api` / `qa-desktop` per project) keeps the main lane clean as a convention, not a gate.
- Lint / format / typecheck (`tsc --noEmit`, `mypy`, `ruff`, `eslint`, `prettier`)
- Build / compile (`npm run build`, `cargo build`, `go build`)
- Read-only inspection (`grep`, `find`, `git status`, `git diff`)
- Ad-hoc HTTP / DB probes (`curl`, `wget`, `httpie`, `psql -c`, `mysql -e`, `alembic`) — too many legitimate uses (API exploration, schema inspection, debugging) for the gate to block

Enforced by: `plugin/scripts/qa_delegation_gate.py` (PreToolUse, no
matcher — the script self-filters by `tool_name` prefix). The gate
allows delegated `harness:qa-browser` calls, then emits a deny envelope
for non-delegated callers whose reason surfaces in system-reminder so
the model self-redirects to spawn `harness:qa-browser`. Detection
prefers explicit agent fields when the runtime exposes them and falls
back to a capped `transcript_path` prologue check for the qa-browser
agent prompt. Bypass: `HARNESS_SKIP_QA_DELEGATION=1` one-shot.

History: prior to 2026-05-14 the gate also blocked Bash test runners.
User feedback narrowed it to MCP-only after false-positive blocks on
legitimate inline `pytest` / `vitest` / `pnpm test` use. The Bash test
runner block was the wrong knob — Bash test output is bounded, browser
MCP output is unbounded.

## 9. Iron Law

The Iron Law has two parallel clauses, both enforced by `update_checks.py`. ACs
cannot be promoted to `implemented_candidate` or `passed` until the artefact
appropriate to the AC's kind is supplied.

### 9a. Bugfix ACs require `root_cause`

`kind: bugfix` ACs cannot be promoted unless `root_cause` is set:

```bash
python3 scripts/update_checks.py --task-dir TASK_DIR --ac AC-001 \
  --status implemented_candidate --root-cause "off-by-one in loop bound"
```

Without `--root-cause`, the command exits 1 with an Iron Law violation message.
Once set, `root_cause` persists across subsequent transitions.

### 9b. Feature / functional ACs require test evidence

`kind in {feature, functional}` ACs cannot be promoted unless `--test-evidence`
points to a real regression test file. The path is validated at gate time:
must exist, must not be a symlink, must resolve inside `repo_root`.

```bash
python3 scripts/update_checks.py --task-dir TASK_DIR --ac AC-001 \
  --status implemented_candidate \
  --test-evidence tests/regression/task_xx/test_ac_001__behavior.py
```

Bypass with a documented reason (logged to `doc/harness/learnings.jsonl` as
`type=test-evidence-bypass`; reason capped at 400 chars):

```bash
python3 scripts/update_checks.py --task-dir TASK_DIR --ac AC-007 \
  --status implemented_candidate \
  --no-test-required "narration-only AC, no behavior to test"
```

**Skip allowlist:** `kind in {bugfix, doc, verification}` skip the
test-evidence rule. Bugfix has its own gate (9a); doc / verification produce
no functional code. ACs whose `kind:` field is missing default to `unknown`
and skip the gate (preserves backward-compat with legacy CHECKS.yaml that
pre-dates this rule).

The error message includes a `Suggested:` line when exactly one file under
`tests/` matches the AC id (e.g. `test_ac_001__*.py`) — turning the gate
from a bare rejection into a helpful nudge.

## 10. Quality scripts

All scripts under `plugin/scripts/`. Stdlib only (PIL optional for canary).

| Script | Purpose | State file (gitignored) |
|--------|---------|------------------------|
| `health.py` | Weighted composite 0–10 score | `doc/harness/health-history.jsonl` |
| `benchmark.py` | Numeric metrics vs baseline, WARN/REGR thresholds | `doc/harness/benchmark/{baseline.json,history.jsonl}` |
| `audit.py` | Generic categorized audit (CSO-style) | `doc/harness/audits/<category>-history.jsonl` |
| `canary.py` | Visual regression baseline + sha/pixel diff | `doc/harness/visual-baselines/<task-id>/` |
| `search_learnings.py` | Keyword/type/skill/since search over Tier 3 | reads `doc/harness/learnings.jsonl` |
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/<task-id>.md` |
| `inject_checkpoint.py` | Manual resume helper — surface latest checkpoint | reads `doc/harness/checkpoints/` |
| `promote_learnings.py` | Tier 3→2 promotion + stale pruning | `doc/harness/patterns/<topic>.md` |
| `retro.py` | Weekly retrospective (git + learnings + health) | `doc/harness/retros/<date>.md` |
| `hygiene_scan.py` | Close-time hygiene scan (contract drift + doc classification) | `doc/harness/.hygiene-last-run` + `doc/harness/.hygiene-session-count` |

All activated via manifest optional keys: `health_components`, `benchmark_components`,
`audit_categories`. Health falls back to `test_command` when no components declared.
Benchmark and audit are inactive until their manifest keys exist.

## 11. Tiered Learning

Every skill logs discoveries. Three tiers:

```
CLAUDE.md                    # Tier 1: loaded every session. Key facts only.
doc/harness/patterns/*.md    # Tier 2: detailed patterns. Read when relevant.
doc/harness/learnings.jsonl  # Tier 3: raw signals. Session-specific, transient.
```

**All skills write to Tier 3.** When a signal repeats 2+ times, promote to Tier 2 doc. When a Tier 2 doc is referenced in 2+ tasks, promote the key fact to Tier 1 (CLAUDE.md).

**Tier 1 entries are one-liners.** Details stay in pattern docs.

Example:
```
# Tier 3 (learnings.jsonl)
{"key":"test-command","insight":"bun test, not npm test","task":"TASK__001"}

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
| `HARNESS_SKIP_MCP_GUARD` | Bypass `mcp_bash_guard.py` for one Bash call (logs `gate-bypass` to learnings.jsonl) | one-shot (per invocation) |
| `HARNESS_DISABLE_RETRO` | Skip auto-retro post-close trigger | session-wide while set |
| `HARNESS_DISABLE_HYGIENE` | Skip Tier-3 hygiene audit post-close | session-wide while set |
| `HARNESS_SKIP_INTERVIEW` | Setup skill auto-accepts defaults | session-wide while set |
| `HARNESS_SPAWNED` | Orchestrator-spawned session: auto-resolve prompts | session-wide while set |
