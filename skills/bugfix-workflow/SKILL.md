---
name: bugfix-workflow
description: Use when a failure, regression, or incorrect behavior must be diagnosed and fixed.
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Agent
user-invocable: false
---

## Trigger

Activate when the orchestrator classifies intent as `bugfix` — the user reports something broken, wrong, failing, or regressed.

## Procedure

### 1. Reproduce or bound the failure
- Identify the expected vs actual behavior
- Find a reproduction path: test, command, browser flow, or log evidence
- If reproduction is not possible, bound the failure: what is known, what is unknown

### 2. Before-state evidence
- Capture the current failing state with concrete evidence:
  - failing test output
  - error logs
  - browser screenshot (if UI)
  - stack trace
- This evidence anchors the fix and prevents false-positive "it works now" claims

### 3. Root cause analysis
- Read relevant code, recent changes (git log/diff), and related tests
- If the area is unfamiliar, delegate to `brownfield-mapper` first
- Distinguish between:
  - **root cause**: the actual defect
  - **symptom**: what the user sees
  - **trigger**: what sequence exposes it
- Fix the root cause, not just the symptom

### 4. Risk gate
- If the fix touches `always_ask_before` zones, get confirmation
- If the root cause is in a shared/critical path, assess blast radius before fixing

### 5. Fix
Delegate to `implementation-engineer` with:
- Root cause analysis
- Specific fix scope
- Relevant constraints
Make the smallest correct fix.

### 6. Regression protection
Delegate to `test-engineer` to:
- Add a test that fails without the fix and passes with it
- This is mandatory for bugs — every fix must have a regression test

### 7. After-state validation
- Run the same validation that showed the failure
- Confirm the fix with concrete evidence
- Run broader tests to check for regressions

### 8. Knowledge capture
If the bug reveals durable knowledge, pass to `docs-sync` with:
```
Handoff:
  from: bugfix-workflow
  scope: <files that were fixed>
  findings: <root cause, debugging insight, architecture finding>
  constraints: <rules that apply>
  next_action: Record in the appropriate location (runbooks, architecture, approvals)
```

### 9. Summary
Report:
- Root cause (one sentence)
- What was fixed
- Before/after evidence
- Regression test added
- Any durable knowledge recorded
- Remaining concerns

## Guardrails

- Never claim "fixed" without after-state evidence
- Never skip regression test for a bug fix
- Never patch a symptom when the root cause is identifiable
- Record debugging knowledge that will save time next time
