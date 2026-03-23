---
name: bugfix-workflow
description: Reproduces failures, captures before-state evidence, identifies root cause, coordinates the fix and regression tests, and records durable debugging knowledge.
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
- If the area is unfamiliar, delegate to `harness:brownfield-mapper` first
- Distinguish between:
  - **root cause**: the actual defect
  - **symptom**: what the user sees
  - **trigger**: what sequence exposes it
- Fix the root cause, not just the symptom
- If standard analysis is insufficient, escalate to `oh-my-claudecode:debugger` for deeper root-cause analysis if it is available in the current session. Otherwise continue with harness agents and main-thread reasoning.
- For bugs with unclear causality or intermittent behavior, escalate to `oh-my-claudecode:tracer` for evidence-driven causal tracing if it is available in the current session. Otherwise continue with harness agents and main-thread reasoning.

### 4. Risk gate
Evaluate the planned fix action and planned paths using `harness/scripts/check-approvals.sh` or equivalent deterministic logic against `harness/policies/approvals.yaml`. If any rule matches → stop and ask the user for confirmation before proceeding.

Also check the `ask_when` situational flags in `approvals.yaml`:
- `requirements_ambiguous`: expected behavior after the fix is unclear
- `blast_radius_unknown`: the fix touches a shared or critical path whose full scope cannot be determined — assess blast radius before proceeding
- `existing_rule_conflicts`: an existing confirmed rule might conflict with the fix approach

If any `ask_when` condition applies → stop and ask the user before proceeding.

When scope is unclear, perform brownfield mapping (Step 3) and blast radius assessment before reaching this gate.

### 5. Fix
Delegate to `harness:implementation-engineer` with:
- Root cause analysis
- Specific fix scope
- Relevant constraints
Make the smallest correct fix.

### 6. Regression protection
Delegate to `harness:test-engineer` to:
- Add a test that fails without the fix and passes with it
- This is mandatory for bugs — every fix must have a regression test

### 7. After-state validation
- Run the same validation that showed the failure
- Confirm the fix with concrete evidence
- Run broader tests to check for regressions

### 8. Knowledge capture
If the bug reveals durable knowledge, pass to `harness:docs-sync` with:
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
