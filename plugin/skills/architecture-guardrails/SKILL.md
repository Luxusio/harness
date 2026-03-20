---
name: architecture-guardrails
description: Use when editing structure, moving code, introducing dependencies, or reasoning about boundaries.
allowed-tools: Read, Glob, Grep, Write, Edit
user-invocable: false
---

## Trigger

Activate when:
- Code is being moved between modules or layers
- New dependencies are being introduced
- Import direction might violate boundaries
- A refactoring touches structural patterns
- The orchestrator needs to verify boundary compliance

## Procedure

### 1. Load current architecture model
- Read `harness/docs/architecture/` if it exists
- Read `harness/manifest.yaml` for project structure hints
- Read `harness/docs/constraints/project-constraints.md` for confirmed rules
- Check `harness/state/unknowns.md` for unresolved architecture questions

### 2. Identify the boundaries at play
For the current change, determine:
- Which layers or modules are involved
- What is the expected dependency direction
- Are there confirmed boundary rules, or only inferred patterns

### 3. Check compliance
- Verify imports follow the established direction
- Verify no new cross-boundary coupling is introduced without cause
- Verify naming follows domain conventions
- Verify the change does not create circular dependencies

### 4. Flag violations
If a violation is found:
- Is it in a confirmed rule? → must fix before proceeding
- Is it in an inferred pattern? → flag as risk, suggest fix, but don't block
- Is the boundary itself wrong? → propose updating the architecture model

### 5. Record findings
- New confirmed boundary rules → `harness/docs/architecture/` + `decision-capture`
- New hypotheses → `harness/state/unknowns.md`
- Violations found and fixed → note in recent decisions

### 6. Suggest enforcement
When a boundary rule is confirmed, propose:
1. Import restriction (lint rule if available)
2. Test that catches boundary violations
3. `harness/scripts/arch-check.sh` addition
4. Documentation (last resort)

If a boundary rule is confirmed:
1. Append it to `harness/arch-rules.yaml` in the defined boundary format
2. Run `harness/scripts/arch-check.sh` to verify the rule catches existing violations
3. If a native lint rule can enforce it (e.g., ESLint `no-restricted-imports`), prefer adding the native rule and note that arch-rules.yaml is the fallback

## Principles

- Explicit dependency direction over implicit coupling
- Small interfaces over convenience leaks
- Clear ownership over shared everything
- Domain-aligned naming over technical naming
- Tests around boundaries, not just inside modules

## Guardrails

- Do not convert an inferred architecture into a confirmed rule without evidence or user confirmation
- Do not block work for a hypothetical violation
- When boundaries are missing, infer a minimal model for the current task only
- Do not over-architect — only enforce what has proven value
