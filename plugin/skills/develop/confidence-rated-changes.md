# Confidence-Rated Changes

Rate each change in the diff on a 1-10 confidence scale. Low-confidence changes
are flagged explicitly in HANDOFF.md so the runtime critic knows where to focus
verification effort.

## Confidence Scale

| Score | Label | Meaning |
|-------|-------|---------|
| 9-10 | **Verified** | Tested, traced through all paths, confident in correctness |
| 7-8 | **High** | Logic is sound, follows established patterns, no edge case concerns |
| 5-6 | **Moderate** | Works for happy path, uncertain about edge cases or integration |
| 3-4 | **Low** | Unfamiliar pattern, complex logic, or untested integration point |
| 1-2 | **Speculative** | Best-effort implementation, may need significant rework |

## Procedure

### Step 1: Build change inventory

```bash
git diff --stat
```

For each file changed, identify the logical units of change (one per AC or feature).

### Step 2: Rate each change

For each change unit, assign a confidence score. Consider:

- **Complexity** — Simple mechanical change (8+) vs complex multi-file refactor (5-6)
- **Testing** — Has regression test (7+) vs no test coverage (3-5)
- **Familiarity** — Follows existing patterns (7+) vs novel approach (4-6)
- **Integration** — Self-contained (7+) vs touches shared utilities (5-6)
- **Edge cases** — All handled (7+) vs some unverified (4-6)

### Step 3: Build confidence table

```
## Confidence Ratings

| Change | Files | Score | Risk | Mitigation |
|--------|-------|-------|------|------------|
| AC-001: <desc> | file1.ts, file2.ts | 8/10 | Integration with X | Test covers path |
| AC-002: <desc> | file3.ts | 5/10 | Edge case in error path | Needs manual QA |
| AC-003: <desc> | file4.ts | 9/10 | None | Straightforward |
```

### Step 4: Flag low-confidence items

Any change rated 6 or below MUST include:

1. **Specific risk** — What exactly might go wrong
2. **Suggested verification** — What the critic should check
3. **Fallback plan** — What to do if the change is wrong

Write these as explicit items in HANDOFF.md under "Low-Confidence Changes":

```
## Low-Confidence Changes

### AC-002: <description> (5/10)
- **Risk:** Error path may not handle timeout correctly
- **Verify:** Test with simulated timeout, check retry behavior
- **Fallback:** Revert to previous error handling, add explicit timeout config
```

### Step 5: Do not artificially inflate scores

A 5 is a 5. Do not round up to make the report look better. Accurate low scores
help the runtime critic focus where it matters. An honest 5 with clear risk
description is more valuable than an inflated 8 with no detail.
