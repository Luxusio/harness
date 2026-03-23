---
name: decision-capture
description: Classifies confirmed project rules, decisions, approval gates, and observed facts, then records them in the correct durable location and encodes them as executable constraints when possible.
allowed-tools: Read, Glob, Grep, Write, Edit, Agent
user-invocable: false
---

## Trigger

Activate when:
- The user states a lasting rule: "from now on...", "always...", "never..."
- A significant implementation choice was made that affects future work
- An approval boundary was defined or changed
- A previously ambiguous area was clarified with a firm decision

## Procedure

### 1. Classify the decision
Determine what kind of memory this is:
- **constraint**: an ongoing rule (`harness/docs/constraints/`)
- **decision**: a specific choice with rationale (`harness/docs/decisions/` as ADR)
- **approval_rule**: a gate that requires user confirmation (`harness/policies/approvals.yaml`)
- **observed_fact**: verified technical finding (`harness/docs/runbooks/` or `harness/docs/architecture/`)
- **risk_zone**: a new area requiring caution (manifest `risk_zones`)

### 2. Verify authority
- Was this explicitly stated by the user? → can auto-record
- Was this confirmed by the user in response to a question? → can auto-record
- Is this an interpretation of something ambiguous? → ask before recording
- Is this inferred from code/tests? → record as `observed_fact`, not `confirmed`

### 3. Record in the right place
Delegate to `harness:docs-scribe` to:

**For constraints:**
- Append to `harness/docs/constraints/project-constraints.md`
- Keep the entry short: rule, scope, reason

**For decisions (ADR):**
- Create `harness/docs/decisions/ADR-NNNN-<slug>.md` with:
  - Status: accepted
  - Context: why this decision was needed
  - Decision: what was chosen
  - Consequences: what this means for future work

**For approval rules:**
Add to `harness/policies/approvals.yaml` using one of these concrete rule shapes:

- **Path-based rule** — triggers when planned paths match:
  ```yaml
  kind: path
  paths: ["<glob-pattern>"]
  reason: "<why confirmation is required>"
  ```

- **Action-based rule** — triggers when planned action matches (optionally scoped by file count):
  ```yaml
  kind: action
  actions: ["<action-name>"]          # e.g. delete, move, schema_change
  min_files: <N>                       # optional: only trigger when N or more files affected
  reason: "<why confirmation is required>"
  ```

- **Situational gate** — triggers when a context condition applies:
  Set the relevant `ask_when` boolean flag in `approvals.yaml` (e.g. `requirements_ambiguous: true`, `blast_radius_unknown: true`, `existing_rule_conflicts: true`).

Do NOT reference manifest approval defaults — `approvals.yaml` is the sole source for approval rules.

**For observed facts:**
- Add to the relevant `harness/docs/runbooks/` or `harness/docs/architecture/` file

### 4. Update recent decisions
Append a one-line entry to `harness/state/recent-decisions.md`:
```
- [YYYY-MM-DD] <type>: <short description>
```
For `approval_rule` type, include the rule shape in the description, e.g.:
```
- [YYYY-MM-DD] approval_rule: added path-based rule for src/auth/** (reason: auth changes require confirmation)
- [YYYY-MM-DD] approval_rule: added action-based rule for delete/move actions min_files=5 (reason: large structural moves need review)
- [YYYY-MM-DD] approval_rule: set ask_when.blast_radius_unknown=true (reason: deployment scope unclear)
```

### 5. Encode as executable when possible
Prefer this order of enforcement:
1. Test that fails if the rule is violated
2. Validation script check
3. Config assertion
4. Documentation only (last resort)

If a test or script can enforce the rule, create it.

### 6. Summary
Report:
- What was captured
- Where it was recorded
- Whether it was encoded as executable constraint
- Confidence level: user-confirmed vs inferred

## Guardrails

- Never store a policy interpretation without user confirmation or explicit repo evidence
- Never upgrade a hypothesis to a confirmed rule without evidence
- Never duplicate an existing decision — update instead
- Keep entries concise and actionable
