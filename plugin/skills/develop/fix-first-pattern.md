# Fix-First Pattern

Classify code quality issues as auto-fixable (mechanical) or judgment-needed.
Auto-fix mechanical issues immediately. Flag judgment items for human review.

## When to run

During Phase 3 (Implement), as a continuous quality gate. Also runs as a final
pass after implementation is complete, before the adversarial self-check.

## Classification Rules

### AUTO-FIX (fix immediately, no approval needed)

These are mechanical issues with objectively correct fixes:

| Category | Example | Fix |
|----------|---------|-----|
| Dead code | Unused imports, unreachable branches | Remove |
| Stale comments | Comments that contradict the code | Update or remove |
| Missing error handling | Promise without catch, null without check | Add standard pattern |
| Inconsistent naming | `user_id` vs `userId` in same module | Normalize to project convention |
| Missing types | `any` where type is inferrable | Add the correct type |
| N+1 queries | Loop with individual DB/API calls | Batch or eager-load |
| Hardcoded magic values | `if (status === 3)` | Extract to named constant |
| Duplicate logic | Same validation in 3 places | Extract to shared function |
| Missing cleanup | Temp file created but never deleted | Add cleanup in finally block |

### ASK (requires human judgment, flag in HANDOFF)

These need design decisions — reasonable developers disagree:

| Category | Example | Why it needs judgment |
|----------|---------|----------------------|
| API design change | Function signature affects callers | Breaking change assessment needed |
| Architecture refactor | Moving logic to a different layer | Scope risk, may belong in separate task |
| Performance tradeoff | Caching vs consistency | Depends on usage patterns |
| Error strategy | Retry vs fail-fast vs circuit breaker | Depends on SLA and caller expectations |
| Concurrency model | Lock ordering, thread safety | Needs understanding of load patterns |
| Data model change | Adding a required field | Migration strategy needed |
| Security pattern | Auth check placement, input sanitization approach | Threat model dependent |

## Procedure

### Step 1: Quality scan

After each AC implementation, scan the changed code for quality issues:

1. Read the diff for the current AC.
2. Check against AUTO-FIX categories.
3. Check against ASK categories.

### Step 2: Auto-fix mechanical issues

Fix all AUTO-FIX items immediately. No approval needed. These are objectively
correct improvements.

Rules for auto-fixes:
- **One fix per commit** when mixing with feature work, OR batch mechanical fixes
  into a single "clean up" commit if there are 3+ mechanical fixes.
- **Do not change behavior** — only fix quality, not design.
- **Run tests after auto-fix** to confirm no regression.

### Step 3: Flag judgment items

For each ASK item, add to HANDOFF.md under "Judgment Items":

```
## Judgment Items (not auto-fixed)

### [J-001] <one-line description>
- **Category:** <architecture/performance/security/etc>
- **Location:** <file:line>
- **Issue:** <what the concern is>
- **Options:**
  - A) <option with tradeoff>
  - B) <option with tradeoff>
- **Recommendation:** <A or B> because <reason>
- **Risk if deferred:** <what happens if not addressed>
```

### Step 4: Do not over-classify

Not every imperfect line of code needs classification. The fix-first pattern catches
issues that affect correctness, maintainability, or security. It does NOT refactor
working code to be "cleaner" unless there is a concrete problem.

**Test for auto-fix worthiness:** "Would a reviewer flag this as a bug or quality
issue?" If no → skip it. If yes → classify and handle.

### Step 5: Final pass

After all ACs are implemented and before the adversarial self-check, run one
final quality scan over the entire diff. Catch anything missed during per-AC
scanning. Same classification rules apply.

## 3-Attempt Escalation Rule

Applies to any fix loop in develop: per-AC fix, Phase 7 verification fix, Phase 7
browser debug (browser-verification.md). If the SAME issue fails to resolve after
3 consecutive fix attempts, STOP. Do not try a 4th attempt.

Track attempts per-issue in PROGRESS.md under `attempts:` (see Phase 3 schema).
An "issue" is identified by {test_name | symptom | file:line} — different
failures in the same cycle don't share a counter.

On the 3rd failure, invoke `AskUserQuestion` with this structured format:

```
AskUserQuestion:
  Question: "3 fix attempts exhausted for <issue>. How should we proceed?"
  Context:
    REASON: <why the current approach keeps failing — one sentence>
    ATTEMPTED:
      1. <attempt 1 summary + failure mode>
      2. <attempt 2 summary + failure mode>
      3. <attempt 3 summary + failure mode>
    RECOMMENDATION: <best next step — invoke investigate, defer to human,
                    widen scope, re-examine the test, etc.>
  Options:
    - A) Invoke investigate skill for structured root-cause analysis
    - B) Defer — mark AC/test as DEFERRED in HANDOFF with details
    - C) Extend budget (allow 2 more attempts — user must explicitly approve)
    - D) Revert the changes and re-plan
```

Log the escalation:
```bash
echo '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"'","type":"escalation","source":"fix-first","key":"3-attempt-exhausted","issue":"<issue>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Never silently keep trying past 3. Thrashing is a signal to stop, not to try harder.
