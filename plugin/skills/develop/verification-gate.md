# Phase 7: Verification Gate

This sub-file covers the verification gate phase. Loaded after Phase 6.5 confirms
test freshness.

---

## Step 0: Clean State Check

Before running any tests, verify the working tree is clean:

```bash
git status --porcelain
```

If the working tree has **uncommitted changes**:
- Uncommitted changes mean tests run against code that differs from what was committed.
- **Staged but uncommitted changes**: commit them first. Tests should verify committed state.
- **Unstaged changes from Phase 4/6 fixes**: commit them, then re-run Phase 6.5 test freshness check.
- **Only proceed when `git status --porcelain` is empty** (all changes committed).

This ensures test results accurately reflect the code state that will be merged.

## Step 1: Run test commands from PLAN.md

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

**Pre-existing failure response protocol:**
For each confirmed PRE-EXISTING failure:
1. **Find who likely broke it** (collaborative repos):
   ```bash
   # Who last touched the failing test?
   git log --format="%an (%ae)" -1 -- <failing-test-file>
   # Who last touched the production code the test covers?
   git log --format="%an (%ae)" -1 -- <source-file-under-test>
   ```
   If different people, prefer the production code author — they likely introduced the regression.
   Note the author in HANDOFF for awareness.
2. Log to learnings: `{"type":"pre-existing-failure","key":"TEST_NAME","insight":"Fails on base branch: <reason>","task":"<task_id>"}`
2. Note in HANDOFF under "Pre-existing Failures" with: test name, base branch result, suspected cause.
3. If the pre-existing failure is in a module this task also touches: note the interaction risk.
   "Pre-existing test X fails in billing module. Our change touches billing — verify our change doesn't worsen it."
4. Recommend: "Consider a separate task to fix pre-existing failure in {module}. Run `Skill(harness:plan)` to create one."

**Persist flaky/pre-existing test knowledge:** For each PERIODIC or PRE-EXISTING failure,
log to `doc/harness/learnings.jsonl` so Phase 1 can skip them in future tasks:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"test-flaky","source":"develop-verify","key":"TEST_NAME","insight":"PRE-EXISTING/PERIODIC: <brief reason>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

This closes the loop: Phase 7 discovers flaky tests, learnings stores them, Phase 1 loads them.

2. Read `plugin/skills/develop/test-failure-triage.md` and classify each GATE+OWN failure (T1-T4).
3. For T1/T2 GATE failures, read `plugin/skills/develop/hypothesis-driven-debugging.md` and follow it. Form hypotheses, test them, then apply targeted fixes. Do NOT guess.

   **Bug pattern reference table** — use this to inform hypothesis formation:

   | Pattern | Signature | Where to look |
   |---------|-----------|---------------|
   | Race condition | Intermittent, timing-dependent | Concurrent access to shared state |
   | Null propagation | TypeError, NoMethodError | Missing guards on optional values |
   | State corruption | Inconsistent data, partial updates | Transactions, callbacks, hooks |
   | Integration failure | Timeout, unexpected response | External API calls, service boundaries |
   | Configuration drift | Works locally, fails in staging | Env vars, feature flags, DB state |
   | Stale cache | Shows old data, fixes on cache clear | Redis, CDN, browser cache |

   **Recurring bug detection:** Before forming a hypothesis, check for prior fixes in the same area:
   ```bash
   git log --oneline -10 -- <affected-files>
   ```
   If the same file has 3+ fix commits in recent history, this is an **architectural smell**,
   not a simple bug. Flag in HANDOFF: "Recurring failures in <file> — consider architectural review."
   Recurring bugs in the same area indicate the root cause is likely a layer above the symptom.

   **External pattern search:** If the failure doesn't match any known pattern above,
   search for documented solutions before forming hypotheses:
   ```
   WebSearch: "{framework_name} {sanitized_error_message}"
   ```
   Sanitization rules:
   - Strip absolute paths, project-specific names, and line numbers
   - Keep framework name, error type, and key phrases
   - Example: "TypeError: Cannot read properties of undefined (reading 'map') at /home/user/project/src/components/List.tsx:42"
     → Search: "React TypeError Cannot read properties of undefined reading map"
   - If search finds a documented solution, add it as an informed hypothesis (higher priority than blind guessing)
   - Log the external finding: `{"type":"external-pattern","key":"ERROR_PATTERN","insight":"Found via WebSearch: <solution summary>","task":"<task_id>"}`

   **Browser debugging path (UI test failures):**

   When a failing test is a browser/E2E test or covers UI codepaths:

   1. Ensure dev server is running (`curl -s -o /dev/null -w '%{http_code}' <entry_url>`).
   2. Navigate to the failing page/route.
   3. Capture failure state:
      - Screenshot → `<task_dir>/audit/screenshots/debug-failure-<N>.png`
      - Console errors via evaluate_script
      - Network requests via list_network_requests (check for 4xx/5xx)
   4. DOM inspection:
      - take_snapshot to check expected elements exist
      - evaluate_script to check component state, data attributes
   5. Form hypothesis based on evidence:
      - Console errors (TypeError, ReferenceError → missing dependency)
      - Network failures (404 → missing route/asset, 500 → server error)
      - DOM state (element missing → rendering issue, wrong content → data issue)
      - Visual state (broken layout → CSS issue, blank page → JS error)
   6. Apply fix, re-verify in browser before re-running test.

   This is the browser equivalent of hypothesis-driven-debugging.
   Same 3-strike rule and blast radius gate apply.

   **Red flags during debugging** — if you notice any of these, slow down:
   - "Quick fix for now" — there is no "for now." Fix it right or escalate.
   - Proposing a fix before tracing data flow — you're guessing, not debugging.
   - Each fix reveals a new problem elsewhere — wrong layer, not wrong code.

   **3-strike hypothesis rule:** If 3 consecutive hypotheses fail to explain the failure,
   use `AskUserQuestion` to present the situation:
   ```
   AskUserQuestion:
     Question: "3 hypotheses failed for <test name>. Am I debugging the right thing?"
     Options:
       - A) Re-examine the failing test itself (maybe the test is wrong)
       - B) Invoke investigate skill for structured root-cause analysis
       - C) Skip this test, mark as known issue in HANDOFF
     Context:
       - Hypotheses tested: <list>
       - Evidence: <summary>
       - Test file: <path>
   ```

   **Blast radius gate:** Before applying any fix, check its scope:
   ```bash
   git diff --stat  # preview what the fix would touch
   ```
   If a fix touches >5 files: use `AskUserQuestion` to present the blast radius:
   ```
   AskUserQuestion:
     Question: "Fix for <failure> touches <N> files — this is an architecture change, not a bug fix."
     Options:
       - A) Proceed anyway (user accepts wider scope)
       - B) Narrow the fix to <=5 files and re-attempt
       - C) Defer fix, document in HANDOFF as known issue
     Context: files affected: <list>
   ```

4. Document T3/T4 and PERIODIC failures with evidence. Never claim pre-existing without verifying on base branch.

**WTF-Likelihood Self-Regulation:** After each fix cycle, assess honestly:

```
WTF Self-Check (cycle N/3):
On a scale of 1-10, how surprised am I that this fix didn't work?

1-3: Expected — the fix was targeted, failure is informative. Adjust hypothesis.
4-6: Mildly surprised — I may be debugging the wrong thing. Re-examine evidence.
7-10: Very surprised — I'm likely chasing symptoms, not root cause. ESCALATE.
```

If WTF ≥ 7 on cycle 2: use `AskUserQuestion` before proceeding to cycle 3:
```
AskUserQuestion:
  Question: "WTF score: <X>/10 — I'm likely chasing symptoms, not root cause. How should we proceed?"
  Options:
    - A) Re-examine from scratch with fresh eyes (re-read error, ignore prior assumptions)
    - B) Invoke investigate skill for structured root-cause analysis
    - C) Skip this test, document in HANDOFF as unresolved
```
Before asking, do this prep:
1. Re-read the error output from scratch (ignore prior assumptions).
2. Check: am I fixing the right file? The right function? The right module?
3. Consider: is the test itself wrong? (Wrong assertion, wrong setup, wrong mock.)
4. If after re-examination the fix is obvious: proceed to cycle 3.
5. If not obvious: invoke investigate skill for cycle 3 instead of guessing.

Log WTF score:
```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"wtf-check","source":"verify","cycle":"N","wtf_score":"X","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Maximum 3 fix cycles for T1/T2. After 2 cycles with persistent failures, escalate:

## Step 2: Investigate escalation (cycle 3)

If 2 fix cycles fail to resolve T1/T2 failures, invoke the investigate skill for structured root-cause analysis:

```
Skill("investigate", "Verification failure in task <task_id>: <failing test names>. Triage: <T1/T2>. Prior fix attempts: <summary of what was tried>.")
```

Use the investigate results for the final (3rd) fix attempt. If still failing: use `AskUserQuestion`:
```
AskUserQuestion:
  Question: "All 3 fix cycles exhausted for <test name>. How should we proceed?"
  Options:
    - A) Close task with DONE_WITH_CONCERNS, document failure in HANDOFF
    - B) Create a new investigate task for this specific failure
    - C) Extend fix budget (allow 2 more cycles)
  Context: <triage table summary>
```

Include the triage table in HANDOFF.md under "Test Failure Triage". Note whether investigate was invoked.

## Step 2.5: Plan Verification Auto-Run

If PLAN.md contains a "Verification" or "Verification Commands" section that specifies
browser-based checks, and `browser_qa_supported: true` in manifest, auto-run those checks:

1. **Parse PLAN.md verification section** for URL paths, expected elements, and interaction steps.
2. **Ensure dev server is running** (curl entry_url). Start if needed (background, 15s wait).
3. **Execute each verification step:**
   - Navigate to specified URL/path.
   - Take snapshot + screenshot.
   - Verify expected elements exist (from PLAN.md verification description).
   - Check for console errors.
   - If interaction steps specified (click, fill, submit): execute them.
4. **Log results** in HANDOFF under "Plan Verification Results":
   ```
   | Step | Expected | Actual | Status |
   |------|----------|--------|--------|
   | Navigate to /dashboard | Dashboard renders | Dashboard visible | PASS |
   | Click "Add Item" | Form appears | Form visible | PASS |
   ```

If browser QA is not available (non-browser project, no dev server, Chrome MCP missing):
skip this step. Log: "Plan verification auto-run skipped: {reason}."

## Step 3: Build verification (if not already done)

If Phase 3.8 was skipped (interpreted language, no build step), run a quick sanity check:

```bash
# At minimum, verify imports resolve
<test_command> --help 2>&1 | head -1
# Or import check
python -c "import <module>" 2>&1
```

This catches import errors before the QA phase.
