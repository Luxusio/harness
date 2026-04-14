# Adversarial Self-Check

After implementation, perform a quick "attacker/chaos engineer" review of your own
changes. The goal: catch what normal review misses by thinking adversarially.

## When to run

After Phase 4 (Plan Completion Audit) confirms all ACs are implemented, before
scope drift detection and verification gate.

## Procedure

### Step 1: Diff review with attacker mindset

```bash
git diff --unified=5
```

Read the full diff. For each changed function/module, ask:

1. **What happens if input is null, empty, or malformed?**
2. **What happens on timeout or network failure?**
3. **What happens if this runs twice (idempotency)?**
4. **What happens under concurrent access?**
5. **Are there resource leaks (file handles, connections, temp files)?**
6. **Are there injection vectors (command, SQL, path traversal, XSS)?**
7. **Are there off-by-one errors in bounds checking?**
8. **Does error handling expose internals (stack traces, file paths, credentials)?**

### Step 2: Classify findings

For each issue found, classify severity:

| Severity | Criteria | Action |
|----------|----------|--------|
| **Critical** | Security hole, data loss, crash in production path | Fix immediately, add regression test |
| **High** | Error path that produces wrong result silently | Fix immediately, add regression test |
| **Medium** | Edge case with degraded behavior | Fix if trivial, otherwise flag in HANDOFF |
| **Low** | Cosmetic, unlikely scenario | Flag in HANDOFF only |

### Step 3: Fix critical and high findings

Fix critical and high severity issues immediately. Each fix gets its own regression
test. Do NOT defer critical or high findings — they are bugs you just introduced.

### Step 4: Document remaining findings

For medium/low findings not fixed, add to HANDOFF.md under "Adversarial Review":

```
## Adversarial Review Findings

| Finding | Severity | Status | Notes |
|---------|----------|--------|-------|
| [description] | medium | deferred | [why acceptable] |
| [description] | low | deferred | [why acceptable] |
```

### Step 5: Time budget

This entire check should take 2-5 minutes. It is NOT a full security audit — it is
a quick sanity pass to catch obvious issues that fresh eyes on the diff can spot.

If no findings: write "Adversarial review: clean — no critical/high findings" in
HANDOFF.md and move on.
