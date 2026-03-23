---
name: refactor-workflow
description: Defines the preservation contract, adds characterization coverage when needed, coordinates behavior-preserving structural cleanup, and validates that external behavior did not change.
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
If boundaries are unclear, delegate to `harness:brownfield-mapper` first to establish scope before this gate.

Evaluate the planned refactor action and planned paths using `harness/scripts/check-approvals.sh` or equivalent deterministic logic against `harness/policies/approvals.yaml`. If any rule matches → stop and ask the user for confirmation before proceeding. Large delete/move structural refactors naturally connect to action-based rules in `approvals.yaml`.

Also check the `ask_when` situational flags in `approvals.yaml`:
- `requirements_ambiguous`: the preservation contract is unclear
- `blast_radius_unknown`: the full scope of impact cannot be determined
- `existing_rule_conflicts`: an existing confirmed rule might conflict with the structural change

If any `ask_when` condition applies → stop and ask the user before proceeding.

If the refactoring scope is large, break it into phases and confirm the plan with the user before beginning.

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

After docs sync, rebuild the compiled memory index:
1. Run `bash harness/scripts/build-memory-index.sh`
2. Run `bash harness/scripts/check-memory-index.sh` to verify consistency

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
