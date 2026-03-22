---
name: refactor-workflow
description: Use when the request is cleanup, simplification, modularization, or dependency untangling without intended behavior changes.
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Agent
user-invocable: false
---

## Trigger

Activate when the orchestrator classifies intent as `refactor` — the user wants structural improvement without behavior change.

## Procedure

### 1. Define preservation contract
Before touching anything:
- Identify the externally observable behaviors that must not change
- Find existing tests that protect these behaviors
- If tests are insufficient, delegate to `harness:test-engineer` with:
  ```
  Handoff:
    from: refactor-workflow
    scope: <files to be refactored>
    findings: <behaviors identified that must be preserved>
    constraints: <no behavior change allowed>
    next_action: Add characterization tests for the listed behaviors before refactoring begins
  ```

### 2. Identify structural pain
- Duplication
- Oversized files or functions
- Mixed responsibilities
- Boundary violations (import direction, layer crossing)
- Naming drift or inconsistency
- Dead code

### 3. Risk gate
- If refactoring touches `always_ask_before` zones, get confirmation
- If the refactoring scope is large, break it into phases and confirm the plan
- If boundaries are unclear, delegate to `harness:brownfield-mapper` first

### 4. Refactor in small steps
Delegate to `harness:refactor-engineer` with:
- Clear preservation contract
- Specific structural improvement goals
- Constraints from policies

Each step should be:
- Independently valid
- Validated before the next step
- Reversible if something goes wrong

### 5. Validate after each meaningful step
- Run existing tests — all must pass
- Run lint/typecheck — no new violations
- Verify the preservation contract holds

### 6. Architecture notes
If boundaries became clearer or structure meaningfully changed, pass to `harness:docs-sync` with:
```
Handoff:
  from: refactor-workflow
  scope: <refactored files/modules>
  findings: <structural insights, boundary clarifications, new architecture rules>
  constraints: <rules that apply>
  next_action: Update docs/architecture/ and record any confirmed structural rules
```

### 7. Summary
Report:
- What structural pain was addressed
- What behaviors were preserved (with evidence)
- What architecture insights emerged
- Any remaining structural debt

## Guardrails

- No hidden behavior changes — if you find a behavior bug during refactoring, report it separately
- No speculative abstraction — only refactor what has proven pain
- Characterize before you change — add tests first if coverage is weak
- Leave the code easier to extend, not just different
