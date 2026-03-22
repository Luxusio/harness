---
name: validate
description: Check repo-os control plane health. Reports missing files, dangling references, stale placeholders, and consistency issues.
argument-hint: ""
allowed-tools: Read, Glob, Grep, Bash
---

Validate the repo-os control plane in the current repository.

## Procedure

### 1. Check required files exist

Verify each file exists and report PASS/FAIL:

| File | Required |
|------|----------|
| `.claude-harness/manifest.yaml` | yes |
| `.claude-harness/router.yaml` | yes |
| `.claude-harness/policies/approvals.yaml` | yes |
| `.claude-harness/policies/memory-policy.yaml` | yes |
| `.claude-harness/state/recent-decisions.md` | yes |
| `.claude-harness/state/unknowns.md` | yes |
| `.claude-harness/state/current-task.yaml` | yes |
| `.claude-harness/state/last-session-summary.md` | yes |
| `.claude-harness/state/recent-decisions-archive.md` | yes |
| `CLAUDE.md` | yes |
| `docs/index.md` | yes |
| `docs/constraints/project-constraints.md` | yes |
| `docs/decisions/ADR-0001-repo-os-bootstrap.md` | yes |
| `docs/domains/README.md` | yes |
| `docs/architecture/README.md` | yes |
| `docs/runbooks/development.md` | yes |
| `scripts/agent/validate.sh` | yes |
| `scripts/agent/smoke.sh` | yes |
| `scripts/agent/arch-check.sh` | yes |

For each: `[PASS] <file> exists` or `[FAIL] <file> missing`

### 2. Check scripts are executable

For each script in `scripts/agent/`:
- Check if file has execute permission
- `[PASS] <script> is executable` or `[WARN] <script> is not executable`

### 3. Check referential integrity

**approvals.yaml paths:**
- Read `.claude-harness/policies/approvals.yaml`
- For each `paths:` entry, check if the glob pattern matches at least one existing file or directory
- `[PASS] approvals path <glob> has matches` or `[WARN] approvals path <glob> has no matches on disk`

**docs/index.md references:**
- Read `docs/index.md`
- For each file path listed, check if it exists
- `[PASS] index entry <path> exists` or `[FAIL] index entry <path> is dangling`

**manifest.yaml references:**
- Read `.claude-harness/manifest.yaml`
- Check that `memory.system_of_record` paths exist
- `[PASS] manifest path <path> exists` or `[WARN] manifest path <path> not found`

### 4. Check for stale placeholders

Search all files under `.claude-harness/`, `docs/`, `CLAUDE.md`, and `scripts/` for `{{` patterns:
- `[PASS] no unresolved placeholders` or `[FAIL] <file> contains unresolved placeholder: {{NAME}}`

### 5. Check workflow summaries

Verify all 10 workflow summaries exist in `.claude-harness/workflows/`:
- feature.md, bugfix.md, tests.md, refactor.md, brownfield-adoption.md
- decision-capture.md, docs-sync.md, validation-loop.md, architecture-guardrails.md, repo-memory-policy.md

For each: `[PASS] workflow <name> exists` or `[FAIL] workflow <name> missing`

### 6. Summary

Output:
```
=== repo-os validate summary ===
Passed: X
Warnings: Y
Failures: Z
```

If Z > 0, suggest running `/repo-os:setup` to repair missing files.
If Y > 0, list the warnings with suggested actions.

## Notes

- This skill does NOT auto-fix. It reports only.
- Run after `/repo-os:setup` to verify the control plane, or anytime files may have drifted.
- The `--fix` flag is reserved for future use and currently has no effect.
