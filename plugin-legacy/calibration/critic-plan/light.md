# Calibration: critic-plan / light

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern

**Scenario**: Single-file docs update — add a new API endpoint to README.
**What was submitted**: Plan says "Acceptance: app works well after update. Verification: run the app and check."
**Why this should FAIL**: "App works well" is not a testable criterion — it cannot be verified without subjective judgment. "Run the app and check" is not an executable verification contract (no command, no endpoint, no expected output).
**Correct verdict**: FAIL — acceptance criteria vague; verification contract not executable

---

## False PASS pattern B — acceptance too vague

**Scenario**: Light-mode plan to update error messages in a CLI tool to be more descriptive.
**What was submitted**: Plan says "Acceptance: error messages are improved and users will understand them better." Verification contract: "run the tool with bad input and see if errors look right."
**Why this should FAIL**: "Users will understand them better" is a subjective, untestable criterion — there is no specific output to verify against. "Look right" has no objective definition; the critic cannot run a command and confirm pass/fail without a concrete expected string or exit code.
**Correct verdict**: FAIL — acceptance criteria too vague (no specific expected output); verification contract not executable (no command, no expected string)

---

## Correct judgment example

**Scenario**: Light-mode plan to fix a typo in a config value that was causing a 404.
**Evidence presented**:
- Scope in: Update `BASE_URL` constant in `config.ts` from `/api/v` to `/api/v1`
- Acceptance: `curl http://localhost:3000/api/v1/health` returns HTTP 200
- Verification contract: `npm run dev` then `curl http://localhost:3000/api/v1/health` — expect `{"status":"ok"}`
- Required doc sync: none
**Verdict**: PASS — scope defined, acceptance testable (specific HTTP response expected), verification contract is executable command with expected output. Scope out and rollback not required for light mode.

### False PASS — team mode for trivial task

**Scenario:** orchestration_mode set to "team" for a single-file typo fix. TEAM_PLAN.md allocates 3 workers.

**Why it should FAIL:** Team mode adds coordination overhead for a task that one agent handles trivially. The orchestration_mode should be "solo". Additionally, team mode for a single file violates the disjoint-file-ownership requirement (there is only one file).

**Correct verdict:** FAIL — orchestration_mode should be solo for single-file trivial changes.
