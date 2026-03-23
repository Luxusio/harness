---
name: feature-workflow
description: Captures requirements, checks conflicts and approvals, coordinates implementation and tests, and syncs durable docs for new features, endpoints, UI flows, or behavior additions.
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
- Delegate to `harness:requirements-curator` to produce scope, criteria, and non-goals. This is mandatory regardless of how clear the request appears.
- Only `harness:requirements-curator` creates REQ files, assigns REQ numbers, manages the draft→accepted transition, and performs conflict checks. Do not create REQ files directly in this workflow.
- The curator persists these as `harness/docs/requirements/REQ-NNNN-<slug>.md` with status `draft`.
- The curator checks for conflicts against all existing `accepted`/`implemented` requirements.
  - If conflicts are found: present them to the user and wait for resolution before proceeding.
  - If no conflicts: the curator sets status to `accepted`.
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
Evaluate the planned action and planned paths using `harness/scripts/check-approvals.sh` or equivalent deterministic logic against `harness/policies/approvals.yaml`. If any rule matches → stop and ask the user for confirmation before proceeding.

Also check the `ask_when` situational flags in `approvals.yaml`:
- `requirements_ambiguous`: requirements interpretation is unclear
- `blast_radius_unknown`: scope of impact cannot be determined
- `existing_rule_conflicts`: an existing confirmed rule might conflict

If any `ask_when` condition applies → stop and ask the user before proceeding.

### 5. Implementation
Delegate to `harness:implementation-engineer` with:
- Clear scope and acceptance criteria (from the REQ file)
- REQ file path for reference
- Relevant context files
- Specific constraints from approvals/policies
The engineer makes the smallest coherent diff.

If the feature involves UI/frontend work, delegate to `oh-my-claudecode:designer` for design-quality implementation if it is available in the current session. Otherwise continue with the harness implementation flow and report that the optional design capability was unavailable.

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

For security-sensitive changes (auth, payment, user-input), delegate to `oh-my-claudecode:security-reviewer` before completing if it is available in the current session. Otherwise continue with harness flow and report that the optional security review capability was unavailable.
For complex changes (>10 files), delegate to `oh-my-claudecode:code-reviewer` for a quality check if it is available in the current session. Otherwise continue with harness flow and report that the optional code review capability was unavailable.

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
Note: `harness:docs-sync` (docs-sync) does NOT modify REQ file status. REQ status transitions are owned exclusively by this workflow (see below).

After docs sync completes, rebuild the compiled memory index:
1. Run `bash harness/scripts/build-memory-index.sh`
2. Run `bash harness/scripts/check-memory-index.sh` to verify consistency

If a durable rule was confirmed during this work, also pass to `harness:decision-capture` with the rule text, type, and evidence.

**REQ status transitions (this workflow's responsibility):**
- After implementation completes (Step 5): set REQ status to `implemented`, append history entry
- After validation passes (Step 7): set REQ status to `verified`, tick acceptance criteria checkboxes, append history entry

These status transitions and history appends are performed by this workflow only. No other skill or agent modifies REQ status after `accepted`.

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
