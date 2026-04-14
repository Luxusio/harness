# Hypothesis-Driven Debugging

When verification fails during Phase 7, use structured hypothesis testing instead of
random fix attempts. This is the methodology from gstack's investigate skill, adapted
for the develop phase's verification failure loop.

## When to use

Whenever Phase 7 (Verification Gate) encounters a test failure. Do NOT jump straight
to editing code. Follow this protocol.

## Protocol

### Step 1: Observe the failure

Collect the full error output. Note:

1. **What test failed** — exact test name and file
2. **What was expected** — the assertion or expected behavior
3. **What actually happened** — the actual output, error message, or exception
4. **Where it failed** — file:line of the failure point (not the test, the code under test)

Do not skim. Read the full stack trace and error message.

### Step 2: Form hypotheses

Generate 2-4 plausible hypotheses for why the failure occurred. Order by likelihood.

For each hypothesis, state:
- **What it claims** — the specific cause of the failure
- **How to test it** — one specific check that would confirm or rule it out
- **Expected result if true** — what you'd see if the hypothesis is correct

Common hypothesis categories:

| Category | Example Hypothesis | Test |
|----------|-------------------|------|
| **Logic error** | "The off-by-one in the loop skips the last item" | Add logging/print at the loop boundary |
| **Wrong assumption** | "The function assumes non-empty input but receives empty array" | Check the input at the failure point |
| **Side effect** | "Earlier test mutated shared state" | Run the failing test in isolation |
| **Timing/order** | "The async operation hasn't completed when assertion runs" | Add explicit wait/flush |
| **Missing dependency** | "The mock doesn't implement the new method" | Check the mock's interface |
| **Environment** | "The test relies on a env var that isn't set" | Check process.env / config at runtime |
| **Integration mismatch** | "The function signature changed but the call site wasn't updated" | Compare the call with the definition |

### Step 3: Test hypotheses in order

For each hypothesis (most likely first):

1. Run the specific test from Step 2 that would confirm or rule it out.
2. **If confirmed** — you found the root cause. Go to Step 4.
3. **If ruled out** — move to the next hypothesis. Do not attempt a fix yet.

**Rule:** Never attempt a code fix until at least one hypothesis is confirmed. Guessing
wastes fix cycles (you only get 3).

### Step 4: Apply targeted fix

Once a hypothesis is confirmed:

1. Fix ONLY the confirmed root cause. Do not "also fix" nearby code.
2. Re-run ONLY the failing test first (fast feedback).
3. If it passes, re-run the full verification suite.
4. If new failures appear, start a new hypothesis cycle for those.

### Step 5: Document the finding

Add to HANDOFF.md under "Debugging Notes":

```
## Debugging Notes

### Failure: [test name]
- **Root cause:** [confirmed hypothesis]
- **Fix:** [what was changed, file:line]
- **Lesson:** [one-line takeaway for future reference]
```

The lesson helps future sessions recognize similar patterns faster.

## Anti-patterns to avoid

1. **Shotgun debugging** — changing multiple things at once hoping one fixes it. One change per cycle.
2. **Assuming the test is wrong** — the test is right until proven otherwise (T2 classification in test-failure-triage.md is the exception, and it requires evidence).
3. **Fixing symptoms** — if the error is "undefined is not a function", don't add a null check. Find WHY it's undefined.
4. **Skipping the hypothesis step** — "I know what's wrong" without evidence is guessing. Even if you're right, the habit of forming and testing hypotheses catches the cases where you're wrong.

## Integration with Test Failure Triage

This protocol applies to T1 (our code broke it) and T2 (our test is wrong) failures
from the test-failure-triage protocol. T3/T4 (pre-existing) failures are handled by
triage, not debugging.
