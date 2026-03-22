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

### 1. Scope clarification and requirement capture
- If the request is ambiguous, delegate to `harness:requirements-curator` to produce scope, criteria, and non-goals.
- The curator persists these as `harness/docs/requirements/REQ-NNNN-<slug>.md` with status `draft`.
- The curator checks for conflicts against all existing `accepted`/`implemented` requirements.
  - If conflicts are found: present them to the user and wait for resolution before proceeding.
  - If no conflicts: the curator sets status to `accepted`.
- If the request is clear enough, create the REQ file directly with status `accepted` (skip the curator), but still run the conflict check.
- **Do NOT proceed to Step 2 until the REQ file has status `accepted` (conflict-free).**
- Pass the REQ file path forward as context for subsequent steps.

### 2. Context loading
Read the smallest relevant set:
- `harness/manifest.yaml` (project shape, commands, risk zones)
- `harness/policies/approvals.yaml` (what needs confirmation)
- Relevant `harness/docs/domains/` files for the target area
- Relevant `harness/docs/constraints/` for rules that apply
- `harness/state/recent-decisions.md` for recent context

### 3. Brownfield check
If the target area has no documentation or tests, delegate to `harness:brownfield-mapper` with:
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
Delegate to `harness:implementation-engineer` with:
- Clear scope and acceptance criteria (from the REQ file)
- REQ file path for reference
- Relevant context files
- Specific constraints from approvals/policies
The engineer makes the smallest coherent diff.

If the feature involves UI/frontend work, also delegate to `oh-my-claudecode:designer` for design-quality implementation.

### 6. Test coverage
Delegate to `harness:test-engineer` to:
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

For security-sensitive changes (auth, payment, user-input), delegate to `oh-my-claudecode:security-reviewer` before completing.
For complex changes (>10 files), delegate to `oh-my-claudecode:code-reviewer` for a quality check.

### 8. Knowledge sync
If durable knowledge changed, pass to `harness:docs-sync` with:
```
Handoff:
  from: feature-workflow
  scope: <changed files>
  findings: <new decisions, constraints, or facts discovered>
  constraints: <which rules were applied>
  next_action: Update relevant docs and recent-decisions.md
```
If a durable rule was confirmed during this work, also pass to `harness:decision-capture` with the rule text, type, and evidence.

Update the REQ file status based on workflow progress:
- After implementation completes (Step 5): set status to `implemented`, append history entry
- After validation passes (Step 7): set status to `verified`, tick acceptance criteria checkboxes, append history entry

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
