---
name: develop
description: Implement PLAN.md with plan completion audit, scope drift detection, bisectable commits, verification gate, adversarial self-check, confidence ratings, test failure triage, fix-first pattern, hypothesis-driven debugging, per-AC visual verification, runtime smoke test, and browser-based debugging. Uses parallel agents for quality audit phases and haiku for mechanical work.
argument-hint: <task-id>
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Edit, Write, Agent, Skill, AskUserQuestion, mcp__harness__task_start, mcp__harness__task_context, mcp__harness__write_handoff, mcp__harness__write_doc_sync, mcp__chrome-devtools__navigate_page, mcp__chrome-devtools__take_snapshot, mcp__chrome-devtools__take_screenshot, mcp__chrome-devtools__evaluate_script, mcp__chrome-devtools__wait_for, mcp__chrome-devtools__list_pages, mcp__chrome-devtools__new_page, mcp__chrome-devtools__select_page, mcp__chrome-devtools__emulate, mcp__chrome-devtools__click, mcp__chrome-devtools__fill, mcp__chrome-devtools__press_key, mcp__chrome-devtools__type_text, mcp__chrome-devtools__hover, mcp__chrome-devtools__list_network_requests, mcp__chrome-devtools__performance_start_trace, mcp__chrome-devtools__performance_stop_trace, mcp__chrome-devtools__lighthouse_audit
---

Implement the plan for a harness2 task. Reads PLAN.md, implements changes, verifies completeness, writes HANDOFF.md.

## Voice

Direct, terse. Status updates, not narration.

## Error Philosophy

Errors in this skill are consumed by the AI agent running it, not humans.
MCP does not tolerate mid-task stops. **Never halt with a bare BLOCKED.**
Instead, use `AskUserQuestion` to present the blocker and recovery options.
The user decides what happens next.

Bad:  "BLOCKED: PLAN.md missing" (halts execution)
Good: AskUserQuestion with blocker description + options:
  - "Run plan skill to create PLAN.md"
  - "Verify task_id is correct"
  - "Skip and continue anyway"

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
| Runtime smoke (Phase 3.9) | **haiku** | Mechanical: dev server check, console scan |
| Visual smoke (Phase 4, Agent D) | **haiku** | Mechanical: screenshot, a11y, console check |
| Browser debugging (Phase 7) | inherit | Hypothesis formation needs reasoning + DOM inspection |
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
near-zero-marginal-cost.md, test-failure-triage.md, hypothesis-driven-debugging.md,
runtime-smoke.md, browser-verification.md, quality-audit-pipeline.md,
verification-gate.md) are NOT all loaded at the start. Each file is read only in the
phase that needs it. Do NOT pre-read sub-files — load them on demand to preserve context
window for implementation.

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
| No browser_qa_supported in manifest | 3.9, 4 (Agent D), 7 (browser debug) | Skip browser phases. Log: "Non-browser project — skipped visual verification" |
| Dev server unreachable after 15s | 3.9 | Skip runtime smoke. Note in HANDOFF. Proceed to Phase 4. |
| Chrome DevTools MCP unavailable | 3 (visual), 3.9, 4 (Agent D), 7 (browser debug) | Skip browser phases. Fall back to CLI-only verification. |

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

If any check fails: present the diagnostic table via `AskUserQuestion` with recovery options.
Do not halt — let the user choose how to proceed.

```
AskUserQuestion:
  Question: "Pre-flight checks failed for <task_id>. How should we proceed?"
  Options:
    - A) Run setup skill to fix missing pieces
    - B) Use a different task_id
    - C) Skip pre-flight and continue anyway (risky)

  Pre-flight diagnostic:
  | Check | Status | Detail |
  |-------|--------|--------|
  | manifest.yaml | MISSING | Run setup skill to initialize harness |
  | TASK_STATE | INVALID | Check task_id spelling or run task_start |
```

If all pass: proceed to Phase 1. Log: "Pre-flight: all checks passed (<N>s)".

**Context Recovery:** Before Phase 1, check for prior session state:

```bash
_TIMELINE="doc/harness/timeline.jsonl"
if [ -f "$_TIMELINE" ]; then
  tail -20 "$_TIMELINE" | grep '"event":"completed"' | tail -5
  tail -5 "$_TIMELINE" | grep -o '"skill":"[^"]*"' | tail -3
fi
ls -dt doc/harness/tasks/TASK__*/ 2>/dev/null | head -3
```

If prior sessions exist, display a one-line briefing:
"Welcome back. Last session: {skill} on {branch} ({duration}s). {N} tasks on file."

If an in-progress task matches the current task_id, log: "Context recovery: resuming from prior session."

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

If PLAN.md is absent: use `AskUserQuestion` to present recovery options:
```
AskUserQuestion:
  Question: "PLAN.md not found for <task_id>. What should we do?"
  Options:
    - A) Run plan skill to create PLAN.md (recommended)
    - B) Check if task_id is correct
    - C) Abort this task
```

### Phase 2: Read existing code + Search Before Building

Before writing any code, read every target file and dependency listed in PLAN.md. Understand:
- Current structure and patterns
- Existing tests and their conventions
- Import/export patterns
- Error handling conventions

Map what already exists vs what needs to change. Do NOT start implementing until this read pass completes.

**Baseline screenshot (browser projects only):**

Read `plugin/skills/develop/browser-verification.md` ("Phase 2: Baseline Screenshot" section)
and follow it in full. Captures before-implementation visual state for browser projects.

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

### Phase 3.1: Scope Lock

Before writing any code, declare the allowed scope in PROGRESS.md:

```yaml
# Added to PROGRESS.md
scope_lock:
  allowed_paths:
    - <file1>
    - <file2>
  test_paths:
    - <test_file1>
  forbidden_paths:
    - <files explicitly out of scope from PLAN.md>
```

**During implementation**, before each file edit, check against scope_lock:
- File in `allowed_paths` → proceed.
- File in `test_paths` → proceed (test files are always in scope).
- File not in any list → warn: "SCOPE WARNING: editing {file} not declared in scope. Adding to scope_lock."
- File in `forbidden_paths` → BLOCK. Do not edit. Escalate to user.

Auto-add newly encountered files to `allowed_paths` with a note:
```yaml
- path/to/new-file.ts  # auto-added during AC-003 (imported by target)
```

At Phase 5 (scope drift), cross-reference scope_lock against actual diff. Any file not in scope_lock = drift.

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

# Session checkpoint (survives crashes)
decisions:
  - choice: "Used Redis for caching instead of Memcached"
    reason: "Already in stack, no new dependency"
    ac: AC-001
  - choice: "Skipped pagination for MVP, will add in follow-up"
    reason: "User confirmed via AskUserQuestion"
    ac: AC-003
attempts:
  - ac: AC-002
    tried: "Direct SQL join"
    failed_because: "N+1 on related entities"
    resolved_with: "Eager loading via .includes()"
notes:
  - "billing.ts:156 — N+1 query, fixed with eager load"
  - "report.ts uses async handler that looks sync — future devs beware"
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

**Per-AC visual verification + interaction testing (browser projects only):**

Read `plugin/skills/develop/browser-verification.md` ("Per-AC Visual Verification" and
"Per-AC Interaction Testing" sections) and follow them in full. Runs for each AC that
touches UI files. Skip for non-UI ACs (data logic, config, backend).

### Phase 3.4: Test Framework Bootstrap

If no test framework was detected in Phase 1, and the project has code that should be tested:

1. **Check for opt-out marker:**
   ```bash
   [ -f doc/harness/.no-test-bootstrap ] && echo "OPT_OUT: yes" || echo "OPT_OUT: no"
   ```

2. **If no opt-out, auto-detect runtime and offer bootstrap:**

   ```bash
   _HAS_JEST=$(grep -q '"jest"' package.json 2>/dev/null && echo "yes" || echo "no")
   _HAS_VITEST=$(grep -q '"vitest"' package.json 2>/dev/null && echo "yes" || echo "no")
   _HAS_PYTEST=$(grep -q "pytest" pyproject.toml setup.cfg 2>/dev/null && echo "yes" || echo "no")
   if [ "$_HAS_JEST" = "no" ] && [ "$_HAS_VITEST" = "no" ] && [ "$_HAS_PYTEST" = "no" ]; then
     echo "NO_TEST_FRAMEWORK: true"
   fi
   ```

3. **If no framework found**, bootstrap the minimal test setup:
   - **JS/TS (Bun):** `bun add -d bun:test` + example test file
   - **JS/TS (Node):** `npm install --save-dev vitest` + `vitest.config.ts` + example test file
   - **Python:** `pip install pytest` + `conftest.py` + example test file
   - **Go:** Built-in `testing` package. Create `_test.go` file.
   - **Rust:** Built-in `#[cfg(test)]`. Create test module.

4. **Log the bootstrap:**
   ```bash
   echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"test-bootstrap","source":"develop","key":"TEST_BOOTSTRAP","insight":"Bootstrapped {framework} for {language} project","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
   ```

5. **Note in HANDOFF:** "Test framework bootstrapped: {framework}. First test file: {path}."

If the user declines test bootstrap, create the opt-out marker:
```bash
echo "User declined test bootstrap on $(date -u +%Y-%m-%dT%H:%M:%SZ)" > doc/harness/.no-test-bootstrap
```

### Phase 3.5: Regression rule (mandatory)

If the diff modifies existing behavior (not new code) and no existing test covers the changed path:
1. Write a regression test immediately. No exceptions.
2. Commit as `test: regression test for {what changed}`.

This runs during implementation, not after. After each AC implementation, check: did I change existing behavior? If yes → regression test first.

### Phase 3.6: Fix-First Pattern (continuous)

Read `plugin/skills/develop/fix-first-pattern.md` and apply it during implementation.

Classify code quality issues into categories and action tiers:

**Maintainability categories to check:**

| Category | Signal | AUTO-FIX? |
|----------|--------|-----------|
| Dead code | Unreachable branches, unused imports, commented-out code | Yes |
| Magic numbers | Hardcoded values with no named constant | Yes |
| Stale comments | Comments that contradict the code they describe | Yes |
| DRY violations | Copy-pasted logic that could be extracted | ASK — extraction may change semantics |
| Conditional side effects | `if` branches that mutate external state | ASK — requires careful analysis |
| Module boundary violations | Code reaching into another module's internals | ASK — may indicate architecture issue |

**Action tiers:**
- **AUTO-FIX** (mechanical): Dead code, missing types, N+1, magic numbers, stale comments. Fix immediately.
- **ASK** (judgment): API design, architecture, security, DRY extraction, conditional side effects, module boundary violations. Flag in HANDOFF.md.

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

### Phase 3.9: Runtime Smoke (all project types)

Read `plugin/skills/develop/runtime-smoke.md` and follow it in full.
Each project type (browser/API/CLI) has its own smoke test path.
Log `"phase_start/phase_end": "3.9"` in timeline.

### Phase 4: Plan Completion Audit (haiku)

After implementation, cross-reference every acceptance criterion against the actual diff.

Spawn a haiku agent for this mechanical check:

```
Agent(
  name="<task_id>:completion-audit",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="Read PLAN.md and run `git diff --stat` and `git diff --name-only`.
For each acceptance criterion (AC-001+), classify completion as one of:
- **DONE** — AC fully implemented, all expected files changed, diff covers stated scope
- **PARTIAL** — AC started but incomplete (some expected changes missing, or implementation covers only happy path)
- **NOT DONE** — No evidence of work on this AC in the diff
- **CHANGED** — AC was implemented differently than planned (scope shifted, better approach found, or requirement evolved)
Also classify each AC by category: CODE | TEST | MIGRATION | CONFIG | DOCS.
Build a completion table:
AC | Category | Expected | Classification | Evidence | Notes
Judgment rules:
- **Be conservative with DONE**: require clear evidence in the diff that the specific functionality
  described in the AC is present. A file being touched is NOT enough.
- **Be generous with CHANGED**: if the goal is met by different means, that counts as addressed.
  Note the difference in the Notes column.
Report any PARTIAL/NOT DONE/CHANGED ACs with specific evidence. Return the full table."
)
```

For each PARTIAL or NOT DONE AC, investigate WHY before returning to Phase 3. Classify the cause:

| Cause | Signal | Remedy |
|-------|--------|--------|
| **Scope cut** | AC describes work explicitly deferred in conversation or notes | Confirm scope cut in HANDOFF. Mark AC as DEFERRED. |
| **Context exhaustion** | AC was early in plan but implementer lost context mid-session | Check PROGRESS.md — was the AC ever started? Resume from last known state. |
| **Misunderstood requirement** | AC text is ambiguous, implementer interpreted differently than intended | Re-read REQUEST.md and original AC. Clarify before re-implementing. |
| **Blocked by dependency** | AC depends on external service, migration, or another AC that wasn't done | Use `AskUserQuestion`: present dependency chain and options (skip AC, create separate task, wait). |
| **Genuinely forgotten** | No evidence of work, no mention in conversation, no blocker | Highest priority fix. Implement immediately. Log as `genuinely-forgotten` signal. |
| **Requirement evolved** | AC text describes old approach; implementation took a better path | Mark as CHANGED. Document the new approach in HANDOFF. No fix needed. |

For CHANGED ACs: verify the new approach covers the original intent. If it doesn't, treat as PARTIAL.
After classification: fix what's fixable, log what's blocked. Do not proceed with unclassified gaps.

### Phase 4.5–4.8: Quality Audit Pipeline

Read `plugin/skills/develop/quality-audit-pipeline.md` and follow it in full.

**Diff scope detection** — before spawning agents, classify the diff to route specialists:

```bash
# Auto-detect diff scope from changed files
git diff --name-only | while read f; do
  case "$f" in
    *.tsx|*.jsx|*.css|*.scss|*.html|*.vue|*.svelte) echo "SCOPE_FRONTEND=true" ;;
    *.rb|*.py|*.go|*.rs|*.java|*controller*|*service*|*model*|*route*) echo "SCOPE_BACKEND=true" ;;
    *auth*|*session*|*token*|*password*|*permission*|*guard*) echo "SCOPE_AUTH=true" ;;
    *migration*|*schema*|*db/*|*migrate*) echo "SCOPE_MIGRATIONS=true" ;;
    *api*|*endpoint*|*graphql*|*rest*|*openapi*) echo "SCOPE_API=true" ;;
  esac
done | sort -u
```

Use detected scopes to decide which specialist agents to dispatch (security for AUTH,
performance for BACKEND, migration review for MIGRATIONS, etc.).

Before spawning Agent A, count test files for before/after tracking:
```bash
find . -name '*.test.*' -o -name '*.spec.*' -o -name '*_test.*' -o -name '*_spec.*' | grep -v node_modules | wc -l
```

After quality audit completes, count again and record delta in HANDOFF:
`Tests: {before} → {after} (+{delta} new)`

Spawns parallel agents (test coverage, confidence ratings, adversarial, visual smoke)
plus quality synthesis agent. After synthesis: fix critical/high findings, write
generated tests, aggregate adversarial patterns. Then run edge case scan (Phase 4.8).

### Phase 4.85: Test Plan Artifact

After the quality audit pipeline completes, produce a standalone test plan from
the coverage diagram results. This artifact survives task directory cleanup and
can be consumed by QA workflows.

Write to `doc/harness/test-plans/<task_id>-test-plan.md`:

```markdown
# Test Plan: <task_id>
Generated: YYYY-MM-DD
Branch: <branch>

## Affected Pages/Routes
- <URL path> — <what to test and why>

## Key Interactions to Verify
- <interaction description> on <page>

## Edge Cases
- <edge case> on <page>

## Critical Paths (must-work)
- <end-to-end flow that must work>

## Coverage Summary
- Code paths: X/Y (Z%)
- User flows: X/Y (Z%)
- Tests generated: N
```

This is lightweight — just extract from the audit results. Do not re-run analysis.

### Phase 4.9: Coverage Gate

If `doc/harness/manifest.yaml` declares `coverage_minimum` or `coverage_target`, enforce coverage thresholds:

```bash
grep -E "coverage_minimum|coverage_target" doc/harness/manifest.yaml
```

| Threshold | Default | Action if not met |
|-----------|---------|-------------------|
| `coverage_minimum` | (none) | BLOCK — must fix before proceeding |
| `coverage_target` | (none) | WARN — log in HANDOFF, proceed |

Steps:
1. Run the project's coverage tool on changed files only.
2. Compare actual coverage against thresholds.
3. If below `coverage_minimum`: write additional tests to reach the threshold.
4. If below `coverage_target` but above `coverage_minimum`: log gap in HANDOFF.
5. If no coverage tool exists: skip this phase.

**Escalation:** If writing tests to reach `coverage_minimum` would take more than 3 cycles,
use `AskUserQuestion`:
```
AskUserQuestion:
  Question: "Coverage is at X% — below minimum Y%. Writing more tests is taking significant effort."
  Options:
    - A) Continue writing tests to reach the minimum
    - B) Lower the minimum threshold for this task (update manifest)
    - C) Defer coverage gap, document in HANDOFF as known debt
```

If no coverage thresholds are configured in manifest: skip this phase entirely.

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

**SCOPE CREEP detection:** Look beyond file-level scope checks:
- Files changed that are unrelated to the stated intent
- New features or refactors not mentioned in PLAN.md
- "While I was in there..." changes that expand blast radius
- Touched files with changes unrelated to any AC (e.g., reformatted code in a distant module)

**MISSING REQUIREMENTS detection:** Compare stated intent against actual diff:
- PLAN.md requirements not addressed by any change
- Test coverage gaps for stated requirements
- Partial implementations (started but not finished — model exists but controller missing, etc.)

Evaluate with skepticism. If scope creep is found, revert unrelated changes before proceeding.

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

**Commit ordering (apply in this sequence):**

| Order | Layer | Examples |
|-------|-------|----------|
| 1 | Infrastructure | Config files, build scripts, dependency additions, CI changes |
| 2 | Models / Services / Data | Schema changes, new types, data layer, business logic |
| 3 | Controllers / Views / API | Route handlers, UI components, API endpoints |
| 4 | Tests | Test additions and updates (separate from implementation) |
| 5 | Docs / Metadata | VERSION, CHANGELOG, README updates, doc_sync |

This ordering ensures each commit leaves the codebase in a working state —
later commits depend on earlier ones, not the reverse. If a bisect lands between
commits, it stops at the infrastructure layer, not in the middle of a feature.

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

### Phase 6.7: Quality Score Trend Tracking

Persist the quality score from Phase 8 to track codebase health over time:

```bash
mkdir -p doc/harness 2>/dev/null || true
_QUALITY_SCORE=$(grep "Quality Score:" <task_dir>/HANDOFF.md 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+' || echo "unknown")
_AC_SCORE=$(grep "AC Completion:" <task_dir>/HANDOFF.md 2>/dev/null | head -1 | grep -oE '[0-9]+/10' || echo "unknown")
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"quality-trend","source":"develop","task":"'"<task_id>"'","quality_score":"'"$_QUALITY_SCORE"'","ac_score":"'"$_AC_SCORE"'"}' >> doc/harness/quality-history.jsonl 2>/dev/null || true
```

This enables trend analysis: is the codebase getting healthier or accumulating debt?

### Phase 7: Verification Gate

Read `plugin/skills/develop/verification-gate.md` and follow it in full.
Runs test commands from PLAN.md, classifies failures (GATE/PERIODIC × OWN/PRE-EXISTING),
triages with hypothesis-driven debugging (including browser debugging path),
and escalates to investigate skill on cycle 3.

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
12. **Debugging Notes**: Structured debug report for each failure debugged in Phase 7:
    ```
    ## Debug Report: <test name>
    - **Symptom**: What the test observed (error message, wrong output, timeout)
    - **Root cause**: The actual underlying cause (file:line)
    - **Fix**: What was changed and why (file:line)
    - **Evidence**: Console output, screenshots, or test output confirming the fix
    - **Regression test**: Test added or updated to prevent recurrence
    - **Related**: Other tests or codepaths affected by the same root cause
    - **Status**: RESOLVED | DEFERRED | ESCALATED
    ```
13. **Visual Evidence**: For UI-related ACs, paths to before/after screenshots
    captured during per-AC visual verification (Phase 3) and runtime smoke (Phase 3.9).
    Format:
    ```
    | AC | Screenshot | Console Errors | Viewport |
    |----|-----------|----------------|----------|
    | AC-002 | audit/screenshots/AC-002-after.png | 0 | 1280x800 |
    ```
14. **Execution Metrics**: Phase timing table and fix loop counts (see below).
15. **Quality Score**: Weighted numerical score (see below).

**Quality Score Calculation:**

```
score = round(
  (ac_completion * 0.40) +
  (test_coverage * 0.30) +
  (adversarial_clean * 0.20) +
  (scope_discipline * 0.10)
, 1)
```

Where:
- `ac_completion` = (ACs completed / total ACs) × 10. Deferred ACs count as 0.5.
- `test_coverage` = from Phase 4.5: (tested paths / total changed paths) × 10. No test framework: 5/10.
- `adversarial_clean` = max(0, 10 - (critical × 3 + high × 1.5 + medium × 0.5)). Fixed findings count at 0.25 weight.
- `scope_discipline` = 10 (no drift), 7 (auto-added only), 4 (justified drift), 0 (unjustified).

Include in HANDOFF:
```
Quality Score: X.X/10
  AC Completion: X/10 (N/M ACs)
  Test Coverage: X/10 (N/M paths)
  Adversarial Clean: X/10 (N findings: C critical, H high, M medium)
  Scope Discipline: X/10 (drift: none|auto-added|justified|unjustified)
```

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
Visual verification: <skipped | N ACs verified, N screenshots>
Runtime smoke: <skipped | PASS | FAIL>
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
6. **Were confidence ratings calibrated?** — Compare Phase 4.6 confidence scores against Phase 7 actual results. Build a calibration table:

   ```
   File | Rated | Actual | Verdict | Lesson
   auth.ts | 8/10 | PASS | calibrated | Complex but well-tested
   billing.ts | 5/10 | FAIL | overconfident | N+1 hidden by mock in unit test
   report.ts | 4/10 | PASS | underconfident | Looked risky due to async, but sequential in practice
   ```

   **Calibration metrics:**
   - **Overconfidence rate**: (changes rated >7 that failed) / (all changes rated >7)
   - **Underconfidence rate**: (changes rated ≤6 that passed cleanly) / (all changes rated ≤6)
   - **Target**: overconfidence < 20%, underconfidence < 30%

   For each calibration event:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"confidence-calibration","source":"develop-reflect","key":"overconfident|underconfident","file":"<file>","rated":<score>,"actual":"<pass|fail>","reason":"<why the rating was wrong>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

   Log the calibration metrics summary:
   ```bash
   echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"calibration-summary","source":"develop-reflect","key":"CALIBRATION_SUMMARY","insight":"Overconfidence: X%, Underconfidence: Y%, N events total","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
   ```

   **Calibration feedback loop:** When a confidence-rated change (Phase 4.6) was rated ≤6
   but actually passed (underconfident), or rated >7 but failed (overconfident), log
   the corrected pattern so future sessions can adjust:
   ```bash
   echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"calibration-lesson","source":"develop-reflect","key":"confidence-pattern","insight":"Pattern <X> in <file> was rated <N> but actual was <pass|fail>. Corrective: <what to look for>","confidence":8,"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
   ```
   Future Phase 4.6 confidence rating agents load these lessons and apply higher
   confidence to patterns that previously passed despite low ratings, and lower
   confidence to patterns that previously failed despite high ratings.

For each insight, log immediately to `doc/harness/learnings.jsonl`.
Use structured types for better future retrieval:

| Type | When to use |
|------|-------------|
| `pattern` | Reusable approach that worked |
| `pitfall` | What NOT to do (negative lesson) |
| `architecture` | Structural decision or constraint |
| `tool` | Library/framework insight or quirk |
| `operational` | Build, env, CLI, workflow knowledge |

Format:
```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"pattern|pitfall|architecture|tool|operational","source":"develop-reflect","key":"FRICTION_KEY","insight":"DESCRIPTION","confidence":N,"files":["path/to/file1","path/to/file2"],"task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Include `files` field with paths referenced by the learning. This enables staleness
detection: if those files are later deleted, the learning can be flagged automatically.

**Learning quality feedback:** Rate the learnings loaded in Phase 1:
- How many were loaded? How many were actually useful during execution?
- Were any stale (referenced files/commands that no longer exist)?
- Were any missing (something you had to discover that should have been pre-loaded)?

**Staleness detection:** For each loaded learning, verify:
1. **File existence**: If a learning references a file path, check it still exists:
   ```bash
   test -f <referenced_path> || echo "STALE: <learning_key> references missing file <path>"
   ```
2. **Contradiction check**: If two entries share the same `key` field, compare their `insight` values.
   If they contradict (e.g., "use npm test" vs "use bun test"), the newer one wins.
   Flag the older entry for removal.
3. **Recency**: If a learning is older than 30 days and references a specific version or CLI flag,
   verify it's still current. Flag if suspect.

Log: `{"key":"learning-audit","insight":"Loaded N learnings: U useful, S stale, M missing topics, C contradictions","task":"<task_id>"}`
This feeds back into the learning system — stale entries get pruned, missing topics get added, contradictions get resolved.

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
Visual verified:   <N> ACs with screenshots | skipped (non-browser)
Runtime smoke:     PASS | FAIL | skipped
Fix loops:         <N> total (investigate: yes/no)
Execution time:    <total duration>
```

## Error Handling

On any failure:
1. Report what happened.
2. Check current state via `task_context`.
3. If recoverable (test failure, missing file): attempt fix within Phase 7 limits.
4. If unrecoverable: use `AskUserQuestion` to present the blocker with options:
   - "Attempt partial fix and continue"
   - "Write HANDOFF with partial results and close task"
   - "Invoke investigate skill for deeper analysis"
   - "Abort task entirely"

Never silently continue past a failure. Never halt without asking the user.
Every `AskUserQuestion` must include the blocker description and concrete recovery options.

### Completion Status Protocol

Report final status using one of:

| Status | When | Required content |
|--------|------|-----------------|
| **DONE** | All ACs implemented, all tests pass, HANDOFF written | Completion report (see below) |
| **DONE_WITH_CONCERNS** | All ACs done but with caveats (deferred fixes, low-confidence areas, known gaps) | Completion report + concern list with file:line evidence |
| **BLOCKED** | Cannot proceed — missing dependency, unfixable test failure, exhausted fix budget | AskUserQuestion with blocker + recovery options |
| **NEEDS_CONTEXT** | Missing information required to continue (ambiguous AC, unclear requirement) | AskUserQuestion asking for the missing info |

**Escalation via AskUserQuestion (never bare stop):**
```
AskUserQuestion:
  Question: "<BLOCKED | NEEDS_CONTEXT>: <1-2 sentence reason>"
  Options:
    - A) <primary recovery action> (recommended)
    - B) <alternative action>
    - C) <fallback action>

  Context:
  - Attempted: <what was tried>
  - Recommendation: <what to do next>
```

It is always OK to ask the user. Bad work is worse than no work.
After 3 failed attempts at the same task, use `AskUserQuestion` to present
the situation rather than burning cycles silently.

| Condition | AskUserQuestion options |
|-----------|------------------------|
| PLAN.md missing | A) Run plan skill, B) Verify task_id, C) Abort task |
| All 3 fix cycles exhausted | A) Check HANDOFF triage + invoke investigate, B) Close task with partial results, C) Skip failing test and continue |
| Pre-flight fails | A) Run setup skill, B) Use different task_id, C) Skip checks (risky) |
| Agent crashes in 4.5-4.7 | A) Re-run failed agent, B) Proceed with partial audit results, C) Abort quality pipeline |
| Build fails in 3.8 | A) Fix and re-run build, B) Skip build check (interpreted language), C) Abort task |

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
| Visual smoke found broken layout | `visual-smoke-broken` | "AC-NNN: layout broken on mobile viewport" |
| Runtime smoke page blank | `runtime-smoke-blank` | "entry_url rendered <5 DOM elements" |
| Per-AC visual verification failed | `per-ac-visual-fail` | "AC-NNN: expected element missing after implementation" |
| Chrome DevTools MCP unavailable | `no-browser-mcp` | "Chrome DevTools tools not available — skipped browser phases" |

**When to log:** Immediately when the signal is detected, not at Phase 8. Early logging survives session crashes.
