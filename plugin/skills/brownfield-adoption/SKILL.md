---
name: brownfield-adoption
description: Maps unfamiliar code, identifies risk zones, installs minimal safety nets, and documents verified findings before risky edits in brownfield areas.
allowed-tools: Read, Glob, Grep, Bash, Agent
user-invocable: false
---

## Trigger

Activate when:
- The target code area has no documentation
- Tests are missing or insufficient for the area
- The code was written by someone else and is unfamiliar
- Risk of breaking unknown dependencies is nontrivial
- The orchestrator explicitly flags brownfield risk

## Procedure

### 1. Inventory
Delegate to `harness:brownfield-mapper` to produce:
- Entry points and exit points
- Data flow through the area
- Side effects (DB writes, API calls, file I/O, message publishing)
- Key dependencies (what this code depends on, what depends on this code)
- Existing tests and validation
- Ownership clues (git blame, code comments, naming patterns)

### 2. Protect critical flows
Before making any changes:
- Identify the 1-3 most critical behaviors in the area
- Verify existing tests cover them
- If not, delegate to `harness:test-engineer` to add characterization tests
- These tests protect against unknown regressions

### 3. Document findings
Create or update:
- `harness/docs/brownfield/inventory.md` — structural map of the area
- `harness/docs/brownfield/findings.md` — verified facts and observations
- `harness/state/unknowns.md` — things we don't know yet

Separate clearly:
- **Verified**: confirmed by code reading or test execution
- **Inferred**: plausible but not proven
- **Unknown**: cannot determine without more investigation or user input

### 4. Identify risk zones
Flag areas that need extra caution:
- Implicit business rules embedded in code
- State mutations with unclear scope
- External integrations with unclear contracts
- Performance-sensitive paths
- Security-sensitive paths

Add new risk zones to `harness/manifest.yaml` if significant.

### 5. Install minimal safety nets
Before proceeding with the actual work:
- Characterization tests for critical paths
- Smoke test commands if missing
- Contract checks for external boundaries

### 6. Handoff
Return to the requesting workflow with:
```
Result:
  from: brownfield-adoption
  scope: <files and domains mapped>
  changes: <characterization tests added, docs created>
  findings: <scope_map, risk_assessment, safety_nets installed>
  validation: <which critical flows are now test-protected>
  unknowns: <what could not be determined>
```

## Guardrails

- Do not pretend to understand what you have not verified
- Do not skip the protection step for critical flows
- Mark all inferred knowledge explicitly as inferred
- The goal is enough understanding to work safely, not perfect documentation
- Prefer asking "what does this do?" over guessing
