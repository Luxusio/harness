---
name: test-expansion
description: Use when changes lack enough proof, when regression risk is rising, or when the user explicitly asks for stronger tests.
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Agent
user-invocable: false
---

## Trigger

Activate when:
- The orchestrator identifies insufficient test coverage
- A feature or fix was implemented without adequate proof
- The user explicitly asks for more tests
- Regression risk is accumulating

## Procedure

### 1. Assess current state
- Read existing tests for the target area
- Identify what behaviors are currently protected
- Identify gaps: missing happy paths, edge cases, error paths, boundary conditions

### 2. Prioritize test additions
Order of priority:
1. **Regression tests** for recent bug fixes (mandatory)
2. **Acceptance tests** aligned with key journeys from manifest
3. **Edge cases** implied by domain rules (from `docs/constraints/`)
4. **Error paths** that could silently fail
5. **Boundary conditions** for inputs, state transitions, permissions

### 3. Write tests
Delegate to `harness:test-engineer` with:
```
Handoff:
  from: test-expansion
  scope: <target files/behaviors>
  findings: <gaps identified, existing patterns observed>
  constraints: <domain rules that imply edge cases>
  next_action: Write tests for the listed behaviors; match existing test style and tooling
```

Principles:
- Favor deterministic tests over flaky coverage
- Match existing test style and tooling
- One test should prove one behavior
- Name tests after what they prove, not how they work

### 4. Validate
- Run all new tests — they must pass
- Run existing tests — no regressions
- Check for flaky indicators: timing, order-dependence, external state

### 5. Report
- Tests added and what each proves
- Remaining coverage gaps (be honest)
- Any flaky risk identified
- Suggestions for future test priorities

## Guardrails

- Do not write tests that duplicate existing coverage
- Do not write brittle tests that will break on irrelevant changes
- Do not chase coverage numbers — chase behavior confidence
- Prefer project-native test tooling over introducing new frameworks
