---
name: validate
description: Check harness control plane health. Reports missing files, dangling references, stale placeholders, and consistency issues.
argument-hint: ""
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
| `harness/docs/runbooks/development.md` | yes |
| `harness/scripts/validate.sh` | yes |
| `harness/scripts/smoke.sh` | yes |
| `harness/scripts/arch-check.sh` | yes |

For each: `[PASS] <file> exists` or `[FAIL] <file> missing`

### 2. Check scripts are executable

For each script in `harness/scripts/`:
- Check if file has execute permission
- `[PASS] <script> is executable` or `[WARN] <script> is not executable`

### 3. Check referential integrity

**approvals.yaml paths:**
- Read `harness/policies/approvals.yaml`
- For each `paths:` entry, check if the glob pattern matches at least one existing file or directory
- `[PASS] approvals path <glob> has matches` or `[WARN] approvals path <glob> has no matches on disk`

**harness/docs/index.md references:**
- Read `harness/docs/index.md`
- For each file path listed, check if it exists
- `[PASS] index entry <path> exists` or `[FAIL] index entry <path> is dangling`

**manifest.yaml references:**
- Read `harness/manifest.yaml`
- Check that `memory.system_of_record` paths exist
- `[PASS] manifest path <path> exists` or `[WARN] manifest path <path> not found`

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

- This skill does NOT auto-fix. It reports only.
- Run after `/harness:setup` to verify the control plane, or anytime files may have drifted.
- The `--fix` flag is reserved for future use and currently has no effect.
