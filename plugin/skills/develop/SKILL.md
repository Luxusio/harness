---
name: develop
description: Implement PLAN.md with plan completion audit, scope drift detection, bisectable commits, verification gate, adversarial self-check, confidence ratings, test failure triage, fix-first pattern, and hypothesis-driven debugging. Uses parallel agents for quality audit phases and haiku for mechanical work.
argument-hint: <task-id>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__harness__task_start, mcp__harness__task_context, mcp__harness__write_handoff, mcp__harness__write_doc_sync
---

Implement the plan for a harness2 task. Reads PLAN.md, implements changes, verifies completeness, writes HANDOFF.md.

## Voice

Direct, terse. Status updates, not narration.

## Error Philosophy

Errors in this skill are consumed by the AI agent running it, not humans.
Every error, BLOCKED condition, or stop must include a **Next step:** directive
telling the agent exactly what to do. Vague stops waste fix cycles.

Bad:  "BLOCKED: PLAN.md missing"
Good: "BLOCKED: PLAN.md missing. Next step: run plan skill to create PLAN.md for this task, or verify task_id is correct."

## Model Routing (cost optimization)

Not all phases need the same intelligence. Route work to the cheapest sufficient model:

| Work type | Model | Rationale |
|-----------|-------|-----------|
| Implementation (Phase 3) | inherit | Core coding — needs judgment |
| Test coverage tracing (4.5) | **haiku** | Mechanical: grep, read, build diagram |
| Confidence ratings (4.6) | **inherit** | Risk assessment needs same capability as implementation |
| Adversarial review (4.7) | **cross-model** | Different model catches implementer's blind spots |
| Edge case scan (4.8) | **haiku** | Pattern matching: null guards, empty checks |
| Completion audit (Phase 4) | **haiku** | Cross-reference ACs against diff |
| Debugging (Phase 7) | inherit | Hypothesis formation needs reasoning |

When spawning agents, use `model="haiku"` for mechanical work. Quality gates (confidence)
inherit the parent model. **Adversarial review (4.7) uses cross-model:** if implementation
ran on Opus, adversarial runs on Sonnet. If implementation ran on Sonnet, adversarial runs
on Haiku. The same model that wrote the code shares its blind spots — a different model
brings different pattern recognition and is more likely to catch what the implementer missed.

## Flow

Execute phases in strict order. Each phase must complete before the next begins.

**Sub-file loading is lazy:** The sub-files under `plugin/skills/develop/` (fix-first-pattern.md,
test-coverage-audit.md, adversarial-self-check.md, confidence-rated-changes.md,
near-zero-marginal-cost.md, test-failure-triage.md, hypothesis-driven-debugging.md) are NOT
all loaded at the start. Each file is read only in the phase that needs it. Do NOT pre-read
sub-files — load them on demand to preserve context window for implementation.

**Idempotency guarantee:** Every phase is safe to re-run after a crash. On crash recovery,
check PROGRESS.md and `audit/` directory to determine where to resume — do NOT restart from
Phase 0. Phase safety: 0-2 (pure reads, always safe), 3 (PROGRESS.md tracks done ACs),
3.5-3.8 (lint/build/test — idempotent), 4-4.8 (read-only analysis), 5-6 (verify git state
first), 7 (re-run verification), 8 (HANDOFF overwrite is intentional).

**Timeline logging (continuous):** Throughout execution, append phase transitions to
`<task_dir>/timeline.jsonl`. This survives session crashes and enables post-mortem debugging.

```bash
_tl_ts() { date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown"; }
_tl_log() { echo '{"ts":"'"$(_tl_ts)"'","phase":"'"$1"'","event":"'"$2"'","detail":"'"$3"'"}' >> <task_dir>/timeline.jsonl 2>/dev/null || true; }
```

Log these events as they happen:
- `phase_start` / `phase_end` — each phase transition
- `ac_start` / `ac_done` — AC implementation lifecycle
- `agent_spawn` / `agent_done` — sub-agent lifecycle
- `fix_cycle` — Phase 7 fix attempt
- `blocked` / `resumed` — stop/restart events
- `finding` — critical/high adversarial finding

Timeline entries are append-only. Never edit or delete them.

**Graceful degradation:** If a tool or phase prerequisite is missing, skip cleanly
rather than failing. Never install missing tools — just note and continue:

| Missing | Phase affected | Action |
|---------|---------------|--------|
| No linter configured | 3.7 | Skip. Log: "No linter — skipped lint phase" |
| No build step | 3.8 | Skip. Log: "Interpreted language — skipped build phase" |
| No test framework | 4.5 | Skip test generation. Note in HANDOFF. |
| No PLAN.md verification commands | 7 | Auto-detect test command from manifest/project config |
| No test files for changed module | 3 (per-AC) | Write new test (Phase 3.5 regression rule) |
| No QA_KNOWLEDGE.yaml | 0 | Skip. First QA session will create it. |
| No learnings.jsonl | 1 | Skip. First session will create it. |
| python3 unavailable for YAML parse | 0 | Use grep/cat fallback for TASK_STATE check |

### Phase 0: Pre-flight check

Before loading any plan, verify the environment is healthy:

```bash
# Record phase timing baseline
_PHASE_START=$(date +%s)

# Verify harness is initialized
cat doc/harness/manifest.yaml 2>/dev/null || echo "MISSING: manifest.yaml"

# Verify task state file parseable
python3 -c "import yaml; yaml.safe_load(open('doc/harness/tasks/${task_id}/TASK_STATE.yaml'))" 2>/dev/null || echo "MISSING: TASK_STATE.yaml"
```

Check:
1. `doc/harness/manifest.yaml` exists and is parseable.
2. Task directory `doc/harness/tasks/<task_id>/` exists.
3. `TASK_STATE.yaml` has a valid `status` field (one of: created, planning, implementing, verifying, documenting, closed).
4. No other task holds write focus (check manifest `active_task` if present).

If any check fails: stop with a diagnostic table, not a bare "BLOCKED".

```
Pre-flight Failed:
| Check | Status | Detail |
|-------|--------|--------|
| manifest.yaml | MISSING | Next step: run setup skill to initialize harness |
| TASK_STATE | INVALID | Next step: check task_id spelling or run task_start |
```

If all pass: proceed to Phase 1. Log: "Pre-flight: all checks passed (<N>s)".

### Phase 1: Load plan

Read the task directory and PLAN.md:

```
task_dir = doc/harness/tasks/<task_id>/
```

1. Read PLAN.md in full.
2. Read REQUEST.md if it exists.
3. Read TASK_STATE.yaml for current status.
4. Extract: objective, scope (in/out), target files, acceptance criteria (AC-001+), verification commands.
5. Read `test-plan.md` if it exists (produced by eng review).
6. **Resume check:** Read `PROGRESS.md` if it exists. Extract `completed_acs` list — these ACs will be skipped in Phase 3. Report: "Resuming from AC <next>".
   - **Staleness detection:** For each completed AC, check if its target files were modified after PROGRESS.md was written:

   ```bash
   # Compare PROGRESS.md mtime against target file mtimes
   _PROG_MTIME=$(stat -c %Y PROGRESS.md 2>/dev/null || stat -f %m PROGRESS.md 2>/dev/null)
   for _FILE in <target files for completed ACs>; do
     _FILE_MTIME=$(stat -c %Y "$_FILE" 2>/dev/null || stat -f %m "$_FILE" 2>/dev/null)
     if [ "$_FILE_MTIME" -gt "$_PROG_MTIME" ] 2>/dev/null; then
       echo "STALE: $_FILE modified after PROGRESS.md — re-verify AC"
     fi
   done
   ```

   If any file was modified post-PROGRESS: mark that AC as "needs re-verification" rather than blindly skipping it. The AC implementation may be partial or corrupted.
7. **Learnings bootstrap:** Read recent learnings to avoid re-discovery:

```bash
head -20 doc/harness/learnings.jsonl 2>/dev/null || true
# Also check Tier 2 pattern docs relevant to this task
ls doc/harness/patterns/*.md 2>/dev/null || true
```

If learnings exist for this project type (build quirks, test commands, env deps), load them before Phase 2. Log: "Loaded N learnings from prior sessions". These inform Phase 2 (knowing test framework upfront) and Phase 7 (known flaky tests, known env requirements).

If PLAN.md is absent: stop, report BLOCKED. Next step: run plan skill to create PLAN.md, or verify task_id is correct.

### Phase 2: Read existing code + Search Before Building

Before writing any code, read every target file and dependency listed in PLAN.md. Understand:
- Current structure and patterns
- Existing tests and their conventions
- Import/export patterns
- Error handling conventions

Map what already exists vs what needs to change. Do NOT start implementing until this read pass completes.

**Search Before Building (mandatory):** For each AC, before implementing:

1. **Grep for existing solutions** — search for function names, utility classes, or patterns that already solve part of the AC. The codebase likely has abstractions you can reuse.
2. **Check Layer 1 (tried-and-true)** — does the project's framework/runtime have a built-in for this? (e.g., framework auth middleware, standard library hashing, existing validation utilities).
3. **Check Layer 2 (current patterns)** — how does the codebase solve similar problems elsewhere? Follow existing conventions, not invented ones.
4. **Only build new when confirmed nothing exists** — if you find an existing utility that covers 80%+ of the need, extend it rather than writing a parallel one.

Log: "Search complete for AC-NNN: found [existing pattern / new code needed]".
Do NOT skip this step — reinventing existing abstractions is the #1 source of unnecessary scope drift.

**Eureka check:** If search reveals that PLAN.md's approach is suboptimal — a better
pattern exists, the plan reinvents something, or first-principles reasoning shows the
plan's assumptions are wrong — flag it explicitly:

```
EUREKA: AC-NNN — PLAN says "build custom validator" but codebase has utils/validate.ts
that handles this. Recommending: extend existing validator instead of new module.
```

Do NOT silently override the plan. Flag in HANDOFF.md under "Plan Challenges".
If the eureka is architectural (fundamentally different approach), ask user before deviating.

**Persist Eureka discoveries** to `doc/harness/learnings.jsonl` so future tasks benefit:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"eureka","source":"develop-search","key":"PATTERN_KEY","insight":"DESCRIPTION","files":["<file>"],"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

This closes the loop: Phase 2 discovers patterns, learnings.jsonl stores them, Phase 1 loads them.

### Phase 3.0: AC Dependency Analysis

Before implementing, determine which ACs can run in parallel:

1. **Read all AC target file lists** from PLAN.md.
2. **Build a dependency matrix:**
   - **Shared files** — if AC-001 and AC-002 both modify `auth.ts`, they are dependent (sequential).
   - **Data flow** — if AC-002's output is AC-003's input (e.g., AC-002 creates a function, AC-003 calls it), they are dependent (sequential).
   - **No overlap** — if ACs touch completely separate files with no data flow, they are independent (parallelizable).
3. **Classify ACs:**
   - `SEQUENTIAL` — must run in order (shared files or data dependency)
   - `PARALLEL` — no shared files, no data flow between them

```
AC Dependency Analysis:
| AC | Target files | Depends on | Classification |
|----|-------------|------------|----------------|
| AC-001 | auth.ts, user.ts | — | SEQUENTIAL (first) |
| AC-002 | billing.ts | — | PARALLEL with AC-003 |
| AC-003 | report.ts | — | PARALLEL with AC-002 |
| AC-004 | auth.ts | AC-001 | SEQUENTIAL (after AC-001) |
```

**If parallel ACs found:** Spawn parallel executor agents:

```
Agent(
  name="<task_id>:AC-002",
  subagent_type="oh-my-claudecode:executor",
  prompt="Implement AC-002 from PLAN.md for task <task_id>.
  Target files: <files>. Scope: <AC description>.
  Follow existing codebase patterns. Write regression test after.
  Write results to <task_dir>/audit/AC-002-result.md"
)
```

Collect results from parallel agents, verify each, then proceed to dependent (sequential) ACs.

**If all ACs are sequential:** Skip parallelization, proceed to Phase 3 as normal. Log: "All ACs sequential — no parallelization opportunity."

### Phase 3: Implement

Implement changes following PLAN.md exactly. Rules:

1. **One AC at a time.** Implement acceptance criteria in order (AC-001, AC-002, ...). Complete each before moving to the next. **Skip ACs listed in PROGRESS.md `completed_acs`** — they are already done.
2. **Follow existing patterns.** Match the codebase's conventions — naming, imports, error handling, test structure.
3. **Smallest coherent diff.** Each change should be the minimum needed for the AC it serves.
4. **No speculative features.** Implement only what PLAN.md specifies. Do not add "nice to have" improvements.

**Checkpoint after each AC:** Write PROGRESS.md to task_dir after completing each AC:

```yaml
task_id: <task_id>
phase: 3
completed_acs:
  - id: AC-001
    status: done
    tests: passed
  - id: AC-002
    status: done
    tests: passed
current_ac: <next AC or "done">
partial_ac: null  # or { id: AC-003, note: "edits done, regression test pending" }
updated: <ISO timestamp>
```

This enables resume if the session breaks mid-implementation. The richer schema
tracks partial progress — on resume, check `partial_ac` before re-reading files.

**Per-AC test run:** After completing each AC, run the tests relevant to that AC (not the full suite).
Build a file→test dependency map instead of guessing which tests to run:

```bash
# 1. Get files changed by this AC
git diff --name-only HEAD~1

# 2. For each changed source file, find tests that import/reference it
# Pattern: test files often mirror source paths (src/X.ts → test/X.test.ts)
# or import the module directly. Search for imports of the changed module:
grep -rl "from.*changed-module\|import.*changed-module\|require.*changed-module" --include="*.test.*" --include="*.spec.*" . 2>/dev/null

# 3. Run only those tests
<test_command> -- <discovered-test-files>
```

If no test files found for the changed module: write a new test file (Phase 3.5 regression rule).
If PLAN.md specifies verification commands per AC, prefer those over auto-discovery.

If per-AC tests fail: fix immediately. This catches issues when context is fresh, before they compound. Count against Phase 7 fix budget? No — these are free. Only Phase 7's full-suite failures count toward the 3-cycle limit.

### Phase 3.5: Regression rule (mandatory)

If the diff modifies existing behavior (not new code) and no existing test covers the changed path:
1. Write a regression test immediately. No exceptions.
2. Commit as `test: regression test for {what changed}`.

This runs during implementation, not after. After each AC implementation, check: did I change existing behavior? If yes → regression test first.

### Phase 3.6: Fix-First Pattern (continuous)

Read `plugin/skills/develop/fix-first-pattern.md` and apply it during implementation.

Classify code quality issues as AUTO-FIX (mechanical: dead code, missing types, N+1) or ASK (judgment: API design, architecture, security). Auto-fix mechanical issues immediately. Flag judgment items in HANDOFF.md.

This runs continuously during Phase 3. After all ACs are done, run one final quality scan over the full diff.

### Phase 3.7: Lint & Format (after all ACs)

Run the project's linter and formatter on all changed files. Catch mechanical style issues before quality audit phases waste time on them.

```bash
# Check manifest for lint/format commands
grep -E "lint_command|format_command" doc/harness/manifest.yaml

# Or auto-detect from project config
# JS/TS: npx eslint, npx prettier --check
# Python: ruff check, black --check
# Go: go vet, golint
# Rust: cargo clippy, cargo fmt --check
```

Steps:
1. Run linter on changed files only (`git diff --name-only | xargs <linter>`).
2. If lint errors found: auto-fix if safe (`--fix` flag), otherwise fix manually.
3. Run formatter on changed files. Auto-format is always safe — just apply it.
4. Re-run per-AC tests to confirm lint/format didn't break anything.

If no linter/formatter is configured: skip this phase. Do not install one.

### Phase 3.8: Build Check

Before the expensive quality audit pipeline (Phase 4+), verify the code compiles.
A build failure in Phase 4 wastes haiku + inherit agents on code that doesn't compile.

```bash
# Check manifest for build command
grep build_command doc/harness/manifest.yaml

# Or auto-detect
# JS/TS: npx tsc --noEmit, npx next build
# Go: go build ./...
# Rust: cargo build
# Python: python -m py_compile <files> or mypy
```

Steps:
1. Run build command on changed files (or full project if partial build isn't supported).
2. If build fails: fix compilation errors immediately. This is always T1 (our code broke it).
3. Re-run per-AC tests to confirm build fix didn't break anything.
4. If build passes: proceed to Phase 4.

If no build step exists (e.g., interpreted languages with no type checker): skip this phase.

### Phase 4: Plan Completion Audit (haiku)

After implementation, cross-reference every acceptance criterion against the actual diff.

Spawn a haiku agent for this mechanical check:

```
Agent(
  name="<task_id>:completion-audit",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="Read PLAN.md and run `git diff --stat` and `git diff --name-only`.
For each acceptance criterion (AC-001+), check: which files were supposed to change, did they change, is the change sufficient?
Build a completion table:
AC | Expected | Status | Evidence
Report any MISSING ACs. Return the full table."
)
```

If any AC is MISSING: investigate WHY before returning to Phase 3. Classify the cause:

| Cause | Signal | Remedy |
|-------|--------|--------|
| **Scope cut** | AC describes work explicitly deferred in conversation or notes | Confirm scope cut in HANDOFF. Mark AC as DEFERRED. |
| **Context exhaustion** | AC was early in plan but implementer lost context mid-session | Check PROGRESS.md — was the AC ever started? Resume from last known state. |
| **Misunderstood requirement** | AC text is ambiguous, implementer interpreted differently than intended | Re-read REQUEST.md and original AC. Clarify before re-implementing. |
| **Blocked by dependency** | AC depends on external service, migration, or another AC that wasn't done | Log blocker in HANDOFF. Mark AC as BLOCKED with dependency chain. |
| **Genuinely forgotten** | No evidence of work, no mention in conversation, no blocker | Highest priority fix. Implement immediately. Log as `genuinely-forgotten` signal. |

After classification: fix what's fixable, log what's blocked. Do not proceed with unclassified gaps.

### Phase 4.5–4.7: Parallel Quality Audit

Create an audit directory for crash-safe agent results:

```bash
mkdir -p <task_dir>/audit
```

Spawn three agents **in parallel** — each analyzes the full diff independently
and writes results to the audit directory (atomic write: write .tmp, then rename).
Issue all three Agent calls in a single message.

**Agent A: Test Coverage Audit (haiku)**

```
Agent(
  name="<task_id>:test-coverage",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the test coverage auditor for <task_id>.
Read plugin/skills/develop/test-coverage-audit.md and follow it in full.
Steps:
1. Detect test framework (check package.json, Gemfile, pytest.ini, etc.)
2. Run `git diff` to get all changed codepaths
3. Trace every changed codepath — entry points, branches, error paths, edges
4. For each path, search for existing tests (grep for test names, describe blocks)
5. Build an ASCII coverage diagram showing TESTED/GAP for each path. Classify test type:
   - **[→E2E]** — User flow spanning 3+ components, integration points where mocking hides failures, auth/payment/data-destruction paths
   - **[→EVAL]** — LLM output quality, prompt template changes, scoring/classification accuracy
   - **(unit)** — Pure functions, internal helpers, single-function edge cases (default)
6. For uncovered paths: generate tests matching existing conventions (unit by default, E2E/eval where marked)
CRITICAL: Write your full results (diagram + generated test code) to:
  <task_dir>/audit/test-coverage.md
Use atomic write: write to test-coverage.md.tmp first, then rename to test-coverage.md.
Return: 'Results written to <task_dir>/audit/test-coverage.md' plus a 3-line summary."
)
```

**Agent B: Confidence Ratings (inherit)**

```
Agent(
  name="<task_id>:confidence-ratings",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the confidence rater for <task_id>.
Read plugin/skills/develop/confidence-rated-changes.md and follow it in full.
Steps:
1. Run `git diff --stat` to get change inventory
2. For each change unit, rate 1-10 based on complexity, testing, familiarity, integration, edge cases
3. Build the confidence table with columns: Change, Files, Score, Risk, Mitigation
4. For any change rated 6 or below: add specific risk, suggested verification, fallback plan
CRITICAL: Write your full results (confidence table + low-confidence details) to:
  <task_dir>/audit/confidence-ratings.md
Use atomic write: write to confidence-ratings.md.tmp first, then rename.
Return: 'Results written to <task_dir>/audit/confidence-ratings.md' plus a 3-line summary."
)
```

**Agent C: Adversarial Self-Check (cross-model)**

Use a DIFFERENT model than implementation. If implementation was Opus → Sonnet.
If implementation was Sonnet → Haiku. Cross-model review catches blind spots.

```
Agent(
  name="<task_id>:adversarial-check",
  model=<downgrade from implementation model>,
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the adversarial reviewer for <task_id>.
Read plugin/skills/develop/adversarial-self-check.md and follow it in full.
Steps:
1. Run `git diff --unified=5` to get the full diff
2. Review with attacker/chaos-engineer mindset — check: null input, timeout, idempotency, concurrent access, resource leaks, injection vectors, off-by-one, error info leakage
3. Classify each finding as critical/high/medium/low
4. For critical and high: provide specific fix with regression test outline
5. For medium/low: document for HANDOFF
Time budget: 2-5 minutes.
CRITICAL: Write your full results (findings table + fixes) to:
  <task_dir>/audit/adversarial-findings.md
Use atomic write: write to adversarial-findings.md.tmp first, then rename.
Return: 'Results written to <task_dir>/audit/adversarial-findings.md' plus a 3-line summary."
)
```

**After all three complete — file-based merge:**

1. Read each result file from `<task_dir>/audit/`:
   - `test-coverage.md` from Agent A
   - `confidence-ratings.md` from Agent B
   - `adversarial-findings.md` from Agent C
2. If any file is missing (agent crashed): log the gap, continue with available results.
   The file-based approach means partial results survive agent failures.
3. Merge results into HANDOFF sections:
   - "Test Coverage" from Agent A (include diagram)
   - "Confidence Ratings" from Agent B (include table)
   - "Adversarial Review Findings" from Agent C
4. If Agent C reports critical/high findings: fix immediately with regression tests.
5. If Agent A generated test code: write the test files, run tests, commit.
6. **Aggregate adversarial patterns:** If Agent C's findings include 2+ issues of the same category
   (e.g., "null input", "resource leak"), log the recurring pattern to learnings:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"security-pattern","source":"adversarial","key":"VULN_CATEGORY","insight":"N instances found in this project","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

This surfaces project-specific vulnerability patterns for future adversarial reviews.

### Phase 4.8: Near-Zero Marginal Cost Check (haiku)

Spawn a haiku agent for this mechanical pattern scan:

```
Agent(
  name="<task_id>:edge-case-scan",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the edge case scanner for <task_id>.
Read plugin/skills/develop/near-zero-marginal-cost.md and follow it in full.
Steps:
1. Run `git diff` to get all changed code
2. Scan every changed function for these patterns:
   - Public/exported functions without null/undefined guards
   - async paths without error handling (await without try/catch, .then without .catch)
   - Loops/iterations that don't handle empty arrays or empty strings
   - Array index or string offset access without bounds check
   - Resources (file handles, connections, timers) without cleanup in error paths
3. For each gap, classify as trivial (<1 min fix), quick (1-5 min), or judgment (>5 min)
4. For trivial/quick: provide the exact fix (file, line, old code, new code)
5. For judgment: describe the gap and why it needs design input
Return: list of all gaps with classification and fixes for trivial/quick items."
)
```

Apply trivial/quick fixes from the haiku agent. Flag judgment items in HANDOFF.md.

### Phase 5: Scope Drift Detection

Check that changes stay within PLAN.md scope.

```bash
git diff --name-only
```

For each changed file:
1. Is this file listed in PLAN.md's target files or directly related?
2. Is the change within the stated scope (in/out boundaries)?

If a file was changed that is NOT in scope:
- **Related but not listed** (e.g., shared utility used by a target file): acceptable, note it in HANDOFF.md.
- **Unrelated change** (e.g., lint fix in a different module): revert it. It belongs in a separate task.
- **Missing from plan but clearly needed** (e.g., PLAN.md forgot to list a file that must change for the feature to work): note it in HANDOFF.md as an unplanned-but-necessary change.

**AC-level completeness check:** For each AC in PLAN.md, verify the diff contains
evidence of completion — not just that target files were touched, but that the
specific functionality described in the AC is present. File touched ≠ AC done.

**Documentation staleness check:** If any changed file has a corresponding doc file
(README section, API doc, ARCHITECTURE.md entry, inline docblock), verify the doc
still matches the new behavior:

```bash
# Find doc files that may reference changed code
git diff --name-only | while read f; do
  grep -rl "$(basename "$f" | sed 's/\..*//')" --include="*.md" doc/ README.md ARCHITECTURE.md 2>/dev/null
done | sort -u
```

If doc content is stale (references old behavior, missing new parameters, outdated
examples): flag in HANDOFF.md under "Documentation Debt". Do not fix here — that's
the writer skill's job.

### Phase 6: Bisectable Commits

Split changes into logical commits. Each commit = one coherent change.

Rules:
- **Rename/move** separate from behavior changes.
- **Test additions** separate from implementation if they test a distinct concern.
- **Mechanical refactors** (import cleanup, dead code removal) separate from feature work.
- **Template changes** separate from generated file regeneration.

```bash
# Stage and commit each logical unit separately
git add <files-for-unit-1>
git commit -m "<unit-1 message>"
git add <files-for-unit-2>
git commit -m "<unit-2 message>"
```

If changes are small enough to be one logical unit (single AC, few files), one commit is fine. Do not force artificial splits.

### Phase 6.5: Verification Gate (IRON LAW)

If any code changed during Phase 4 (quality audit fixes), Phase 4.8 (edge case fixes),
or Phase 6 (commit preparation — e.g., lint fixes, import cleanup), the test results
from Phase 3.8 are stale. Re-run before proceeding to Phase 7.

```bash
# Check if any source files changed since last test run
git diff --name-only HEAD 2>/dev/null | head -20
# If changed files exist beyond VERSION/CHANGELOG/metadata, tests are stale
```

**Rules:**
- "Should work now" → RUN IT.
- "I'm confident" → Confidence is not evidence.
- "I already tested earlier" → Code changed since then. Test again.
- "It's a trivial change" → Trivial changes break production.

If re-run is needed: execute Phase 3 per-AC tests on changed files (not full suite).
If any fail: fix immediately. This is T1 (our code broke it). Do not proceed to Phase 7
until all per-AC tests pass.

Log: `"phase_start/phase_end": "6.5"` in timeline.

### Phase 7: Verification Gate

**Step 1: Run test commands from PLAN.md:**

```bash
# Run whatever PLAN.md specifies (from its verification contract section)
<verification commands from PLAN.md>
```

If tests fail:
1. **Classify each failure along two dimensions:**

   **Dimension A: GATE vs PERIODIC**

   | Tier | Criteria | Action |
   |------|----------|--------|
   | **GATE** (blocks completion) | Regression tests, core functionality tests, tests covering changed codepaths | Must fix. Counts toward 3-cycle limit. |
   | **PERIODIC** (log only) | Style/lint tests on unrelated code, slow E2E for unrelated features, known-flaky tests | Log in HANDOFF. Don't block. Don't count toward fix limit. |

   To determine tier: does the failing test cover code that this task changed?
   - Yes → GATE. No → PERIODIC (unless it's a new test we wrote — then GATE).
   - Check `learnings.jsonl` for known-flaky test entries — those are always PERIODIC.

   **Dimension B: OWN vs PRE-EXISTING**

   | Ownership | Criteria | Action |
   |-----------|----------|--------|
   | **OWN** (our code broke it) | Failing test covers codepaths modified by this task's diff | Fix within 3-cycle limit. Uses investigate skill if needed. |
   | **PRE-EXISTING** (was broken before) | Failing test covers code NOT touched by this task, AND the same test fails on the base branch | Log in HANDOFF. Do NOT count toward fix limit. Do NOT invoke investigate. |

   To determine ownership:
   - Get changed files: `git diff --name-only <base>...HEAD`
   - For each failing test, check: does the test file OR the code it tests appear in the diff?
   - Yes → OWN. No → verify on base branch:
     ```bash
     git stash && <test_command> -- <failing-test-files> && git stash pop
     ```
   - If it also fails on base → PRE-EXISTING. If it passes on base → OWN (our change triggered it indirectly).
   - **When ambiguous, default to OWN.** Only classify as PRE-EXISTING when you can prove it.

**Persist flaky/pre-existing test knowledge:** For each PERIODIC or PRE-EXISTING failure,
log to `doc/harness/learnings.jsonl` so Phase 1 can skip them in future tasks:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"test-flaky","source":"develop-verify","key":"TEST_NAME","insight":"PRE-EXISTING/PERIODIC: <brief reason>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

This closes the loop: Phase 7 discovers flaky tests, learnings stores them, Phase 1 loads them.

2. Read `plugin/skills/develop/test-failure-triage.md` and classify each GATE+OWN failure (T1-T4).
3. For T1/T2 GATE failures, read `plugin/skills/develop/hypothesis-driven-debugging.md` and follow it. Form hypotheses, test them, then apply targeted fixes. Do NOT guess.
4. Document T3/T4 and PERIODIC failures with evidence. Never claim pre-existing without verifying on base branch.

Maximum 3 fix cycles for T1/T2. After 2 cycles with persistent failures, escalate:

**Step 2: Investigate escalation (cycle 3):**

If 2 fix cycles fail to resolve T1/T2 failures, invoke the investigate skill for structured root-cause analysis:

```
Skill("investigate", "Verification failure in task <task_id>: <failing test names>. Triage: <T1/T2>. Prior fix attempts: <summary of what was tried>.")
```

Use the investigate results for the final (3rd) fix attempt. If still failing: stop, report in HANDOFF.md.

Include the triage table in HANDOFF.md under "Test Failure Triage". Note whether investigate was invoked.

**Step 3: Build verification (if not already done):**

If Phase 3.8 was skipped (interpreted language, no build step), run a quick sanity check:

```bash
# At minimum, verify imports resolve
<test_command> --help 2>&1 | head -1
# Or import check
python -c "import <module>" 2>&1
```

This catches import errors before the QA phase.

### Phase 8: Write HANDOFF

**Concreteness standard:** Every HANDOFF entry must be specific enough to locate
without searching the codebase:
- Bad:  "Fixed auth bug" → Good: "auth.ts:47 — added null check on session.token"
- Bad:  "Added tests" → Good: "auth.test.ts:89 — test('rejects expired token'), covers AC-002"
- Bad:  "Confidence: moderate" → Good: "billing.ts:142 — confidence 6/10: N+1 on line 156"
Name the file, function, and line number. Show the exact evidence, not vague descriptions.

Call `mcp__harness__write_handoff` with:

1. **Summary**: What was implemented (one sentence per AC).
2. **Files changed**: Every file modified, created, or deleted with a one-line description.
3. **Verification results**: Each AC's verification outcome.
4. **Scope notes**: Any files changed outside PLAN.md scope (with justification).
5. **Do Not Regress**: Caveats, fragile patterns, or non-obvious constraints that future developers must know.
6. **Confidence Ratings**: Table from Phase 4.6. Highlight items rated 6 or below.
7. **Adversarial Review Findings**: Table from Phase 4.7. Critical/high fixed, medium/low deferred.
8. **Near-Zero Cost Check**: Fixed count and deferred items from Phase 4.8.
9. **Test Failure Triage**: Table from Phase 7 (if any failures occurred).
10. **Test Results**: Per-AC test results and fix history from Phase 7.
11. **Judgment Items**: ASK-classified items from Phase 3.6 that need human review.
12. **Debugging Notes**: Root cause, fix, and lesson for each failure debugged in Phase 7.
13. **Execution Metrics**: Phase timing table and fix loop counts (see below).

**Execution Metrics** — track phase start/end times throughout execution and include:

```
## Execution Metrics

| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 2: Read | Nm | <file count> files |
| Phase 3: Implement | Nm | <AC count> ACs |
| Phase 4: Audit | Nm | <gaps found> |
| Phase 7: Verify | Nm | <fix cycles> fix cycles |
| Phase 4.8: Edge cases | Nm | <fixed> fixed, <deferred> deferred |

Fix loops: N (T1: X, T2: Y)
Investigate invoked: yes/no
Runtime QA: deferred to run phase (Phase 4)
```

**Cleanup:** Delete PROGRESS.md from task_dir (no longer needed — HANDOFF is the permanent record).

### Phase 8.5: Reflect and Log

After HANDOFF is written, reflect on the execution. This is NOT the predefined signals
table — it is open-ended reflection on process friction that the signals don't capture.

Ask yourself:
1. **What took longer than expected?** — Was there a phase that dragged? Why?
2. **What surprised you?** — Hidden dependencies, unexpected patterns, framework quirks.
3. **What would you do differently?** — Process improvements for next time.
4. **Were any prior learnings helpful?** — Feedback on learning quality. Were they stale?
5. **Were any learnings missing?** — What should have been pre-loaded but wasn't?
6. **Were confidence ratings calibrated?** — Compare Phase 4.6 confidence scores against Phase 7 actual results:
   - Did a high-confidence change (>7) break in testing? → Overconfident. Log the pattern.
   - Did a low-confidence change (≤6) pass without issues? → Underconfident. Note what made it seem risky but wasn't.
   - Calibration events improve future confidence ratings. Log them:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"confidence-calibration","source":"develop-reflect","key":"overconfident|underconfident","file":"<file>","rated":<score>,"actual":"<pass|fail>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

For each insight, log immediately to `doc/harness/learnings.jsonl`:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"harness-improvement","source":"develop-reflect","key":"FRICTION_KEY","insight":"DESCRIPTION","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

**Learning quality feedback:** Rate the learnings loaded in Phase 1:
- How many were loaded? How many were actually useful during execution?
- Were any stale (referenced files/commands that no longer exist)?
- Were any missing (something you had to discover that should have been pre-loaded)?

Log: `{"key":"learning-audit","insight":"Loaded N learnings: U useful, S stale, M missing topics","task":"<task_id>"}`
This feeds back into the learning system — stale entries get pruned, missing topics get added.

This closes the learning loop: Phase 1 reads learnings, Phase 8.5 writes them back
enriched with fresh experience. Future sessions benefit from today's friction.

After reflection is logged, proceed to Phase 8.6.

### Phase 8.6: DOC_SYNC Generation

Produce DOC_SYNC.md — a structured change tracking artifact. Call `mcp__harness__write_doc_sync` with:

1. Read HANDOFF.md to extract the complete changed file list.
2. Read `doc/CLAUDE.md` to identify registered doc roots.
3. For each changed file, determine which doc root (if any) it belongs to.
4. Call `write_doc_sync` with the file list and affected roots.

This is mechanical — the data already exists in HANDOFF.md and git diff.

### Phase 8.7: Distilled Change Doc

Produce a permanent record at `doc/changes/YYYY-MM-DD-<slug>.md`. Task directories are gitignored — this is the durable artifact.

**Sources:** Read from the task directory:
- `PLAN.md` → key design decisions (why this direction)
- `HANDOFF.md` → change summary (what changed) + Do Not Regress (caveats)
- `CRITIC__runtime.md` → verification results (AC PASS/FAIL summary)
- `REQUEST.md` → original user request

**Format:**

```markdown
# <Task title — extracted from PLAN.md objective>
date: YYYY-MM-DD
task: TASK__<slug>

## Decisions
- (Key design decisions from PLAN.md, 1-2 lines each)

## Changes
- (File/module list from HANDOFF.md, one-line summary per file)

## Caveats
- (From HANDOFF.md Do Not Regress section)

## Verification
- (AC result summary from CRITIC__runtime.md)
- (runtime_verdict summary)
```

**Rules:**
- **Distill, don't copy.** Never paste source artifacts verbatim.
- **3-5 lines per section max.** If longer, you missed the point.
- **Omit empty sections.** If REQUEST.md has no user feedback, drop it.
- Create `doc/changes/` directory if it doesn't exist.

After Phase 8.7, this skill is DONE.

## Completion Report

```
DONE

Task:    <task_id>
Phase:   develop
Dir:     <task_dir>

ACs completed:     <N>/<M>
Files changed:     <count>
Commits:           <count>
Scope drift:       <none | <list>>
Verification:      PASS | FAIL (<details>)
Runtime QA:        deferred to run Phase 4
Confidence avg:    <X>/10  (low: <N> items)
Adversarial:       <clean | N findings: X fixed, Y deferred>
Edge cases:        <N> fixed, <N> deferred
Fix-first:         <N> auto-fixed, <N> judgment items flagged
Fix loops:         <N> total (investigate: yes/no)
Execution time:    <total duration>
```

## Error Handling

On any failure:
1. Report what happened.
2. Check current state via `task_context`.
3. If recoverable (test failure, missing file): attempt fix within Phase 7 limits.
4. If unrecoverable: stop, write HANDOFF.md with partial results and BLOCKED status.

Never silently continue past a failure. Every stop must include a **Next step:**
directive so the caller (agent or human) knows exactly what to do next.

### Completion Status Protocol

Report final status using one of:

| Status | When | Required content |
|--------|------|-----------------|
| **DONE** | All ACs implemented, all tests pass, HANDOFF written | Completion report (see below) |
| **DONE_WITH_CONCERNS** | All ACs done but with caveats (deferred fixes, low-confidence areas, known gaps) | Completion report + concern list with file:line evidence |
| **BLOCKED** | Cannot proceed — missing dependency, unfixable test failure, exhausted fix budget | Blocker description + Next step directive |
| **NEEDS_CONTEXT** | Missing information required to continue (ambiguous AC, unclear requirement) | What's needed + where to find it |

**Escalation format for BLOCKED/NEEDS_CONTEXT:**
```
STATUS: BLOCKED | NEEDS_CONTEXT
REASON: <1-2 sentences>
ATTEMPTED: <what was tried>
RECOMMENDATION: <what to do next>
```

It is always OK to stop and escalate. Bad work is worse than no work.
After 3 failed attempts at the same task, STOP and escalate rather than burning cycles.

| Condition | Report | Next step |
|-----------|--------|-----------|
| PLAN.md missing | BLOCKED: no plan found for task_id | Run plan skill, or verify task_id |
| All 3 fix cycles exhausted | BLOCKED: 3/3 fix cycles used | Check HANDOFF triage table, consider investigate skill |
| Pre-flight fails | BLOCKED: diagnostic table shown | Fix the specific check that failed (see table) |
| Agent crashes in 4.5-4.7 | WARNING: partial audit results | Check audit/ dir for surviving files, re-run failed agent |
| Build fails in 3.8 | BLOCKED: compilation errors | Fix errors, re-run build before proceeding to Phase 4 |

## Self-Improvement Signals

Log friction signals discovered during develop to `doc/harness/learnings.jsonl`.
These feed into the plan skill (Phase 0.1.5) and setup skill (repair mode).

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
echo '{"ts":"'"$_TS"'","type":"harness-improvement","source":"develop","key":"SHORT_KEY","insight":"DESCRIPTION","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

**Signals to log during execution:**

| When | Key | Insight example |
|------|-----|-----------------|
| PROGRESS.md resume detected | `resume-used` | "Resumed from AC-003 — session broke mid-impl" |
| Per-AC test failed >2 times for same AC | `per-ac-flaky` | "AC-002 needed 3 per-AC test attempts" |
| Phase 7 used all 3 fix cycles | `fix-cycles-exhausted` | "3/3 fix cycles used, tests still failing" |
| Investigate skill was invoked | `investigate-invoked` | "Root cause: race condition in async handler" |
| Lint errors >5 after implementation | `lint-heavy` | "27 lint errors on changed files — missing formatter setup" |
| No test framework detected (Phase 4.5) | `no-test-framework` | "No test framework found for coverage audit" |
| Runtime QA subagent failed | `runtime-qa-fail` | "qa-browser: AC-002 FAIL — form not found" (logged by run phase, not develop) |
| Runtime QA dev server unreachable | `runtime-qa-no-server` | "Dev server unreachable after 15s wait" (logged by run phase) |

**When to log:** Immediately when the signal is detected, not at Phase 8. Early logging survives session crashes.
