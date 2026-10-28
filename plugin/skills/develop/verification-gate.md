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
- **Unstaged changes from Phase 4.5–4.9/6 fixes**: commit them, then re-run Phase 6.5 test freshness check.
- **Only proceed when `git status --porcelain` is empty** (all changes committed).

This ensures test results accurately reflect the code state that will be merged.

## Step 0.5: Install-tree removals (repos whose tests can reach a runtime)

The repository is not the whole blast radius. Hooks execute from the installed
trees — `~/.claude/harness-dev`, the Claude plugin cache
`~/.claude/plugins/cache/harness/harness` that Claude Code actually runs hooks
from, `~/.codex/harness`, and the Codex plugin cache entry
`~/.codex/plugins/cache/harness/harness` that Codex actually loads — and a
test, script, or mutation experiment that reaches one can delete the runtime
needed by subsequent work. Review and QA check all four roots, not only task artifacts.

```bash
snapshot() { for r in "$HOME/.claude/harness-dev" \
                     "$HOME/.claude/plugins/cache/harness/harness" \
                     "$HOME/.codex/harness" \
                     "$HOME/.codex/plugins/cache/harness/harness"; do
  [ -d "$r" ] || continue
  find "$r" -mindepth 1 -maxdepth 1 -type f -printf '%p\n'
  for v in "$r"/*/ "$r"/.[!.]*/; do
    [ -d "$v" ] || continue
    [ "$1" = before ] && [ -f "$v/.orphaned_at" ] && continue
    find "${v%/}" -type f -not -path '*/__pycache__/*' -printf '%p\n'
  done
done | LC_ALL=C sort; }
snapshot before > /tmp/install-before.txt
<verification commands>
snapshot after > /tmp/install-after.txt
LC_ALL=C comm -23 /tmp/install-before.txt /tmp/install-after.txt   # must be empty
```

Only **removals** matter, and the snapshot records **paths only, excluding
`__pycache__`**, for the same two reasons `tests/conftest.py` does: hooks
legitimately regenerate bytecode during a run and `python3 install.py --force`
prunes those directories outright, so including them reports losses right after
the repair this paragraph prescribes; and a rewritten file is not a removal, so
recording sizes or checksums cries wolf on every normal run. The `before`
snapshot also skips version directories that already hold a `.orphaned_at`
file: the Claude CLI marks superseded plugin-cache versions that way and deletes
them later, which is not a removal by your commands. The `after` snapshot walks
everything, because an install during the run (including the repair below)
orphans the live version with its files still present. A set
`CLAUDE_CONFIG_DIR` moves the Claude cache away from `$HOME/.claude`; this
snapshot does not follow it. If anything
disappeared, repair with `python3 install.py --force` before continuing, and
bind the responsible test to a tmp install root.

In this repository the pytest suite carries the same check automatically
(`tests/conftest.py::install_trees_lose_no_files`), and
`test_the_documented_snapshot_matches_the_fixture` pins this snippet to that
fixture's semantics; the manual snapshot covers work that runs outside pytest.
See `doc/harness/REQ__guards-are-verified-where-they-run.md`.

## Step 1: Run test commands from PLAN.md

Before issuing a frozen target or starting native roles, identify writable
operational outputs from the actual command and framework. Declare bounded
runtime-only `output_dirs` outside selected inputs; in this repository pytest's
session marker guard needs `output_dirs:["doc/harness/tasks"]`. An output cannot
overlap or contain selected inputs, and an output inside a selected source root
must be explicitly excluded. Exclude only actual runtime outputs, keeping every
required source, dependency and configuration input selected and read-only.
Route caches and temporary fixture artifacts to external paths or disable
optional caches before target issuance. For pytest use an absolute external
`--basetemp`, `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`, and explicit
bounded workers (`-n 4` or lower here). Execute against the latest returned
`snapshot_root` with that exact cwd; reissued preparation may reconstruct a
different path for the same token. Never chmod frozen sources or fall back to
the mutable checkout to make verification writable.

If execution fails because runtime output preparation is missing, retain the
failed attempt and repair that environment-only cause through scoped target
preparation. Applicable completed source-bound reviews remain valid when selected
bytes, commands, selected environment, toolchains and review scope are unchanged;
rerun only affected blocked execution after repair. Changing argv (including
cache flags) or relevant context requires the appropriate new target and affected
evidence. Preserve failed history and authentic review-before-QA ordering;
successful roles are reused when their applicable inputs remain unchanged.

Run the highest available verification tier without asking the user to opt in.
For shared preparation, use `verify_runner.py --jobs <group.json> --root <exact
source-view> --results <task-work/group-results.json> --task-id <task> --run-id
<run> --generation <generation>`. One group has at most one producer and its
read-only consumers; independent groups use separate result files and can run
concurrently. Declare source/artifact inputs, argv, environment, toolchains,
explicit producer output files and actual host capabilities. Name shared
VM/display/GPU/audio/port/build resources in each conflicting job's `resources`.
Pending leases do not occupy worker slots. Use the same per-group result file
for restored views or later outcome assertions: unchanged scoped bytes and
context reuse successful execution; lost output regenerates preparation without
invalidating consumers of byte-identical artifacts. Legacy shell commands do not
gain reuse authority. Use `--reuse-only` for a later outcome assertion to validate
that scoped proof without rerunning product commands on a cache miss. Typed execution
JSON is advisory and never replaces native
actual independent review/QA or WBS gates. See
`doc/harness/REQ__verification-execution.md` for declarations, capability preflight,
failure states and ownership boundaries.
If this task created or unblocked a live/API/browser/CLI verification path, use
that path now. Local rebuilds, dev server restarts, local DB seeds, and dev-only
tokens are normal verification work. Stop to ask only for destructive state changes,
production resources, paid/external credentials, or a real product choice between
valid approaches.

```bash
# Run whatever PLAN.md specifies (from its verification contract section)
<verification commands from PLAN.md>
```

If tests fail:
1. **Classify each failure along two dimensions:**

   **Dimension A: GATE vs PERIODIC**

   | Tier | Criteria | Action |
   |------|----------|--------|
   | **GATE** (blocks completion) | Regression tests, core functionality tests, tests covering changed codepaths | Repair authorized failures; otherwise remain unfinished. |
   | **PERIODIC** (log only) | Checks explicitly outside required acceptance | Report their actual results; they do not satisfy required checks. |

   Required acceptance checks remain GATE even if their failure appears unrelated
   or historically flaky. Inspect actual dependency and acceptance scope before
   classifying a check as PERIODIC; a flaky label never grants PASS.

   **Dimension B: OWN vs PRE-EXISTING**

   | Ownership | Criteria | Action |
   |-----------|----------|--------|
   | **OWN** (our code broke it) | Failing test covers codepaths modified by this task's diff | Repair the confirmed cause using `hypothesis-driven-debugging.md`. |
   | **PRE-EXISTING** (was broken before) | Failing test covers code NOT touched by this task, AND the same test fails on the base branch | Preserve base evidence; required GATE failures remain unfinished. |

   To determine ownership:
   - Get changed files: `git diff --name-only <base>...HEAD`
   - For each failing test, check: does the test file OR the code it tests appear in the diff?
   - Yes → investigate the changed behavior. No → run the failing test against an
     isolated base-revision source view with equivalent dependencies and execution
     context. Preserve user changes; do not stash, reset or checkout over them.
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
   Note the author in the final response or follow-up task for awareness.
2. Log to learnings: `{"type":"pre-existing-failure","key":"TEST_NAME","insight":"Fails on base branch: <reason>","task":"<task_id>"}`
2. Note the test name, base branch result, and suspected cause in the final response or a follow-up task.
3. If the pre-existing failure is in a module this task also touches: note the interaction risk.
   "Pre-existing test X fails in billing module. Our change touches billing — verify our change doesn't worsen it."
4. Recommend: "Consider a separate task to fix pre-existing failure in {module}. Run `Skill(harness:plan)` to create one."

**Persist flaky/pre-existing test knowledge:** For each PERIODIC or PRE-EXISTING failure,
record concrete reusable facts in `doc/harness/learnings.jsonl`; future tasks
must still determine current acceptance and applicability:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"test-flaky","source":"develop-verify","key":"TEST_NAME","insight":"PRE-EXISTING/PERIODIC: <brief reason>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Historical classifications inform diagnosis; they never waive current required checks.

2. Read `${CLAUDE_PLUGIN_ROOT}/skills/develop/test-failure-triage.md` and classify each GATE+OWN failure (T1-T4).
3. For T1/T2 GATE failures, read `${CLAUDE_PLUGIN_ROOT}/skills/develop/hypothesis-driven-debugging.md` and follow it. Form hypotheses, test them, then apply targeted fixes. Do NOT guess.

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
   not a simple bug. Flag in final response or a follow-up task: "Recurring failures in <file> — consider architectural review."
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
      - Screenshot, inspected in the current browser tool response
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
   The same evidence-driven recovery and ownership checks apply.

   **Red flags during debugging** — if you notice any of these, slow down:
   - "Quick fix for now" — there is no "for now." Fix it right or escalate.
   - Proposing a fix before tracing data flow — you're guessing, not debugging.
   - Each fix reveals a new problem elsewhere — wrong layer, not wrong code.

   **Scope check:** Before a fix, inspect the actual diff and affected callers.
   File count does not decide whether a change needs approval. Repair necessary
   in-scope causes and amend current PLAN/WBS through its owner; ask only for an
   unresolved material user decision or missing external/destructive authority.
   Preserve assigned ownership and user changes.

4. Document T3/T4 and PERIODIC failures with evidence. Never claim pre-existing without verifying on base branch.

## Step 2: Evidence-driven root-cause recovery

When repairs fail to explain the observation or produce no new evidence, revisit
`hypothesis-driven-debugging.md` from the actual failing entrypoint. Re-read the
full error, check callers and state transitions, and distinguish product errors,
fixture errors, verification-tool errors and environment blockers. Do not repeat
an unchanged failing approach or add counters, scores or an approval budget.

Inspect owner state and active writers after interruptions; retry only known safe
effects. Preserve the same task/run, actual failed history, ownership and
applicable unchanged proofs. Repair authorized failures autonomously and restore
affected independent review PASS before actual QA. Required FAIL or BLOCKED_ENV
remains unfinished with its concrete blocker and unblock condition; exhausted
effort never authorizes skip, deferral or completion. Ask only for a genuine
unresolved material decision or missing authorization, as in `fix-first-pattern.md`.

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
4. **Report results** in final response under "Plan Verification Results":
   ```
   | Step | Expected | Actual | Status |
   |------|----------|--------|--------|
   | Navigate to /dashboard | Dashboard renders | Dashboard visible | PASS |
   | Click "Add Item" | Form appears | Form visible | PASS |
   ```

If browser verification is not applicable, report that scope explicitly. If it
is required but the browser tool or app is unavailable, report BLOCKED_ENV with
the concrete unblock condition; do not skip required browser acceptance.

## Step 3: Build verification (if not already done)

If Phase 3.8 was skipped (interpreted language, no build step), run a quick sanity check:

```bash
# At minimum, verify imports resolve
<test_command> --help 2>&1 | head -1
# Or import check
python -c "import <module>" 2>&1
```

This catches import errors before the QA phase.

## Step 3.5: Transient-failure diagnosis

Preserve every actual failed attempt. A safe targeted rerun may help distinguish
an intermittent environment, timing or fixture cause; inspect state and effects
first so a retry cannot duplicate unknown or non-idempotent work. Do not rerun an
entire successful suite to repair recording, and do not keep retrying unchanged
failures. A retry PASS alone does not clear an unresolved required QA failure.
Resolve the cause or publish its actual unfinished blocker, restore affected
independent review when needed, then obtain applicable actual QA PASS. Retain
unaffected source-bound results and all failed history.

## Step 3.6: Severity × confidence close gate

Apply this rule to actual independent reviewer/QA findings before WBS close.
No quality synthesis agent or synthesized findings table is a prerequisite:

| Severity | Confidence | Action |
|----------|-----------|--------|
| critical | ≥ 7 | **hard-block close.** Must fix or defer via `AskUserQuestion`. |
| high | ≥ 8 | **hard-block close.** Must fix or defer via `AskUserQuestion`. |
| high | 5–7 | WARN — report as deferred or follow-up; close allowed. |
| medium / low | any | final response or follow-up only; close allowed. |
| any | < 5 | appendix only; do not surface at gate. |

Repair blocking findings and restore affected independent review before actual
QA. The user may explicitly change acceptance scope; publish that decision
through the PLAN/WBS owner and preserve the unfinished/deferred history. Neither
a severity score nor deferral prose overrides required actual QA/UX PASS and
current WBS checks. Silent deferral is not permitted.

## Step 4: Record the verification result

Keep acceptance intent in PLAN.md and verification evidence in the ordered
current WBS outcome checks and concrete QA evidence. A failed AC loops back to the fix cycle;
an explicitly deferred AC is named with its reason in the final response.
`wbs(action="close")` relies on current required WBS checks, not a second AC ledger.
