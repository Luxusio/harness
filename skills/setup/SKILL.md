---
name: setup
description: Bootstrap repo-local memory, workflow routing, approvals, validation stubs, and brownfield discovery for this repository.
argument-hint: [optional focus]
disable-model-invocation: true
allowed-tools: AskUserQuestion, Read, Glob, Grep, Write, Edit, Bash
---

You are initializing the repo-local operating system in the current repository.

This is the only user-invocable command in this plugin. After setup, the user should work in plain language without invoking more plugin commands.

Optional setup focus from the user: `$ARGUMENTS`

## Placeholder Reference

| Placeholder | Description | Fallback Default | Type |
|-------------|-------------|-----------------|------|
| `{{PROJECT_NAME}}` | Repository or directory name | Directory basename | single |
| `{{PROJECT_MODE}}` | greenfield or brownfield | Detected automatically | single |
| `{{PROJECT_TYPE}}` | web-app, api-service, worker, library, monorepo, other | other | single |
| `{{LANGUAGE_1}}` | Primary language | Detected from config files | single |
| `{{FRAMEWORK_1}}` | Primary framework | Detected from config files | single |
| `{{PACKAGE_MANAGER}}` | Package manager | Detected from lockfiles | single |
| `{{DEV_COMMAND}}` | Dev server command | (none) | single |
| `{{BUILD_COMMAND}}` | Build command | (none) | single |
| `{{TEST_COMMAND}}` | Test runner command | (none) | single |
| `{{LINT_COMMAND}}` | Lint/typecheck command | (none) | single |
| `{{KEY_JOURNEY_1}}` | First key user journey | (ask user) | single |
| `{{KEY_JOURNEY_2}}` | Second key user journey | (ask user) | single |
| `{{KEY_JOURNEYS_BULLETS}}` | All key journeys as bullet list | Derived from KEY_JOURNEY_* | **multi-value** |
| `{{RISK_PATH_1}}` | First high-risk directory | Detected from project structure | single |
| `{{RISK_REASON_1}}` | Reason for first risk zone | Detected from project structure | single |
| `{{RISK_PATH_2}}` | Second high-risk directory | Detected from project structure | single |
| `{{RISK_REASON_2}}` | Reason for second risk zone | Detected from project structure | single |
| `{{SETUP_DATE}}` | Date of setup run | Current date (YYYY-MM-DD) | single |
| `{{BROWNFIELD_SECTION}}` | Conditional guidance block based on detected project mode | Replaced per greenfield/brownfield rules below | **conditional** |

> **Multi-value placeholders** expand to multiple lines (e.g., a bulleted list). Single-value placeholders are replaced with a single string. **Conditional placeholders** are replaced with a full prose block chosen by project mode.
>
> **`{{BROWNFIELD_SECTION}}` replacement rules:**
> - **Greenfield**: replace with `"This is a greenfield project. Consider creating \`src/\`, \`tests/\`, \`docs/\` as the codebase grows. See \`docs/architecture/README.md\` for scaffold hints."`
> - **Brownfield**: replace with `"Always check \`docs/brownfield/inventory.md\` before editing unfamiliar areas. Risk zones are documented in \`docs/brownfield/findings.md\` and \`.claude-harness/manifest.yaml\`."`
>
> See `skills/setup/inference-application.md` for confidence tiers and signal-to-placeholder mapping.

## Before you create files

Read and adapt these supporting templates:

- [templates/CLAUDE.md](templates/CLAUDE.md)
- [templates/.claude-harness/manifest.yaml](templates/.claude-harness/manifest.yaml)
- [templates/.claude-harness/router.yaml](templates/.claude-harness/router.yaml)
- [templates/.claude-harness/policies/approvals.yaml](templates/.claude-harness/policies/approvals.yaml)
- [templates/.claude-harness/policies/memory-policy.yaml](templates/.claude-harness/policies/memory-policy.yaml)
- [templates/.claude-harness/state/recent-decisions.md](templates/.claude-harness/state/recent-decisions.md)
- [templates/.claude-harness/state/recent-decisions-archive.md](templates/.claude-harness/state/recent-decisions-archive.md)
- [templates/.claude-harness/state/unknowns.md](templates/.claude-harness/state/unknowns.md)
- [templates/.claude-harness/state/current-task.yaml](templates/.claude-harness/state/current-task.yaml)
- [templates/.claude-harness/state/last-session-summary.md](templates/.claude-harness/state/last-session-summary.md)
- [templates/.claude-harness/workflows/feature.md](templates/.claude-harness/workflows/feature.md)
- [templates/.claude-harness/workflows/bugfix.md](templates/.claude-harness/workflows/bugfix.md)
- [templates/.claude-harness/workflows/tests.md](templates/.claude-harness/workflows/tests.md)
- [templates/.claude-harness/workflows/refactor.md](templates/.claude-harness/workflows/refactor.md)
- [templates/.claude-harness/workflows/brownfield-adoption.md](templates/.claude-harness/workflows/brownfield-adoption.md)
- [templates/.claude-harness/workflows/decision-capture.md](templates/.claude-harness/workflows/decision-capture.md)
- [templates/.claude-harness/workflows/docs-sync.md](templates/.claude-harness/workflows/docs-sync.md)
- [templates/.claude-harness/workflows/validation-loop.md](templates/.claude-harness/workflows/validation-loop.md)
- [templates/.claude-harness/workflows/architecture-guardrails.md](templates/.claude-harness/workflows/architecture-guardrails.md)
- [templates/.claude-harness/workflows/repo-memory-policy.md](templates/.claude-harness/workflows/repo-memory-policy.md)
- [templates/docs/index.md](templates/docs/index.md)
- [templates/docs/constraints/project-constraints.md](templates/docs/constraints/project-constraints.md)
- [templates/docs/decisions/ADR-0001-repo-os-bootstrap.md](templates/docs/decisions/ADR-0001-repo-os-bootstrap.md)
- [templates/docs/domains/README.md](templates/docs/domains/README.md)
- [templates/docs/runbooks/development.md](templates/docs/runbooks/development.md)
- [templates/docs/brownfield/inventory.md](templates/docs/brownfield/inventory.md)
- [templates/docs/brownfield/findings.md](templates/docs/brownfield/findings.md)
- [templates/docs/architecture/README.md](templates/docs/architecture/README.md)
- [templates/scripts/agent/validate.sh](templates/scripts/agent/validate.sh)
- [templates/scripts/agent/smoke.sh](templates/scripts/agent/smoke.sh)
- [templates/scripts/agent/arch-check.sh](templates/scripts/agent/arch-check.sh)
- [templates/.claude-harness/arch-rules.yaml](templates/.claude-harness/arch-rules.yaml)

## Goals

Create a repo-local operating layer that lets future Claude work behave consistently:
- remember durable user constraints
- record verified findings from implementation work
- route ordinary language into the right workflow
- keep brownfield work safer
- keep validation and docs sync explicit

## Setup procedure

1. **Check for existing setup**
   - If `.claude-harness/manifest.yaml` already exists, ask whether to:
     - repair missing files
     - upgrade the structure
     - or re-run setup from scratch
   - Never overwrite user-authored files silently.

### Idempotency

If `.claude-harness/manifest.yaml` already exists:
1. Inform the user that repo-os is already initialized
2. Offer two paths:
   - **Overwrite**: re-run setup from scratch, replacing all generated files (preserves `docs/` user content)
   - **Incremental**: only create missing files, never overwrite existing ones
3. Record the choice in `manifest.yaml` as `setup_mode: initial | overwrite | incremental`
4. **Critical**: In ALL modes, never delete files in `docs/` that contain user-written content (content that differs from the original template)

2. **Detect project shape**
   Infer what you can from the repository:
   - greenfield vs brownfield (see detection rules below)
   - project type: web app / api service / worker / library / monorepo / other
   - language(s), framework(s), package manager, and likely build/test commands
   - obvious risk zones such as auth, migrations, api contracts, infra, billing, or deployment

   ### Greenfield / Brownfield Detection

   After the initial project scan, determine the project mode:

   **Greenfield** (empty or near-empty repo):
   - No recognized source files (`.js`, `.ts`, `.py`, `.go`, `.rs`, `.java`, etc.)
   - No build config files (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, etc.)
   - Only contains: README, LICENSE, `.gitignore`, `.git/`, or nothing
   - **Action**: Create full template pack with minimal questions. Write scaffold hints (documentation suggestions only) to `docs/architecture/README.md` — e.g., "Consider creating `src/`, `tests/`, `docs/`". Do NOT create these directories.

   **Brownfield** (existing codebase):
   - Any recognized source or config files present
   - **Action**: Run inventory detection first. Populate `docs/brownfield/inventory.md` with discovered structure. Populate `docs/brownfield/findings.md` with verified observations. Add initial entries to `.claude-harness/state/unknowns.md` for areas that couldn't be fully analyzed.

   Record the decision in `manifest.yaml` as exactly one value: `mode: greenfield` or `mode: brownfield`.

   Both paths produce the same output directory structure:
   - `.claude-harness/` (manifest, router, policies, state, workflows)
   - `docs/` (index, constraints, decisions, domains, runbooks, architecture)
   - `scripts/agent/` (validate, smoke, arch-check)

   Brownfield adds extra populated files; greenfield does NOT omit any standard files.

3. **Ask only the smallest necessary questions**
   Prefer at most 5 setup questions. Ask only what the repo cannot tell you reliably:
   - primary project type if unclear
   - build/test/dev command if unclear
   - top 1-3 key user journeys or critical flows
   - no-touch or high-risk areas
   - which classes of changes must always be confirmed first

4. **Create the repo-local structure**
   Adapt templates to this repository and create:
   - `CLAUDE.md` — include conditional content based on detected mode:
     - **Greenfield**: include getting-started guidance and scaffold hints (reference `docs/architecture/README.md` for suggested directory structure)
     - **Brownfield**: include inventory summary references (link to `docs/brownfield/inventory.md`) and risk zone references (link to `docs/brownfield/findings.md` and highlight `{{RISK_PATH_1}}` / `{{RISK_PATH_2}}`)
   - `.claude-harness/manifest.yaml`
   - `.claude-harness/router.yaml`
   - `.claude-harness/policies/approvals.yaml` — see **Dynamic approvals population** below
   - `.claude-harness/policies/memory-policy.yaml` — copy from template as-is; do NOT skip this file
   - `.claude-harness/state/recent-decisions.md`
   - `.claude-harness/state/unknowns.md`
   - `.claude-harness/workflows/*.md`
   - `docs/index.md` — generated dynamically (see **Generate docs/index.md dynamically** below)
   - `docs/constraints/project-constraints.md`
   - `docs/decisions/ADR-0001-repo-os-bootstrap.md`
   - `docs/domains/README.md`
   - `docs/runbooks/development.md`
   - `scripts/agent/validate.sh`
   - `scripts/agent/smoke.sh`
   - `scripts/agent/arch-check.sh`

   #### Dynamic approvals population

   When creating `.claude-harness/policies/approvals.yaml`, start from the template defaults (auth, db_schema, public_contract, infra, dependency_upgrade, billing_payment) and then prune and populate based on what actually exists in the repository:

   1. **Scan the repo** for directories and files that correspond to each rule's default paths.
   2. **For each approval rule**, replace the template path list with only the paths that exist (or are reasonable globs for existing directories). Use the following mapping as a guide:
      - `auth_change`: check for `auth/`, `src/auth/`, `lib/auth/`, `app/auth/` etc.
      - `db_schema_change`: check for `migrations/`, `db/`, `schema/`, `prisma/`, `alembic/`
      - `public_contract_change`: check for `api/`, `contracts/`, `openapi/`, `graphql/`, `proto/`
      - `infra_change`: check for `.github/`, `infra/`, `terraform/`, `deploy/`, `k8s/`, `.circleci/`
      - `dependency_upgrade`: check for `package.json`, `pnpm-lock.yaml`, `package-lock.json`, `poetry.lock`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `pyproject.toml`
      - `billing_payment_change`: check for `billing/`, `payments/`, `stripe/`, `checkout/`
   3. **Remove any approval rule** whose scanned paths list is empty (i.e., none of the candidate paths exist in the repo). Do not emit rules that reference phantom directories.
   4. **Keep any rule** where at least one path exists or is a file that is present (e.g., `package.json`).
   5. **Log which rules were kept and which were removed** in the finish summary so the user knows what was detected.

5. **Brownfield extras**
   If the repo is brownfield, also create or update:
   - `docs/brownfield/inventory.md`
   - `docs/brownfield/findings.md`
   - initial unknowns in `.claude-harness/state/unknowns.md`

6. **Bootstrap memory**
   Record:
   - explicit user constraints
   - approval rules
   - inferred commands marked as inferred if not confirmed
   - initial risk zones
   - initial key journeys

7. **Generate docs/index.md dynamically**

   After all files have been created, generate `docs/index.md` by listing only the files that were actually written during this setup run. Do not include files that were skipped (e.g., brownfield-only files omitted in greenfield mode).

   Use the following structure, omitting any section that has no files:

   ```markdown
   # Project docs index

   ## Core operating files
   - `CLAUDE.md` — project instructions for Claude
   - `.claude-harness/manifest.yaml` — project shape and commands
   - `.claude-harness/router.yaml` — intent routing configuration
   - `.claude-harness/policies/approvals.yaml` — risk zone approval rules
   - `.claude-harness/policies/memory-policy.yaml` — memory classification rules

   ## State
   - `.claude-harness/state/recent-decisions.md` — chronological decision log
   - `.claude-harness/state/unknowns.md` — open questions and hypotheses

   ## Knowledge
   - `docs/constraints/project-constraints.md` — confirmed project rules
   - `docs/decisions/ADR-0001-repo-os-bootstrap.md` — bootstrap decision record
   - `docs/domains/README.md` — domain knowledge index
   - `docs/architecture/README.md` — architecture boundaries and patterns
   - `docs/runbooks/development.md` — development procedures and debugging notes

   ## Brownfield (if applicable)
   - `docs/brownfield/inventory.md` — structural map of existing code
   - `docs/brownfield/findings.md` — verified observations

   ## Scripts
   - `scripts/agent/validate.sh` — validation checks
   - `scripts/agent/smoke.sh` — smoke tests
   - `scripts/agent/arch-check.sh` — architecture guardrail checks
   ```

   Rules:
   - Only include a bullet if the file was actually created in this run.
   - Remove the "Brownfield (if applicable)" section entirely if the project is greenfield.
   - Remove any other section that ends up with no bullets.
   - Do not reference the template version of `docs/index.md` for content; generate from the actual file list.

8. **Final consistency check**

   Before completing setup, verify:
   1. Every file path listed in `docs/index.md` actually exists on disk.
   2. Every file referenced in `manifest.yaml` exists on disk.
   3. Every risk zone path in `approvals.yaml` references a real directory or file (or is a reasonable glob over an existing directory).
   4. No `{{...}}` placeholders remain in any generated file — scan all created files and replace or remove any unresolved placeholder.
   5. If any dangling reference is found, log a warning in the finish summary and remove it from the file that references it.

9. **Finish cleanly**
   End with:
   - files created or updated
   - which approval rules were kept and which were removed (from dynamic approvals scan)
   - what was inferred vs confirmed
   - remaining unknowns
   - a short reminder that the user can now work in plain language

## Guardrails

- Keep generated files concise and editable by humans.
- Do not fill templates with fake certainty.
- Mark uncertain items as `inferred` or place them in unknowns.
- Prefer repository evidence over assumptions.
