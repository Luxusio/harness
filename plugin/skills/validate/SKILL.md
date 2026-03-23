---
name: validate
description: Checks the harness control plane for missing files, dangling references, stale placeholders, permissions issues, and drift between expected and actual repo-local state.
argument-hint: ""
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

Validate the harness control plane in the current repository.

## Procedure

### 1. Check required files exist

Verify each file exists and report PASS/FAIL:

| File | Required |
|------|----------|
| `harness/manifest.yaml` | yes |
| `harness/router.yaml` | yes |
| `harness/policies/approvals.yaml` | yes |
| `harness/policies/memory-policy.yaml` | yes |
| `harness/state/recent-decisions.md` | yes |
| `harness/state/unknowns.md` | yes |
| `harness/state/current-task.yaml` | yes |
| `harness/state/last-session-summary.md` | yes |
| `harness/state/recent-decisions-archive.md` | yes |
| `CLAUDE.md` | yes |
| `harness/docs/index.md` | yes |
| `harness/docs/constraints/project-constraints.md` | yes |
| `harness/docs/decisions/ADR-0001-harness-bootstrap.md` | yes |
| `harness/docs/domains/README.md` | yes |
| `harness/docs/architecture/README.md` | yes |
| `harness/docs/requirements/README.md` | yes |
| `harness/docs/runbooks/development.md` | yes |
| `harness/scripts/validate.sh` | yes |
| `harness/scripts/smoke.sh` | yes |
| `harness/scripts/arch-check.sh` | yes |
| `harness/scripts/check-approvals.sh` | yes |

For each: `[PASS] <file> exists` or `[FAIL] <file> missing`

### 2. Check scripts are executable

For each script in `harness/scripts/`:
- Check if file has execute permission
- `[PASS] <script> is executable` or `[WARN] <script> is not executable`

### 3. Check referential integrity

**approvals.yaml paths:**
- Read `harness/policies/approvals.yaml`
- For each rule, determine whether it is path-based (has a `paths:` field) or action-based (has only an `actions:` field):
  - **Path-based rules** (`always_ask_before` items with `paths:`): check if each glob pattern matches at least one existing file or directory. `[PASS] approvals path <glob> has matches` or `[WARN] approvals path <glob> has no matches on disk`
  - **Action-based rules** (`always_ask_before` items with only `actions:`, no `paths:`): do NOT warn about missing paths. Instead, verify that `reason` exists and that `min_files` (if present) is numeric. `[PASS] action-based rule <name> is valid` or `[WARN] action-based rule <name> missing reason field`

**harness/docs/index.md references:**
- Read `harness/docs/index.md`
- For each file path listed, check if it exists
- `[PASS] index entry <path> exists` or `[FAIL] index entry <path> is dangling`

**manifest.yaml references:**
- Read `harness/manifest.yaml`
- Check that `memory.system_of_record` paths exist
- `[PASS] manifest path <path> exists` or `[WARN] manifest path <path> not found`

**requirements/ status validation:**
- Read all `harness/docs/requirements/REQ-*.md` files
- For each, verify the `**Status:**` line contains one of: `draft`, `accepted`, `implemented`, `verified`
- `[PASS] <file> has valid status: <status>` or `[WARN] <file> has invalid status: <value>`

### 4. Check for stale placeholders

Search all files under `harness/`, `CLAUDE.md` for `{{` patterns:
- `[PASS] no unresolved placeholders` or `[FAIL] <file> contains unresolved placeholder: {{NAME}}`

### 5. Summary

Output:
```
=== harness validate summary ===
Passed: X
Warnings: Y
Failures: Z
```

If Z > 0, suggest running `/harness:setup` to repair missing files.
If Y > 0, list the warnings with suggested actions.

## Notes

- This is a diagnostic command. It reports only; it does not mutate files.
- Run after `/harness:setup` to verify the control plane, or anytime files may have drifted.
- Normal feature/bugfix/refactor work should not auto-route into this skill.
- The `--fix` flag is reserved for future use and currently has no effect.
