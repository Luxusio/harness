# Architecture Check Promotion Reference

updated: 2026-03-29

This document describes how architecture constraint checks transition from advisory hints to required evidence in the harness v2.0 runtime critic.

---

## Default Behavior: Hints Only

Architecture constraint checks are **hints by default**. They inform critic playbooks and give the developer awareness of structural boundaries, but:

- Their absence does not cause a FAIL verdict
- Their failure does not cause a FAIL verdict
- No user configuration is required to keep this default behavior

This default applies to all light and standard mode tasks, unconditionally.

---

## Promotion Conditions

An architecture check is **promoted to required evidence** for a runtime PASS only when ALL three conditions are simultaneously true:

| # | Condition | Where to check |
|---|-----------|---------------|
| 1 | `execution_mode` is `sprinted` | `TASK_STATE.yaml` top-level field |
| 2 | `risk_tags` contains at least one of: `structural`, `migration`, `schema`, `cross-root` | `TASK_STATE.yaml` `risk_tags` list |
| 3 | A file matching `doc/harness/constraints/check-architecture.*` exists in the repo | Filesystem check |

If any one of these conditions is false, the check remains a hint and no verdict impact occurs.

---

## What "Promoted" Means

When all three conditions are met, the critic-runtime agent:

1. Locates and executes the `doc/harness/constraints/check-architecture.*` script
2. Captures the full output (stdout + exit code)
3. Includes the output in the evidence bundle under a dedicated `### Architecture Check` section
4. Evaluates the result:
   - Exit 0 → architecture check passes; no additional action needed
   - Non-zero exit → architecture check fails; PASS verdict requires an explicit deviation justification in `CRITIC__runtime.md`

A FAIL verdict is issued if the architecture check fails and no justification is provided.

---

## Script Absence: Skip, Not Fail

If the `doc/harness/constraints/check-architecture.*` file does not exist:

- The architecture check is **skipped entirely**
- No warning is emitted
- No verdict impact
- The evidence bundle section is omitted or marked "skipped"

This is the **expected state for most repos**. The `doc/harness/constraints/` directory is optional and only created during setup when the project has detectable architectural boundaries (see `plugin/skills/setup/SKILL.md` Phase 10).

---

## Light and Standard Mode Tasks: No Change

For tasks with `execution_mode: light` or `execution_mode: standard`:

- Architecture checks are **always hints**, regardless of `risk_tags`
- The promotion logic is never evaluated
- Existing behavior is completely unchanged

---

## Evidence Bundle: Architecture Check Section

When promoted and the script is executed, the evidence bundle in `CRITIC__runtime.md` includes:

```markdown
### Architecture Check
<full script output including exit code>
<"skipped" if script not found or promotion conditions not met>
```

**Example — check passes:**

```markdown
### Architecture Check
Running: doc/harness/constraints/check-architecture.sh
Checking cross-workspace imports... OK
Checking layer boundaries... OK
[EVIDENCE] arch-check: PASS — exit 0 — all constraints satisfied
```

**Example — check fails with justification:**

```markdown
### Architecture Check
Running: doc/harness/constraints/check-architecture.sh
Checking cross-workspace imports... VIOLATION: packages/ui imports from packages/api
[EVIDENCE] arch-check: FAIL — exit 1 — cross-workspace import detected

Deviation justification: The shared type `ApiResponse` was intentionally moved to
packages/ui as part of this migration. The constraint will be updated in a follow-up
task (TASK__update-arch-constraints).
```

**Example — not promoted (hint only):**

```markdown
### Architecture Check
skipped — promotion conditions not met (execution_mode: standard)
```

---

## Integration with TASK_STATE.yaml risk_tags

The `risk_tags` field in `TASK_STATE.yaml` is set by the harness or plan skill during task creation, based on task signals:

```yaml
task_id: TASK__add-billing-module
execution_mode: sprinted
risk_tags:
  - structural
  - schema
  - cross-root
```

Tags that trigger promotion:

| Tag | Meaning |
|-----|---------|
| `structural` | New root directories, major file reorganization, new agent/skill files |
| `migration` | Database migrations, data transformations, schema evolution |
| `schema` | Schema changes (DB tables, API contracts, config formats) |
| `cross-root` | Changes spanning multiple repo roots or workspaces |

Other risk tags (e.g., `auth`, `performance`) do not trigger architecture check promotion.

---

## Summary: When Does This Matter?

| Scenario | Promotion? | Effect |
|----------|-----------|--------|
| Light mode task, any risk_tags | No | Hints only, no verdict impact |
| Standard mode task, any risk_tags | No | Hints only, no verdict impact |
| Sprinted task, no structural risk_tags | No | Hints only, no verdict impact |
| Sprinted task, structural risk_tags, no check script | No | Skip silently, no verdict impact |
| Sprinted task, structural risk_tags, check script present | **Yes** | Required evidence; FAIL if script fails without justification |
