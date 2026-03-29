# Calibration: critic-plan / light

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern

**Scenario**: Single-file docs update — add a new API endpoint to README.
**What was submitted**: Plan says "Acceptance: app works well after update. Verification: run the app and check."
**Why this should FAIL**: "App works well" is not a testable criterion — it cannot be verified without subjective judgment. "Run the app and check" is not an executable verification contract (no command, no endpoint, no expected output).
**Correct verdict**: FAIL — acceptance criteria vague; verification contract not executable

---

## Correct judgment example

**Scenario**: Light-mode plan to fix a typo in a config value that was causing a 404.
**Evidence presented**:
- Scope in: Update `BASE_URL` constant in `config.ts` from `/api/v` to `/api/v1`
- Acceptance: `curl http://localhost:3000/api/v1/health` returns HTTP 200
- Verification contract: `npm run dev` then `curl http://localhost:3000/api/v1/health` — expect `{"status":"ok"}`
- Required doc sync: none
**Verdict**: PASS — scope defined, acceptance testable (specific HTTP response expected), verification contract is executable command with expected output. Scope out and rollback not required for light mode.
