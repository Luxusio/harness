---
name: develop
description: Implement PLAN.md. Orchestrates per-AC implementation, quality audit, verification gate, and HANDOFF generation. Uses parallel agents for quality phases and haiku for mechanical work. Detail lives in sub-files — this file is the orchestration layer.
argument-hint: <task-id>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__harness__task_start, mcp__harness__task_context, mcp__harness__write_handoff, mcp__harness__write_doc_sync, mcp__chrome-devtools__navigate_page, mcp__chrome-devtools__take_snapshot, mcp__chrome-devtools__take_screenshot, mcp__chrome-devtools__evaluate_script, mcp__chrome-devtools__wait_for, mcp__chrome-devtools__list_pages, mcp__chrome-devtools__new_page, mcp__chrome-devtools__select_page, mcp__chrome-devtools__emulate, mcp__chrome-devtools__click, mcp__chrome-devtools__fill, mcp__chrome-devtools__press_key, mcp__chrome-devtools__type_text, mcp__chrome-devtools__hover, mcp__chrome-devtools__list_network_requests, mcp__chrome-devtools__performance_start_trace, mcp__chrome-devtools__performance_stop_trace, mcp__chrome-devtools__lighthouse_audit
---

Implement the plan for a harness2 task. Reads PLAN.md, implements changes, verifies completeness, writes HANDOFF.md.

## Voice

Direct, terse. Status updates, not narration.

## Error Philosophy

MCP does not tolerate mid-task stops. **Never halt with a bare BLOCKED.** Use `AskUserQuestion` with options; user decides. Errors are consumed by the running agent, not humans.

## Model Routing

Route work to the cheapest sufficient model. Inline below; full rationale in sub-files.

| Work | Model |
|------|-------|
| Implementation (Phase 3) | inherit |
| Confidence ratings (4.6) | inherit |
| Adversarial review (4.7) | **cross-model** — Opus→Sonnet, Sonnet→Haiku (different blind spots) |
| Everything else mechanical | haiku (test-coverage tracing, edge-case scan, completion audit, runtime smoke, visual smoke) |

## Flow

Phases run in strict order; each phase must complete before the next. Sub-files are lazy-loaded — do NOT pre-read them, load each only in the phase that needs it. Every phase is idempotent on re-run; check PROGRESS.md + `audit/` to resume instead of restarting from Phase 0.

**Timeline logging:** append phase transitions to `<task_dir>/timeline.jsonl` as append-only JSON lines with keys `ts, phase, event, detail`. Events: `phase_start, phase_end, ac_start, ac_done, agent_spawn, agent_done, fix_cycle, blocked, resumed, finding`.

**Graceful degradation:** missing tool or phase prerequisite → skip cleanly, log reason, do NOT install missing tools. Skipped-phase table:

| Missing | Phases skipped |
|---------|----------------|
| Linter / build / test framework / coverage tool | 3.7 / 3.8 / 4.5 / 4.9 |
| `browser_qa_supported: false` or Chrome MCP missing | 3 visual, 3.9, 4 Agent D, 7 browser debug |
| Dev server unreachable | 3.9 |
| No QA_KNOWLEDGE.yaml / learnings.jsonl | 0 / 1 (first run creates them) |

### Phase 0: Pre-flight

Verify `doc/harness/manifest.yaml` and `TASK_STATE.yaml` parse and `status` is one of: created, planning, implementing, verifying, documenting, closed. No other task holds write focus. On failure, `AskUserQuestion` with setup-skill / task-id / continue-anyway options.

**Context Recovery:** tail `doc/harness/timeline.jsonl` for last 5 completed skills and 3 newest tasks. If an in-progress task matches the current `task_id`, log "resuming from prior session".

**Health baseline snapshot:** capture composite health score for Phase 8 delta. Best-effort — skip cleanly.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py --dry-run > "<task_dir>/audit/health-baseline.txt" 2>&1 || true
```

Reads `health_components` from manifest (falls back to `test_command`). Output includes per-component PASS/FAIL + composite 0–10 score. `--dry-run` prevents appending to project-level history at this stage.

### Phase 1: Load plan

Read `doc/harness/tasks/<task_id>/`:
1. `PLAN.md`, `REQUEST.md` (if present), `TASK_STATE.yaml`, `test-plan.md` (if eng review produced one).
2. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
3. **Resume check:** `PROGRESS.md` → skip ACs listed in `completed_acs`. For each completed AC, compare target-file mtimes against `PROGRESS.md` mtime; files modified post-PROGRESS → mark "needs re-verification", do not blindly skip.
4. **Learnings bootstrap:** `head -20 doc/harness/learnings.jsonl` and `ls doc/harness/patterns/*.md`. If PLAN.md absent, `AskUserQuestion` (run plan skill / check task_id / abort).

### Phase 2: Read + Search Before Building

Read target files and dependencies from PLAN.md. For each AC, before implementing:
1. Grep for existing solutions — function names, utilities, patterns.
2. Check framework/stdlib built-ins.
3. Follow existing codebase conventions, not invented ones.
4. Only build new when nothing fits — extend over duplicate.

**Eureka check:** if search reveals PLAN.md's approach is suboptimal (reinventing, wrong assumption), flag as `EUREKA: AC-NNN — <discovery>` in HANDOFF under "Plan Challenges". Do NOT silently override. Persist as `type:"eureka"` in `learnings.jsonl`.

**Baseline screenshot (browser projects):** see `browser-verification.md` → "Phase 2: Baseline Screenshot".

### Phase 3.0: AC Dependency Analysis

Classify ACs as SEQUENTIAL (shared files or data dependency) or PARALLEL (disjoint). Build a dependency matrix. For parallel batches, spawn executor agents concurrently; collect results before proceeding to dependent ACs. If all sequential: skip parallelization.

### Phase 3.1: Scope Lock

Declare allowed / test / forbidden paths in PROGRESS.md. Before each file edit:
- allowed → proceed. test → proceed. forbidden → BLOCK + escalate. unlisted → WARN, auto-add to allowed with note.

### Phase 3: Implement

1. **One AC at a time**, in order. Skip ACs in `completed_acs`.
2. **Follow existing patterns.** Smallest coherent diff. No speculative features.

**PROGRESS.md after each AC:**

```yaml
task_id: <task_id>
phase: 3
completed_acs:
  - id: AC-001
    status: done
    tests: passed
    completeness: 9            # 0-10 — see rubric below
    deferred_edges: []         # edge cases consciously skipped
current_ac: <next or "done">
partial_ac: null               # or { id: AC-003, note: "edits done, regression test pending" }
decisions:
  - { choice: "...", reason: "...", ac: AC-001 }
attempts:
  - { ac: AC-002, tried: "...", failed_because: "...", resolved_with: "..." }
notes:
  - "<file:line> — <observation>"
updated: <ISO timestamp>
```

**AC Completeness rubric (0-10):** covers how much of the AC's surface area was addressed (NOT confidence that it works — that's Phase 4.6).

| Score | Meaning |
|-------|---------|
| 10 | Happy path + all edge cases + negative paths + regression tests |
| 8-9 | Happy path + common edges + regression test. Rare edges documented |
| 6-7 | Happy path + main branches. Some edges deferred with justification |
| 4-5 | Happy path only. Significant surface skipped |
| ≤3  | Partial — AC should not be marked done |

Any AC scoring ≤7 MUST list `deferred_edges`. ≤5 requires explicit justification in HANDOFF (MVP scope, user-deferred, etc.).

**Acceptance Ledger update (after each AC):** once the AC's code is in and per-AC tests pass, mark it `implemented_candidate` in CHECKS.yaml. Only Phase 7 promotes to `passed`. Never hand-edit CHECKS.yaml.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --ac AC-00X --status implemented_candidate \
  --evidence "<file:line | test name>"
```

**Per-AC test run:** `git diff --name-only HEAD~1` → for each changed source, find test files that import/reference it (mirror path or import search) → run only those. If no tests exist for changed module, write one (Phase 3.5 rule). If PLAN.md specifies per-AC verify commands, prefer those.

Per-AC test failures → fix immediately. These are free; only Phase 7 full-suite failures count toward the 3-cycle limit.

**Per-AC visual verification** (browser projects only): see `browser-verification.md` → "Per-AC Visual Verification" and "Per-AC Interaction Testing".

### Phase 3.3: Auto-checkpoint (post all ACs)

After all ACs reach `implemented_candidate`, snapshot task state for session resume:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 3 complete — all ACs at implemented_candidate"
```

### Phase 3.4–3.6: Per-AC Quality Gate

Runs continuously during Phase 3.

- **3.4 Test framework bootstrap** — if project has no framework and no `doc/harness/.no-test-bootstrap` opt-out marker, offer minimal setup (JS/TS: vitest or bun:test; Python: pytest; Go/Rust: built-in). Log bootstrap to `learnings.jsonl` type `test-bootstrap`. If user declines, create opt-out marker.
- **3.5 Regression rule** — if the diff modifies existing behavior and no test covers the changed path, write a regression test immediately. Commit separately: `test: regression test for <what>`.
- **3.6 Fix-first pattern** — see `fix-first-pattern.md`. Classify AUTO-FIX (dead code, magic numbers, stale comments, missing guards) and ASK (API design, architecture, security, DRY extractions). Auto-fix immediately; flag ASK in HANDOFF "Judgment Items". The **3-attempt escalation rule** also lives in this sub-file and applies to every fix loop (per-AC, Phase 7, browser debug).

### Phase 3.7–3.9: Post-implementation health

After all ACs done. Each runs only if prerequisite exists.

- **3.7 Lint & Format** — run linter and formatter on `git diff --name-only` only. `--fix` where safe. Re-run per-AC tests after. Skip if none configured.
- **3.8 Build check** — compile / typecheck the diff (or full project). Build failures are always T1 (our code). Fix immediately.
- **3.9 Runtime smoke** — see `runtime-smoke.md`. Project-type-specific (browser / API / CLI).

### Phase 4: Plan Completion Audit (haiku)

Haiku agent cross-references every AC against `git diff --stat` and classifies each as DONE / PARTIAL / NOT DONE / CHANGED + category (CODE / TEST / MIGRATION / CONFIG / DOCS). Be conservative with DONE (file touched ≠ AC done); be generous with CHANGED (goal met by different means).

For PARTIAL / NOT DONE, classify cause: scope-cut / context-exhaustion / misunderstood / blocked / forgotten / evolved. Fix forgotten and misunderstood immediately; log scope-cut + blocked; mark evolved as CHANGED with new approach in HANDOFF.

### Phase 4.5–4.8: Quality Audit Pipeline

Read `quality-audit-pipeline.md` in full. Runs parallel agents (test-coverage haiku, confidence-ratings inherit, adversarial cross-model, visual-smoke haiku browser-only) + conditional specialists (security / perf / migration / LLM-trust) based on diff scope, then a quality synthesis agent that deduplicates and scores.

**Diff scope detection** (routes specialists):
```bash
git diff --name-only | while read f; do
  case "$f" in
    *.tsx|*.jsx|*.css|*.scss|*.html|*.vue|*.svelte) echo "SCOPE_FRONTEND=true" ;;
    *auth*|*session*|*token*|*password*|*permission*|*guard*) echo "SCOPE_AUTH=true" ;;
    *migration*|*schema*|*db/*|*migrate*) echo "SCOPE_MIGRATIONS=true" ;;
    *api*|*endpoint*|*graphql*|*rest*|*openapi*) echo "SCOPE_API=true" ;;
  esac
done | sort -u
```

**Adaptive gating:** if a specialist has been dispatched 3+ times with 0 findings, auto-skip. Security and migration are never gated (insurance). Only gate performance.

**Red Team (conditional):** spawned only when diff ≥ 200 lines or any specialist reported critical. Job: find what the first pass MISSED.

**Phase 4.85 Test Plan Artifact** — extract coverage diagram from audit into `doc/harness/test-plans/<task_id>-test-plan.md`. Mechanical — no re-analysis.

**Phase 4.9 Coverage Gate** — if manifest declares `coverage_minimum` / `coverage_target`, enforce. Below minimum = BLOCK (write tests); below target = WARN (log in HANDOFF). 3 fix cycles max; on exhaustion `AskUserQuestion` (continue / lower threshold / defer).

### Phase 5: Scope Drift Detection

`git diff --name-only` — each file is:
- In scope → proceed.
- Related but unlisted → acceptable, note in HANDOFF.
- Unrelated → revert. Belongs in a separate task.
- Missing from plan but necessary → note as "unplanned-but-necessary".

**SCOPE CREEP signals:** unrelated changes, "while I was in there" edits, new features not in PLAN, reformatted distant modules.
**MISSING REQUIREMENTS:** PLAN.md requirements not addressed by any change; partial implementations (model exists but controller missing); test coverage gaps.
**Documentation staleness:** if changed file has a corresponding doc (README section, API doc, inline docblock), flag stale under "Documentation Debt" — writer skill fixes, not here.

### Phase 6: Bisectable Commits

Split into coherent commits in this order:

| Order | Layer | Examples |
|-------|-------|----------|
| 1 | Infrastructure | Config, build, deps, CI |
| 2 | Models / Services / Data | Schema, types, data layer, business logic |
| 3 | Controllers / Views / API | Routes, UI components, endpoints |
| 4 | Tests | Test additions (separate from impl) |
| 5 | Docs / Metadata | VERSION, CHANGELOG, README, DOC_SYNC |

Each commit must leave the codebase working. Bisect stops at infra layer, not mid-feature.

### Phase 6.5 + 6.7: Verification Gate + Trend

- **6.5 IRON LAW** — PASS = PASS. No stale PASS. No unverified claim. Runtime verdict must be fresh after last file change.
- **6.7 Quality score trend** — append current score to `doc/harness/quality-trend.jsonl` with task_id + timestamp. Enables trend analysis.

### Phase 7: Verification Gate

Read `verification-gate.md` in full. Runs test commands from PLAN.md, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING), triages with hypothesis-driven debugging, enforces the 3-cycle limit with investigate-skill escalation on cycle 3.

**Also implements:**
- **Transience filter** — a failure must reproduce on 2 consecutive runs to count as `failed`. Single-run failures are logged as `transient` in `learnings.jsonl` and not counted toward the 3-cycle limit.
- **Severity × confidence close gate** — after synthesis, block close on:
  - `critical` AND confidence ≥ 7
  - `high` AND confidence ≥ 8
  Lower severities flow into HANDOFF as deferred — do not block close.
- **Acceptance Ledger promotion** — on gate pass, `update_checks.py --status passed`. On gate fail, `--status failed` (auto-increments `reopen_count`), loop back to fix cycle. Close gate requires every AC to be `passed` or `deferred`.

### Phase 7.5: Auto-checkpoint (post verify gate)

After Phase 7 completes (pass or fail), snapshot for mid-task resume:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_checkpoint.py \
  --task-dir doc/harness/tasks/<task_id>/ \
  --note "Phase 7 done — runtime_verdict=$(grep runtime_verdict <task_dir>/TASK_STATE.yaml | awk '{print $2}')"
```

### Phase 7.6: Health score capture

Run health score and append to project-level history (Phase 8 uses the delta against Phase 0 baseline):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/health.py > "<task_dir>/audit/health-after.txt" 2>&1 || true
```

### Phase 8: Write HANDOFF

**Concreteness standard:** every entry must locate without searching — name file, function, line. "Fixed auth bug" is not acceptable; `auth.ts:47 — added null check on session.token` is.

Call `mcp__harness__write_handoff` with:

1. Summary (one sentence per AC)
2. Files changed (every file + one-line description)
3. Verification results per AC
4. Scope notes (out-of-plan changes with justification)
5. Do Not Regress (caveats, fragile patterns)
6. Confidence Ratings table from Phase 4.6 (highlight ≤6)
7. Adversarial Findings table from Phase 4.7 (critical/high fixed, lower deferred)
8. Near-Zero Cost check (Phase 4.8 fixed + deferred)
9. Test Failure Triage (Phase 7)
10. Test Results per AC + fix history
11. Judgment Items (Phase 3.6 ASK-classified)
12. Debugging Notes (Phase 7 debug reports — Symptom / Root cause / Fix / Evidence / Regression / Related / Status)
13. Visual Evidence (AC → screenshot path → console errors → viewport)
14. Execution Metrics (phase timing + fix loop counts)
15. Quality Score (weighted)
16. Health Delta — recompute metrics from Phase 0 baseline:

    ```
    | Metric | Before | After | Δ |
    | Tests | 42 | 46 | +4 ↑ |
    | Type errors | 12 | 8 | -4 ↓ |
    | Lint issues | 3 | 0 | -3 ↓ |
    ```

    Log `type:"health-delta"` to `learnings.jsonl` with before/after fields.

**Quality Score:**
```
score = (ac_completion × 0.40) + (test_coverage × 0.30)
      + (adversarial_clean × 0.20) + (scope_discipline × 0.10)
```
- `ac_completion` = (done / total) × 10. Deferred = 0.5.
- `test_coverage` = (tested paths / total changed paths) × 10. No framework → 5.
- `adversarial_clean` = max(0, 10 - (crit × 3 + high × 1.5 + med × 0.5)). Fixed at 0.25 weight.
- `scope_discipline` = 10 / 7 / 4 / 0 (none / auto-added / justified / unjustified).

**Cleanup:** delete PROGRESS.md from task_dir — HANDOFF is the permanent record.

### Phase 8.5: Reflect and Log

Not the signals table — open-ended friction reflection. Ask:
1. What took longer than expected?
2. What surprised you?
3. What would you do differently?
4. Were prior learnings helpful? Stale? Missing?
5. **Operational friction sweep (5-minute-save test):** scan for operational surprises that would save 5+ minutes in a future task if known upfront. Log at least one `operational` learning per session if any apply:
   - command failed or had non-obvious flag
   - tool had to run in specific order
   - undocumented env var / port
   - framework quirk that wasted a cycle
   Log each as `type:"operational"` with a concrete one-line instruction for the next session.

6. **Calibration metrics** — compare Phase 4.6 confidence scores vs Phase 7 actual results:

   ```
   File | Rated | Actual | Verdict | Lesson
   billing.ts | 5/10 | FAIL | overconfident | N+1 hidden by mock
   report.ts | 4/10 | PASS | underconfident | async looked risky, was sequential
   ```

   Log each event as `type:"confidence-calibration"`. Summary as `type:"calibration-summary"` with `overconfidence_rate` / `underconfidence_rate` (targets: <20% / <30%).

Log each insight to `doc/harness/learnings.jsonl`:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'","type":"pattern|pitfall|architecture|tool|operational","source":"develop-reflect","key":"FRICTION_KEY","insight":"<one-line>","confidence":N,"files":["<path>"],"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Include `files` so staleness can be detected if those files are later deleted.

**Learning quality feedback:** verify loaded learnings. File existence (`test -f` referenced paths); contradiction (same `key` with different `insight` — newer wins); recency (>30 days old referencing version-specific flags — flag suspect). Log as `key:"learning-audit"`.

### Phase 8.6: DOC_SYNC

Mechanical. Read HANDOFF.md (changed file list) + `doc/CLAUDE.md` (registered roots). For each file, map to doc root. Call `mcp__harness__write_doc_sync`.

### Phase 8.7: Distilled Change Doc

One-paragraph summary of the task's user-visible behavior change. Lives at `doc/changes/<date>-<slug>.md`. Optional if no user-visible change. Writer skill consumes this for release notes.
