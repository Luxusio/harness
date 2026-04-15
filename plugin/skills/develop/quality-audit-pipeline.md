# Phase 4.5–4.8: Quality Audit Pipeline

This sub-file covers the parallel quality audit pipeline and edge case scan.
Loaded during Phase 4 after the plan completion audit.

---

## Phase 4.5–4.7: Parallel Quality Audit

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
2. Count existing test files: `find . -name '*.test.*' -o -name '*.spec.*' -o -name '*_test.*' -o -name '*_spec.*' | grep -v node_modules | wc -l`
   Record this count. After generating tests, count again and report delta.
3. Run `git diff` to get all changed codepaths
3. Trace every changed codepath — entry points, branches, error paths, edges
4. Trace USER FLOWS for each changed feature (separate from code paths):
   - **User journeys**: Map the full sequence of actions a user takes that touches changed code
   - **Interaction edge cases**: Double-click/rapid resubmit, navigate away mid-operation (back/close),
     submit with stale data (page idle 30+ min, session expired), slow connection (API 10s+),
     concurrent actions (two tabs, same form)
   - **Error states the user sees**: For every error the code handles, what does the user experience?
     Clear message or silent failure? Can they recover or are they stuck?
   - **Empty/zero/boundary states**: Zero results, 10000 results, single character input, max-length input
   Add these to the coverage diagram under a separate USER FLOW COVERAGE section.
5. Build an ASCII coverage diagram showing TESTED/GAP for each path. Classify test type:
   - **[→E2E]** — User flow spanning 3+ components, integration points where mocking hides failures, auth/payment/data-destruction paths
   - **[→EVAL]** — LLM output quality, prompt template changes, scoring/classification accuracy
   - **(unit)** — Pure functions, internal helpers, single-function edge cases (default)
6. For each existing test, rate quality:
       - **★★★** — Tests behavior with edge cases AND error paths
       - **★★** — Tests correct behavior, happy path only
       - **★** — Smoke test / trivial assertion (e.g., "it renders"). Flag as effectively uncovered.
    7. For uncovered paths: generate tests matching existing conventions (unit by default, E2E/eval where marked).
   **Interaction edge case checklist** — when generating tests for user-facing code, include:
   - Double-click / rapid resubmit prevention
   - Navigate away mid-operation (back button, tab close, click another link)
   - Stale data submission (page idle 30+ minutes, session expired)
   - Slow connection handling (API takes 10+ seconds — what does user see?)
   - Concurrent actions (two tabs open, same form)
   - Empty state (zero results), boundary state (single item, max items), overflow (10000+ items)
8. Check for test anti-patterns across ALL tests (existing + generated):
   - **Missing negative paths**: Tests only verify happy path — no tests for error handling, invalid input, permission denial
   - **Isolation violations**: Tests share mutable state, depend on execution order, or mutate global fixtures without cleanup
   - **Flaky patterns**: Time-dependent assertions (Date.now, sleep), random values without seeds, order-dependent array comparisons, network calls not mocked
   - **Security enforcement**: No tests for auth guards, input sanitization, rate limiting, or permission boundaries on protected endpoints
   For each anti-pattern found: flag the test file, category, and recommended fix.
CRITICAL: Write your full results (diagram + generated test code + anti-patterns) to:
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

**Agent C: Adversarial Self-Check (cross-model, two-pass)**

Use a DIFFERENT model than implementation. If implementation was Opus → Sonnet.
If implementation was Sonnet → Haiku. Cross-model review catches blind spots.

```
Agent(
  name="<task_id>:adversarial-check",
  model=<downgrade from implementation model>,
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the adversarial reviewer for <task_id>.
Read plugin/skills/develop/adversarial-self-check.md and follow it in full.

Run `git diff --unified=5` to get the full diff. Review in TWO PASSES:

**Pass 1 — CRITICAL (run first):**
- SQL & Data Safety: string interpolation in queries, TOCTOU races, bypassing validations
- Race Conditions: read-check-write without atomicity, concurrent access without locks
- Injection Vectors: SQL/command/LDAP injection, shell injection, XSS on user-controlled data
- Authentication: session handling, token management, password storage issues
- Trust Boundaries: LLM output written to DB without validation, URLs fetched without allowlist

**Pass 2 — INFORMATIONAL (run second):**
- Null/undefined propagation: missing guards on optional values
- Resource leaks: file handles, connections, timers without cleanup
- Idempotency: operations that break on retry
- Error info leakage: stack traces or internals exposed to users
- Off-by-one errors: boundary conditions in loops/indexing
- Timeout handling: missing timeout on external calls
- Concurrent access: shared state without synchronization

For EACH finding, provide:
1. severity: critical/high/medium/low
2. confidence: 1-10 (9-10=verified by reading code, 7-8=high confidence pattern, 5-6=moderate/could be FP, 3-4=low/suspicious, 1-2=speculation)
3. category: one of: Visual/UI, Functional, UX, Performance, Console/Errors, Accessibility, Security, Data Safety, Concurrency
4. file:line and specific code reference
5. fix recommendation

Format each finding as:
[SEVERITY] (confidence: N/10, category: CAT) file:line — description
  Fix: recommended fix

For critical/high with confidence 7+: provide regression test outline.
For medium/low: document for HANDOFF.
For confidence 5-6: add caveat 'Medium confidence — verify this is actually an issue'.
For confidence 3-4: only include if severity is critical/high.

Time budget: 2-5 minutes.
CRITICAL: Write your full results (findings table + fixes) to:
  <task_dir>/audit/adversarial-findings.md
Use atomic write: write to adversarial-findings.md.tmp first, then rename.
Return: 'Results written to <task_dir>/audit/adversarial-findings.md' plus a 3-line summary."
)
```

**Agent D: Visual Smoke (haiku, browser projects only)**

Only spawned when `browser_qa_supported: true` in manifest. Otherwise skipped.

```
Agent(
  name="<task_id>:visual-smoke",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the visual smoke tester for <task_id>.
Read the manifest at doc/harness/manifest.yaml for entry_url and dev_command.
Read PLAN.md for acceptance criteria. Identify UI-related ACs (components, pages, layouts).

Steps:
1. Verify dev server is running (curl entry_url). If not, start it (background, 15s wait).
2. Navigate to entry_url. Take screenshot (desktop viewport).
3. Emulate mobile viewport (375x667). Take screenshot.
4. Emulate dark mode (colorScheme: dark, desktop viewport). Take screenshot.
   Revert to desktop light mode.
5. Check console errors:
   () => {
     const errors = [];
     const origError = console.error;
     console.error = (...args) => { errors.push(args.join(' ')); origError.apply(console, args); };
     return { note: 'Check window for existing errors', elementCount: document.querySelectorAll('*').length };
   }
6. Network request audit:
   Use list_network_requests to find failed or slow requests.
   Filter: status >= 400, or resourceType in (xhr, fetch) with duration > 3000ms.
   Report: 404s (missing assets), 5xx (server errors), CORS failures.
7. For each UI-related AC, run the per-page QA checklist:
   a. Navigate to the relevant page/route.
   b. Take snapshot + screenshot.
   c. **Visual scan:** Check layout, broken images, alignment, z-index issues.
   d. **Interactive elements:** Click every button and link on the page. Verify each does what it says.
   e. **Forms:** If forms present, test empty submission, invalid data, special characters.
   f. **Navigation:** Check all paths in/out (breadcrumbs, back button, deep links).
   g. **States:** Check empty state, loading state, error state, full/overflow state.
   h. **Console:** Check for new JS errors after interactions.
   i. **Responsiveness:** If relevant, check mobile viewport (375x667).
   j. **Auth boundaries:** What happens when logged out? Different user roles?
   k. Verify expected elements exist (from AC description).
   l. Check for visual regressions: broken layout, missing content, error states.
8. Lighthouse accessibility audit:
   Use lighthouse_audit(mode: 'snapshot', device: 'desktop').
   Extract accessibility score and specific violations.
   If lighthouse_audit is unavailable, fall back to JS-based scan:
   () => {
     const inputs = document.querySelectorAll('input:not([type=hidden])');
     const unlabeled = [...inputs].filter(i => !i.labels?.length && !i.getAttribute('aria-label') && !i.getAttribute('aria-labelledby'));
     const noAlt = [...document.querySelectorAll('img')].filter(i => !i.getAttribute('alt'));
     const dupIds = (() => { const ids = [...document.querySelectorAll('[id]')].map(e => e.id); return ids.filter((id, i) => ids.indexOf(id) !== i); })();
     return { unlabeledInputs: unlabeled.length, imagesWithoutAlt: noAlt.length, duplicateIds: dupIds.length };
   }
9. Summarize: pages visited, console errors, accessibility score, network issues, visual anomalies (desktop + mobile + dark mode).
CRITICAL: Write your full results to:
  <task_dir>/audit/visual-smoke.md
Use atomic write: write to visual-smoke.md.tmp first, then rename.
Return: 'Results written to <task_dir>/audit/visual-smoke.md' plus a 3-line summary."
)
```

**Adaptive Specialist Gating:**

Before dispatching specialists, check their historical hit rates:

```bash
# Check specialist stats from prior tasks
grep '"type":"specialist-result"' doc/harness/learnings.jsonl 2>/dev/null | tail -20
```

For each specialist: if it has been dispatched 3+ times across tasks with 0 findings total,
auto-skip it and log: "[specialist] auto-gated (0 findings in N prior dispatches)."
Security and migration specialists are NEVER gated — they're insurance policies.
Only gate performance specialist.

**Conditional Specialist Agents:**

Before spawning the quality synthesis agent, check the diff for domain-specific patterns.
If any trigger matches, spawn the corresponding specialist agent alongside the quality synthesis agent.
Only spawn specialists whose trigger matches and passed gating — skip silently otherwise.

**Agent E: Security Specialist (haiku) — when diff touches auth, payments, user data, or API boundaries:**

Check trigger: `git diff --name-only | grep -iE "(auth|login|session|token|password|payment|stripe|user|api|middleware|csrf|xss|sql)"`

```
Agent(
  name="<task_id>:security-specialist",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the security specialist for <task_id>.
Read the diff and check for OWASP Top 10 patterns:
1. Injection (SQL, command, LDAP) — unsanitized user input in queries/commands
2. Broken auth — session handling, token management, password storage
3. Sensitive data exposure — PII in logs, plaintext secrets, verbose error messages
4. XSS — unescaped user input in HTML/rendered output
5. CSRF — missing anti-CSRF tokens on state-changing requests
6. Access control — privilege escalation, IDOR, missing authorization checks
For each finding: severity (critical/high/medium/low), file:line, remediation.
CRITICAL: Write results to <task_dir>/audit/security-review.md
Use atomic write. Return 3-line summary."
)
```

**Agent F: Performance Specialist (haiku) — when diff touches data processing, loops, queries, or rendering:**

Check trigger: `git diff | grep -iE "(map\(|forEach|\.query|\.find|SELECT|render|useEffect|useMemo|computed)"`

```
Agent(
  name="<task_id>:perf-specialist",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the performance specialist for <task_id>.
Read the diff and check for performance anti-patterns:
1. N+1 queries — loops with individual DB/API calls
2. Missing memoization — expensive computations re-run on every render
3. Unbounded collections — lists/arrays grown without limit
4. Sync blocking — expensive operations in request/render path
5. Memory leaks — event listeners/timers not cleaned up, closures holding references
6. Unnecessary re-renders — React components re-rendering without prop changes
For each finding: severity, file:line, estimated impact, fix suggestion.
CRITICAL: Write results to <task_dir>/audit/perf-review.md
Use atomic write. Return 3-line summary."
)
```

**Agent G: Migration Specialist (haiku) — when diff touches DB schemas, config files, or API contracts:**

Check trigger: `git diff --name-only | grep -iE "(migration|schema|\.sql|prisma|drizzle|knex|sequelize|alembic|\.env|config\.)"`

```
Agent(
  name="<task_id>:migration-specialist",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the migration specialist for <task_id>.
Read the diff and check for migration safety:
1. Irreversible changes — DROP TABLE, DROP COLUMN without downgrade path
2. Data loss risk — NOT NULL columns without defaults, column type changes
3. Locking risk — ALTER TABLE on large tables without batching/online strategy
4. Config drift — hardcoded values, missing env vars, platform-specific paths
5. API contract breaks — removed fields, changed types, removed endpoints
6. Dependency conflicts — version pin changes, peer dependency updates
For each finding: severity, rollback strategy, safe migration approach.
CRITICAL: Write results to <task_dir>/audit/migration-review.md
Use atomic write. Return 3-line summary."
)
```

**Agent H: LLM Trust Boundary Specialist (haiku) — when diff touches LLM/AI patterns:**

Check trigger: `git diff | grep -iE "(prompt|completion|embed|vector|openai|anthropic|llm|gpt|claude|model|generate|chat|token_count|embedding)"`

```
Agent(
  name="<task_id>:llm-trust-specialist",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the LLM trust boundary specialist for <task_id>.
Read the diff and check for AI-specific safety patterns:
1. LLM output written to DB without format validation — add guards before persisting
2. LLM-generated URLs fetched without allowlist — SSRF risk if URL points to internal network
3. Structured LLM output (JSON, arrays) accepted without type/shape checks before use
4. LLM output stored in vector DBs without sanitization — stored prompt injection risk
5. 0-indexed lists in prompts — LLMs reliably return 1-indexed
6. Prompt text referencing tools/capabilities that don't match actual wired-up tools
7. Token/character limits stated in multiple places that could drift
For each finding: severity, confidence (1-10), file:line, remediation.
CRITICAL: Write results to <task_dir>/audit/llm-trust-review.md
Use atomic write. Return 3-line summary."
)
```

**Red Team dispatch (conditional):**

If the diff is large (200+ lines changed) OR any specialist agent reported a CRITICAL finding,
spawn a second adversarial pass specifically looking for what the first review MISSED:

```
Agent(
  name="<task_id>:red-team",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the Red Team reviewer for <task_id>.
Your job is NOT to re-find what other agents found. Your job is to find what they MISSED.
Read <task_dir>/audit/ to see what was already reported. Then read the full diff.

Look specifically for:
1. Issues in files that had NO findings (other agents may have skimmed them)
2. Interactions between changes in different files (each agent reviewed independently)
3. Edge cases that only appear when two features are used together
4. Missing error handling in new code paths that connect to existing code
5. Assumptions about data shape/size that would break at scale

For each finding: severity, file:line, description, why other agents likely missed it.
CRITICAL: Write results to <task_dir>/audit/red-team.md
Use atomic write. Return 3-line summary."
)
```

Skip Red Team for small diffs with no critical findings — it would add latency without value.

The quality synthesis agent reads all available audit files, including specialist and Red Team reports.

**After all agents complete — spawn quality synthesis agent:**

Instead of merging inline, delegate synthesis to a separate agent. This separates
evaluation from implementation — the implementer doesn't judge its own work.

```
Agent(
  name="<task_id>:quality-synthesis",
  model="haiku",
  subagent_type="oh-my-claudecode:executor",
  prompt="You are the quality synthesis agent for <task_id>.
Your job is to read three independent audit reports, merge them, deduplicate findings,
and produce a unified quality assessment.

Steps:
1. Read these files from <task_dir>/audit/:
   - test-coverage.md (from test-coverage agent)
   - confidence-ratings.md (from confidence-ratings agent)
   - adversarial-findings.md (from adversarial-check agent)
   - visual-smoke.md (from visual-smoke agent, if browser project)
   - security-review.md (from security specialist, if spawned)
   - perf-review.md (from performance specialist, if spawned)
   - migration-review.md (from migration specialist, if spawned)
   - llm-trust-review.md (from LLM trust specialist, if spawned)
   If any file is missing: note the gap, continue with available results.

2. Extract all findings from each report. For each finding, compute a fingerprint:
   {file}:{line}:{category} (or {file}:{category} if no line number).
   Use any existing fingerprint field if present.

2a. Classify each finding into a severity taxonomy category:
   - **Visual/UI** — layout breaks, broken images, alignment, theme issues
   - **Functional** — broken links, dead buttons, wrong redirects, state issues
   - **UX** — confusing navigation, missing feedback, unclear errors, dead ends
   - **Performance** — slow loads, N+1, unbounded collections, sync blocking
   - **Console/Errors** — JS exceptions, failed requests, CORS, CSP violations
   - **Accessibility** — missing alt text, unlabeled inputs, keyboard nav, ARIA
   - **Security** — injection, auth issues, data exposure, XSS, CSRF
   - **Data Safety** — SQL safety, race conditions, data loss, migration risk
   Include category in the findings table.

2b. Apply confidence gates:
   - Confidence 9-10: show normally in report
   - Confidence 7-8: show normally
   - Confidence 5-6: show with caveat 'Medium confidence — verify'
   - Confidence 3-4: move to appendix only
   - Confidence 1-2: suppress entirely

3. Deduplicate by fingerprint:
   - Group findings sharing the same fingerprint
   - Keep the finding with the highest severity
   - If 2+ agents found the same issue: tag it 'MULTI-AGENT CONFIRMED ({agent1} + {agent2})'
     and boost its severity one level (e.g., medium → high)

3a. Apply skip memory:
   - Check doc/harness/review-skip-memory.jsonl for previously skipped findings
   - For each finding, check if its fingerprint matches a prior skip entry
   - If matched AND the file's content hash hasn't changed (git diff <file> shows no changes):
     suppress the finding — it was reviewed and intentionally skipped before
   - If matched BUT the file HAS changed since the skip: keep the finding, it may no longer apply
   - Log any suppressed findings in an appendix so the reviewer can verify

4. Compute a quality score:
   quality_score = max(0, 10 - (critical_count * 2 + high_count * 1 + medium_count * 0.5))
   Cap at 10.

5. Check test quality: count ★★★/★★/★ rated tests from the coverage report.
   If any ★ tests exist, list them as 'effectively uncovered'.

6. Match findings against known bug patterns:
   | Pattern | Signature | Where to look |
   |---------|-----------|---------------|
   | Race condition | Intermittent, timing-dependent | Concurrent access to shared state |
   | Null propagation | TypeError, NoMethodError | Missing guards on optional values |
   | State corruption | Inconsistent data, partial updates | Transactions, callbacks, hooks |
   | Integration failure | Timeout, unexpected response | External API calls, service boundaries |
   | Configuration drift | Works locally, fails in staging | Env vars, feature flags, DB state |
   | Stale cache | Shows old data, fixes on cache clear | Redis, CDN, browser cache |
   If 2+ findings match the same pattern, note the pattern as recurring.

7. Produce a structured report with:
   - DEDUPLICATED FINDINGS table (severity, confidence, file:line, summary, agents that found it)
   - MULTI-AGENT CONFIRMED findings (highest priority)
   - Quality score
   - Test quality summary (★★★/★★/★ counts)
   - Recurring bug patterns (if any)
   - Actions: list critical/high findings that need immediate fix

CRITICAL: Write your full results to:
  <task_dir>/audit/quality-synthesis.md
Use atomic write: write to quality-synthesis.md.tmp first, then rename.
Return: 'Results written to <task_dir>/audit/quality-synthesis.md' plus a 3-line summary."
)
```

**After quality synthesis completes:**

1. Read `<task_dir>/audit/quality-synthesis.md`.
2. If critical/high findings exist: fix immediately with regression tests.
3. If test code was generated in test-coverage.md: write the test files, run tests, commit.
4. If visual issues found in visual-smoke.md: fix critical ones (broken layout, blank page),
   note non-critical in HANDOFF. Re-verify fixed pages in browser.
5. **Aggregate adversarial patterns:** If the synthesis found 2+ issues of the same category
   (e.g., "null input", "resource leak"), log the recurring pattern to learnings:

```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"security-pattern","source":"adversarial","key":"VULN_CATEGORY","insight":"N instances found in this project","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

This surfaces project-specific vulnerability patterns for future adversarial reviews.

---

## Phase 4.8: Near-Zero Marginal Cost Check (haiku)

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
