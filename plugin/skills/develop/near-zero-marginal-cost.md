# Near-Zero Marginal Cost Check

Loaded by `develop/SKILL.md` Phase 4.8.
Scans for edge cases that can be fixed in under 5 minutes — the "boil the lake" principle.

## Philosophy

When marginal cost of completeness is near zero (under 5 minutes), always go the extra step.
A missing null check that takes 30 seconds to add saves a potential production bug and a 30-minute debugging session later.

## When to run

After Phase 4.5 (Test Coverage Audit), before Phase 5 (Scope Drift Detection).
This is the last quality pass before scope and commit discipline take over.

## Procedure

### Step 1: Scan changed code for common gaps

Read every file in the diff. For each changed function/method:

1. **Null/undefined guards** — Does every public/exported function handle null, undefined, or empty input?
2. **Error handling on async paths** — Is every `await` or `.then()` wrapped in try/catch or `.catch()`? Does every Promise have error propagation?
3. **Empty collection handling** — Do loops and iterations handle `[]`, `""`, `{}` gracefully?
4. **Boundary checks** — Are array indices, string offsets, and numeric ranges guarded?
5. **Resource cleanup** — Are file handles, connections, subscriptions, and timers cleaned up in all code paths (happy + error)?
6. **Type narrowing** — Are union types narrowed before access? Are optional fields checked before use?

### Step 2: Classify each gap

| Category | Time estimate | Action |
|----------|---------------|--------|
| **Trivial** (<1 min) | Add null check, add catch, add empty guard | Fix immediately |
| **Quick** (1-5 min) | Add error path handling, add boundary check | Fix immediately |
| **Judgment** (>5 min or design decision) | Architectural change, API redesign | Flag in HANDOFF |

### Step 3: Fix trivial and quick items

Fix immediately. Follow fix-first pattern rules:
- One fix per logical unit
- Run tests after batch of fixes
- No behavioral change beyond the guard itself

Do not over-engineer. A simple `if (!input) return default` is correct. Do not add a validation library.

### Step 4: Flag judgment items

For items that would take more than 5 minutes or require design decisions:

```
## Near-Zero Cost Deferred Items

| Item | Location | Why deferred | Estimated fix time |
|------|----------|--------------|-------------------|
| Missing rate limiting on endpoint | api/handler.ts:42 | Needs design: sliding window vs fixed | 30min |
| No retry on transient DB errors | db/client.ts:18 | Retry strategy decision needed | 20min |
```

### Step 5: Record results in HANDOFF

Add to HANDOFF.md:

```
## Near-Zero Marginal Cost Check

Fixed: N trivial/quick items
Deferred: M judgment items (see table above)
```

If no gaps found: "Near-zero cost check: clean — all edge cases covered."

## Anti-patterns

1. **Do not add features** — This checks for robustness, not completeness of functionality. Missing features belong in a new AC or task.
2. **Do not refactor** — Only add guards and error handling. Structural improvements are out of scope.
3. **Do not add tests here** — Test generation happened in Phase 4.5. This phase only adds defensive code.
4. **Do not gold-plate** — A 6-minute fix is not "near zero." Respect the boundary.
