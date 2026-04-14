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
| **T3** | Pre-existing, unrelated | Test was already failing on base branch | Document, do not fix in this task |
| **T4** | Pre-existing, exposed by our changes | Test was latent-failing, our changes made it visible | Document, assess if fix is in scope |

### Step 3: Verify pre-existing classification

**Never claim T3/T4 without proof.** Required evidence:

```bash
# Check if the test passes on the base branch
git stash
git checkout <base-branch>
<run the failing test>
git checkout <feature-branch>
git stash pop
```

If the test fails on base: confirmed T3/T4. Document in HANDOFF.md.
If the test passes on base: it is T1/T2, NOT pre-existing. Fix it.

### Step 4: Handle by classification

**T1 (our code broke it):**
1. Read the test output carefully. Understand what the test expects.
2. Fix the code (not the test — the test is correct).
3. Re-run the full verification suite.
4. This counts against the 3-cycle limit in Phase 7.

**T2 (our test is wrong):**
1. Re-read the AC the test was supposed to verify.
2. Fix the test to match the AC's actual requirement.
3. Re-run verification.
4. This counts against the 3-cycle limit in Phase 7.

**T3 (pre-existing, unrelated):**
1. Document in HANDOFF.md under "Pre-existing Failures".
2. Do NOT fix in this task — it is out of scope.
3. Continue. T3 failures do not block this task.

**T4 (pre-existing, exposed by our changes):**
1. Assess: is the fix trivial (one line, obvious cause)? If yes, fix it.
2. If the fix is non-trivial or risks scope creep: document in HANDOFF.md.
3. Ask user via AskUserQuestion if unsure whether to fix.

### Step 5: Document triage results

Add to HANDOFF.md:

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
   say "unverified — may or may not be related" in HANDOFF.md.
3. **Never dismiss a failure** as pre-existing without evidence.
4. **If the test was added by this branch**, it is always T1 or T2, never T3.
