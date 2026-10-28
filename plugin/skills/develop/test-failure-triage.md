# Test Failure Triage

When tests fail during the verification gate, classify failures before deciding
how to handle them. Not all failures are caused by this branch's changes.

## Triage Protocol

### Step 1: Run the test suite

Run the verification commands from PLAN.md. Collect the full output.

### Step 2: Classify each failure

For every failing test, classify into one of four categories:

| Class | Label | Meaning | Action |
|-------|-------|---------|--------|
| **T1** | In-branch, caused by our changes | Test was passing on base, our diff broke it | Fix our code, re-verify |
| **T2** | In-branch, new test is wrong | New test we wrote has a bug | Fix the test, re-verify |
| **T3** | Pre-existing, unrelated | Test was already failing on base branch | Create follow-up or mention in final response, do not fix in this task |
| **T4** | Pre-existing, exposed by our changes | Test was latent-failing, our changes made it visible | Assess if fix is in scope; otherwise create follow-up |

### Step 3: Verify pre-existing classification

**Never claim T3/T4 without proof.** Required evidence:

Run the failing test in an isolated base-revision source view using equivalent
inputs, dependencies and execution context. Preserve user changes; do not stash,
reset or checkout over the active worktree to obtain base evidence.

If the test fails on base: confirmed T3/T4. Create a follow-up or mention it in final response.
If the test passes on base: it is T1/T2, NOT pre-existing. Fix it.

### Step 4: Handle by classification

**T1 (our code broke it):**
1. Read the test output carefully. Understand what the test expects.
2. Fix the code (not the test — the test is correct).
3. Rerun affected verification; retain unaffected applicable results. Run full
   integration regression when required by current acceptance and dependencies.
4. Preserve the failed attempt and use evidence-driven recovery without an attempt quota.

**T2 (our test is wrong):**
1. Re-read the AC the test was supposed to verify.
2. Fix the test to match the AC's actual requirement.
3. Re-run verification.
4. Preserve the failed attempt and use evidence-driven recovery without an attempt quota.

**T3 (pre-existing, unrelated):**
1. Create a follow-up or mention it in final response as a pre-existing failure.
2. Do NOT fix in this task — it is out of scope.
3. Report the scope accurately. A T3 label cannot override a current required
   GATE check or actual QA FAIL/BLOCKED_ENV; unresolved required work remains unfinished.

**T4 (pre-existing, exposed by our changes):**
1. Repair a confirmed cause necessary for the authorized outcome, regardless of
   line count. Amend current PLAN/WBS through its owner for supported discoveries.
2. Keep genuinely unrelated work outside scope and name it accurately; required
   GATE failures and actual QA FAIL/BLOCKED_ENV remain unfinished.
3. Ask only for an unresolved material user decision or missing authorization;
   uncertainty about an implementation detail calls for investigation.

### Step 5: Report triage results

Include this in final response or the follow-up task:

```
## Test Failure Triage

| Test | Classification | Evidence | Action |
|------|---------------|----------|--------|
| test-name-1 | T1 | Our change at file.ts:42 returns wrong type | Fixed in commit abc123 |
| test-name-2 | T3 | Fails on base branch too (verified) | Documented, not our scope |
```

## Blame Protocol

"Pre-existing" is a strong claim that requires proof. Rules:

1. **Always verify on base branch** before claiming T3/T4.
2. **If you cannot verify** (base branch unavailable, test takes too long):
   say "unverified — may or may not be related" in final response.
3. **Never dismiss a failure** as pre-existing without evidence.
4. **If the test was added by this branch**, it is always T1 or T2, never T3.
