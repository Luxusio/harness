---
name: feature-workflow
description: Use when the user wants new functionality, a new endpoint, a new screen, or a nontrivial behavior addition.
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Agent
user-invocable: false
---

## Trigger

Activate when the orchestrator classifies intent as `feature` — the user wants something new built, added, or created.

## Inputs

- User request (natural language)
- Scoped context from orchestrator (manifest, relevant domain docs, approvals)

## Procedure

### 1. Scope clarification
- If the request is ambiguous, delegate to `requirements-curator` to produce:
  - requested outcome
  - non-goals
  - acceptance criteria
  - risk flags
- If the request is clear enough, skip this step.

### 2. Context loading
Read the smallest relevant set:
- `harness/manifest.yaml` (project shape, commands, risk zones)
- `harness/policies/approvals.yaml` (what needs confirmation)
- Relevant `harness/docs/domains/` files for the target area
- Relevant `harness/docs/constraints/` for rules that apply
- `harness/state/recent-decisions.md` for recent context

### 3. Brownfield check
If the target area has no documentation or tests, delegate to `brownfield-mapper` with:
```
Handoff:
  from: feature-workflow
  scope: <target files/directories>
  findings: <what is known so far>
  constraints: <relevant approval rules>
  next_action: Map the area and return scope_map, risk_assessment, safety_nets, unknowns
```
Incorporate the mapper's returned findings before proceeding to Step 4.

### 4. Risk gate
Check approvals policy. If the feature touches any `always_ask_before` zones:
- auth, db_schema, public_contract, infra, dependency_upgrade, large_delete_or_move
- **Stop and ask the user for confirmation** before proceeding.
Also ask if:
- Requirements interpretation is ambiguous
- Blast radius is unclear
- An existing rule might conflict

### 5. Implementation
Delegate to `implementation-engineer` with:
- Clear scope and acceptance criteria
- Relevant context files
- Specific constraints from approvals/policies
The engineer makes the smallest coherent diff.

### 6. Test coverage
Delegate to `test-engineer` to:
- Add tests that prove the new behavior works
- Add edge case coverage if domain rules suggest it
- Ensure regression safety

### 7. Validation
Run the narrowest validation that proves the change:
1. lint / typecheck (if available)
2. related unit tests
3. integration / smoke tests (if relevant)
4. browser validation (for UI changes, if tooling available)
If validation cannot be completed, state the exact gap.

### 8. Knowledge sync
If durable knowledge changed, pass to `docs-sync` with:
```
Handoff:
  from: feature-workflow
  scope: <changed files>
  findings: <new decisions, constraints, or facts discovered>
  constraints: <which rules were applied>
  next_action: Update relevant docs and recent-decisions.md
```
If a durable rule was confirmed during this work, also pass to `decision-capture` with the rule text, type, and evidence.

### 9. Summary
Report:
- What was built
- What was validated (with evidence)
- What was recorded in repo memory
- What remains unknown or needs follow-up

## Guardrails

- Do not invent architecture without cause
- Do not skip the risk gate for high-risk zones
- Do not claim completion without validation evidence
- Do not store hypotheses as confirmed facts
- Prefer the smallest coherent change over a comprehensive rewrite
