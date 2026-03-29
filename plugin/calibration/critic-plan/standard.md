# Calibration: critic-plan / standard

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern

**Scenario**: Standard-mode plan to add input validation to a REST endpoint.
**What was submitted**: Plan includes all required fields. Hard fail conditions say "any validation error is a failure." Verification contract says "manually check that invalid inputs are rejected." Risks/rollback say "revert to previous version if issues arise."
**Why this should FAIL**: "Manually check" is not an executable verification contract — no specific command, no test name, no endpoint to curl with specific inputs. Hard fail conditions are present but trivially restated ("validation error = failure") without specifying which inputs trigger them. Risks/rollback is boilerplate ("revert to previous version") without a concrete rollback path.
**Correct verdict**: FAIL — verification contract not executable (no runnable commands); hard-fail conditions too vague; rollback is generic boilerplate

---

## False PASS pattern B — acceptance too vague, verification contract not executable

**Scenario**: Standard-mode plan to add a CSV export feature to a reporting dashboard.
**What was submitted**: Plan lists all required sections. Acceptance: "CSV export works correctly and the file downloads." Verification contract: "click the export button and verify a CSV appears." Hard fail: "export fails." Risks/rollback: "revert if broken."
**Why this should FAIL**: "Works correctly" and "a CSV appears" are not specific — no column structure, no expected row count, no filename pattern. "Click the export button" is a manual UI instruction, not a runnable command (no URL, no test name, no script). Hard fail and rollback are pure boilerplate with zero specificity.
**Correct verdict**: FAIL — acceptance not specific (no column/row contract); verification contract not executable (no runnable command); rollback is boilerplate with no concrete path

---

## Correct judgment example

**Scenario**: Standard-mode plan to add rate limiting middleware.
**Evidence presented**:
- Scope in/out defined; touched files listed; QA mode: tests
- Acceptance: `POST /api/data` returns 429 with `Retry-After` header after 10 requests per minute from same IP
- Verification contract: `npm test -- --grep "rate limit"` exits 0; `curl -X POST http://localhost:3000/api/data` (x11 within 60s) — 11th returns HTTP 429
- Hard fail: If 11th request returns 200, feature is broken
- Risks/rollback: Rollback = `git revert <sha>` targeting `src/middleware/rate-limiter.ts` only; no DB migration involved
**Verdict**: PASS — all standard fields present, acceptance is specific and testable, verification contract has runnable commands with expected outputs, hard fail is concrete, rollback identifies exact file and method.

### False PASS — team plan without file ownership

**Scenario:** orchestration_mode is "team", TEAM_PLAN.md exists but does not define per-worker file ownership. Workers are described by role ("frontend worker", "backend worker") without specifying which files each worker may write.

**Why it should FAIL:** Without explicit file ownership, workers may edit the same files concurrently, causing merge conflicts and lost work. Disjoint file ownership is a mandatory requirement for team mode.

**Correct verdict:** FAIL — TEAM_PLAN.md must define writable paths per worker.
