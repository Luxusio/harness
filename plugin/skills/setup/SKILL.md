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
> - **Greenfield**: replace with `"This is a greenfield project. Consider creating \`src/\`, \`tests/\`, \`docs/\` as the codebase grows. See \`harness/docs/architecture/README.md\` for scaffold hints."`
> - **Brownfield**: replace with `"Always check \`harness/docs/brownfield/inventory.md\` before editing unfamiliar areas. Risk zones are documented in \`harness/docs/brownfield/findings.md\` and \`harness/manifest.yaml\`."`
>
> See `skills/setup/inference-application.md` for confidence tiers and signal-to-placeholder mapping.

## Before you create files

Read and adapt these supporting templates:

- [templates/CLAUDE.md](templates/CLAUDE.md)
- [templates/harness/manifest.yaml](templates/harness/manifest.yaml)
- [templates/harness/router.yaml](templates/harness/router.yaml)
- [templates/harness/policies/approvals.yaml](templates/harness/policies/approvals.yaml)
- [templates/harness/policies/memory-policy.yaml](templates/harness/policies/memory-policy.yaml)
- [templates/harness/state/recent-decisions.md](templates/harness/state/recent-decisions.md)
- [templates/harness/state/recent-decisions-archive.md](templates/harness/state/recent-decisions-archive.md)
- [templates/harness/state/unknowns.md](templates/harness/state/unknowns.md)
- [templates/harness/state/current-task.yaml](templates/harness/state/current-task.yaml)
- [templates/harness/state/last-session-summary.md](templates/harness/state/last-session-summary.md)
- [templates/harness/docs/index.md](templates/harness/docs/index.md)
- [templates/harness/docs/constraints/project-constraints.md](templates/harness/docs/constraints/project-constraints.md)
- [templates/harness/docs/decisions/ADR-0001-harness-bootstrap.md](templates/harness/docs/decisions/ADR-0001-harness-bootstrap.md)
- [templates/harness/docs/domains/README.md](templates/harness/docs/domains/README.md)
- [templates/harness/docs/runbooks/development.md](templates/harness/docs/runbooks/development.md)
- [templates/harness/docs/brownfield/inventory.md](templates/harness/docs/brownfield/inventory.md)
- [templates/harness/docs/brownfield/findings.md](templates/harness/docs/brownfield/findings.md)
- [templates/harness/docs/architecture/README.md](templates/harness/docs/architecture/README.md)
- [templates/harness/docs/requirements/README.md](templates/harness/docs/requirements/README.md)
- [templates/harness/docs/requirements/REQ-0000-template.md](templates/harness/docs/requirements/REQ-0000-template.md)
- [templates/harness/scripts/validate.sh](templates/harness/scripts/validate.sh)
- [templates/harness/scripts/smoke.sh](templates/harness/scripts/smoke.sh)
- [templates/harness/scripts/arch-check.sh](templates/harness/scripts/arch-check.sh)
- [templates/harness/arch-rules.yaml](templates/harness/arch-rules.yaml)

## Goals

Create a repo-local operating layer that lets future Claude work behave consistently:
- remember durable user constraints
- record verified findings from implementation work
- route ordinary language into the right workflow
- keep brownfield work safer
- keep validation and docs sync explicit

## Setup procedure

1. **Check for existing setup**
   - If `harness/manifest.yaml` already exists, ask whether to:
     - repair missing files
     - upgrade the structure
     - or re-run setup from scratch
   - Never overwrite user-authored files silently.

### Idempotency

If `harness/manifest.yaml` already exists:
1. Inform the user that harness is already initialized
2. Offer two paths:
   - **Overwrite**: re-run setup from scratch, replacing all generated files (preserves `harness/docs/` user content)
   - **Incremental**: only create missing files, never overwrite existing ones
3. Record the choice in `manifest.yaml` as `setup_mode: initial | overwrite | incremental`
4. **Critical**: In ALL modes, never delete files in `harness/docs/` that contain user-written content (content that differs from the original template)

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
   - **Action**: Create full template pack with minimal questions. Write scaffold hints (documentation suggestions only) to `harness/docs/architecture/README.md` — e.g., "Consider creating `src/`, `tests/`, `docs/`". Do NOT create these directories.

   **Brownfield** (existing codebase):
   - Any recognized source or config files present
   - **Action**: Run inventory detection first. Populate `harness/docs/brownfield/inventory.md` with discovered structure. Populate `harness/docs/brownfield/findings.md` with verified observations. Add initial entries to `harness/state/unknowns.md` for areas that couldn't be fully analyzed.

   Record the decision in `manifest.yaml` as exactly one value: `mode: greenfield` or `mode: brownfield`.

   Both paths produce the same output directory structure:
   - `harness/` (manifest, router, policies, state)
   - `harness/docs/` (index, constraints, decisions, domains, runbooks, architecture)
   - `harness/scripts/` (validate, smoke, arch-check)

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
     - **Greenfield**: include getting-started guidance and scaffold hints (reference `harness/docs/architecture/README.md` for suggested directory structure)
     - **Brownfield**: include inventory summary references (link to `harness/docs/brownfield/inventory.md`) and risk zone references (link to `harness/docs/brownfield/findings.md` and highlight `{{RISK_PATH_1}}` / `{{RISK_PATH_2}}`)
   - `harness/manifest.yaml`
   - `harness/router.yaml`
   - `harness/policies/approvals.yaml` — see **Dynamic approvals population** below
   - `harness/policies/memory-policy.yaml` — copy from template as-is; do NOT skip this file
   - `harness/state/recent-decisions.md`
   - `harness/state/unknowns.md`
   - `harness/state/recent-decisions-archive.md`
   - `harness/state/current-task.yaml`
   - `harness/state/last-session-summary.md`
   - `harness/docs/index.md` — generated dynamically (see **Generate harness/docs/index.md dynamically** below)
   - `harness/docs/constraints/project-constraints.md`
   - `harness/docs/decisions/ADR-0001-harness-bootstrap.md`
   - `harness/docs/domains/README.md`
   - `harness/docs/requirements/README.md`
   - `harness/docs/runbooks/development.md`
   - `harness/scripts/validate.sh`
   - `harness/scripts/smoke.sh`
   - `harness/scripts/arch-check.sh`
   - `harness/arch-rules.yaml`

   #### Dynamic approvals population

   When creating `harness/policies/approvals.yaml`, start from the template defaults (auth, db_schema, public_contract, infra, dependency_upgrade, billing_payment) and then prune and populate based on what actually exists in the repository:

   1. **Scan the repo** for directories and files that correspond to each rule's default paths.
   2. **For each approval rule**, replace the template path list with only the paths that exist (or are reasonable globs for existing directories). Use the following mapping as a guide:
      - `auth_change`: check for `auth/`, `src/auth/`, `lib/auth/`, `app/auth/` etc.
      - `db_schema_change`: check for `migrations/`, `db/`, `schema/`, `prisma/`, `alembic/`; also scan `compose.yaml`/`docker-compose.yaml` for volume mounts to `docker-entrypoint-initdb.d` — the source path contains SQL schema init files that should be protected
      - `public_contract_change`: check for `api/`, `contracts/`, `openapi/`, `graphql/`, `proto/`
      - `infra_change`: check for `.github/`, `infra/`, `terraform/`, `deploy/`, `k8s/`, `.circleci/`
      - `dependency_upgrade`: check for `package.json`, `pnpm-lock.yaml`, `package-lock.json`, `poetry.lock`, `requirements*.txt`, `go.mod`, `Cargo.toml`, `pyproject.toml`
      - `billing_payment_change`: check for `billing/`, `payments/`, `stripe/`, `checkout/`
      - `submodule_change`: check for `.gitmodules` file; if present, parse it to extract submodule paths
      - `env_secrets_change`: check for `.env`, `.env.*`, `.env.local`, `.env.dev`, `.env.prod`, `.env.example` files at root and in service directories
   3. **Remove any approval rule** whose scanned paths list is empty (i.e., none of the candidate paths exist in the repo). Do not emit rules that reference phantom directories.
   4. **Keep any rule** where at least one path exists or is a file that is present (e.g., `package.json`).
   5. **Log which rules were kept and which were removed** in the finish summary so the user knows what was detected.

5. **Brownfield extras**
   If the repo is brownfield, also create or update:
   - `harness/docs/brownfield/inventory.md`
   - `harness/docs/brownfield/findings.md`
   - initial unknowns in `harness/state/unknowns.md`

   Additionally, scan for operational scripts at the repository root and in common script directories:
   - Root-level scripts: `run.sh`, `start.sh`, `deploy.sh`, `Makefile`, `Taskfile.yml`, `justfile`
   - Script directories: `scripts/`, `bin/`, `tools/`

   Also scan for existing documentation directories:
   - `docs/`, `doc/`, `documentation/`
   - Look for infrastructure docs (`infrastructure.md`, `deployment.md`, `architecture.md`), API docs, and runbooks.

   For each discovered documentation file:
   1. Add it to `harness/docs/brownfield/inventory.md` with a summary of its content.
   2. If it contains architecture information (environments, deployment patterns, auth flows), incorporate key facts into `harness/docs/architecture/README.md`.
   3. If it contains operational procedures (setup, troubleshooting, debugging), add reference links to `harness/docs/runbooks/development.md`.
   4. Do NOT duplicate the full content — reference the original file and extract only durable facts that affect how the AI agent should work.

   For each discovered script:
   1. Read and summarize its purpose in `harness/docs/brownfield/inventory.md`.
   2. Document relevant commands in `harness/docs/runbooks/development.md`.
   3. Flag any scripts that modify infrastructure, deploy, or handle secrets as risk zones in `manifest.yaml`.

   #### Detect cross-service dependencies

   In monorepo projects, scan for coupling between services:

   1. **Database coupling via compose.yaml**: Read `compose.yaml`/`docker-compose.yaml` and look for volume mounts from one service into database init directories (e.g., `docker-entrypoint-initdb.d`). This reveals which service owns the DB schema and which services share the same database.
   2. **Shared database pattern**: If service A provides SQL init scripts and service B connects to the same database (via shared environment variables like `DB_HOST`, `POSTGRES_*`), document this in `harness/docs/architecture/README.md` under a "Cross-service dependencies" section:
      - Which service owns the schema
      - Which services read/write to the same DB
      - The risk: schema changes in the owner service can break dependent services
   3. **Add to risk zones**: Add the schema source path to `manifest.yaml` risk zones with reason "DB schema source — changes affect all services sharing this database".
   4. **Architecture doc update**: In `harness/docs/architecture/README.md`, add a "System Boundaries" or "Cross-service Dependencies" section documenting discovered coupling patterns.

   #### Migrate existing domain knowledge

   After brownfield inventory, check for pre-existing knowledge files that contain domain facts:
   - `MEMORY.md`, `AGENTS.md`, `AI_CONTEXT.md`, `.cursorrules`, `.clinerules`
   - Any `docs/` directory with domain-specific documentation
   - **Service-level instruction files** in monorepos: check each service directory for `CLAUDE.md`, `AGENTS.md`, `README.md`, or similar files that contain domain-specific technical facts (frameworks, test strategies, build patterns)

   If found:
   1. Read each file and identify verified domain facts (not hypotheses or preferences).
   2. For each distinct domain area discovered, create a corresponding file in `harness/docs/domains/` (e.g., `data-fetcher.md`, `auth.md`, `payments.md`).
   3. Transfer only factual, verified knowledge — parsing rules, API contracts, architectural patterns, naming conventions, known limitations.
   4. For service-level files (e.g., `services/catchy-api/CLAUDE.md`), create a dedicated domain doc per service (e.g., `harness/docs/domains/catchy-api.md`) and update `manifest.yaml` service entries with confirmed technical details (framework versions, test tools, build commands) replacing any `inferred` markers.
   5. Do NOT transfer: personal preferences, IDE settings, temporary workarounds, or unverified hypotheses (those go to `harness/state/unknowns.md`).
   6. Log which source files were processed and what was migrated in the finish summary.

6. **Bootstrap memory**
   Record:
   - explicit user constraints
   - approval rules
   - inferred commands marked as inferred if not confirmed
   - initial risk zones
   - initial key journeys

7. **Generate harness/docs/index.md dynamically**

   After all files have been created, generate `harness/docs/index.md` by listing only the files that were actually written during this setup run. Do not include files that were skipped (e.g., brownfield-only files omitted in greenfield mode).

   Use the following structure, omitting any section that has no files:

   ```markdown
   # Project docs index

   ## Core operating files
   - `CLAUDE.md` — project instructions for Claude
   - `harness/manifest.yaml` — project shape and commands
   - `harness/router.yaml` — intent routing configuration
   - `harness/policies/approvals.yaml` — risk zone approval rules
   - `harness/policies/memory-policy.yaml` — memory classification rules

   ## State
   - `harness/state/recent-decisions.md` — chronological decision log
   - `harness/state/unknowns.md` — open questions and hypotheses
   - `harness/state/recent-decisions-archive.md` — archived decision log entries

   ## Knowledge
   - `harness/docs/constraints/project-constraints.md` — confirmed project rules
   - `harness/docs/decisions/ADR-0001-harness-bootstrap.md` — bootstrap decision record
   - `harness/docs/domains/README.md` — domain knowledge index
   - `harness/docs/architecture/README.md` — architecture boundaries and patterns
   - `harness/docs/runbooks/development.md` — development procedures and debugging notes

   ## Requirements
   - `harness/docs/requirements/README.md` — requirement specifications index

   ## Brownfield (if applicable)
   - `harness/docs/brownfield/inventory.md` — structural map of existing code
   - `harness/docs/brownfield/findings.md` — verified observations

   ## Scripts
   - `harness/scripts/validate.sh` — validation checks
   - `harness/scripts/smoke.sh` — smoke tests
   - `harness/scripts/arch-check.sh` — architecture guardrail checks
   - `harness/arch-rules.yaml` — architecture rule definitions
   ```

   Rules:
   - Only include a bullet if the file was actually created in this run.
   - Remove the "Brownfield (if applicable)" section entirely if the project is greenfield.
   - Remove any other section that ends up with no bullets.
   - Do not reference the template version of `harness/docs/index.md` for content; generate from the actual file list.
   - If domain knowledge files were created (from the migration step), include them in a "Domain knowledge" sub-section under Knowledge.
   - If brownfield inventory includes operational scripts, reference them in the Brownfield section.

8. **Ensure scripts are executable and use LF line endings**

   After creating all scripts, run:
   ```bash
   sed -i 's/\r$//' harness/scripts/*.sh
   chmod +x harness/scripts/*.sh
   ```
   - `sed` strips any CRLF line endings that may have been introduced by the Write tool on some platforms. Shell scripts with `\r` in the shebang line will fail with cryptic errors.
   - `chmod` ensures the scripts can be invoked directly. The validate skill checks for execute permissions and will report warnings if missing.

9. **Final consistency check**

   Before completing setup, verify:
   1. Every file path listed in `harness/docs/index.md` actually exists on disk.
   2. Every file referenced in `manifest.yaml` exists on disk.
   3. Every risk zone path in `approvals.yaml` references a real directory or file (or is a reasonable glob over an existing directory).
   4. No `{{...}}` placeholders remain in any generated file — scan all created files and replace or remove any unresolved placeholder.
   5. If any dangling reference is found, log a warning in the finish summary and remove it from the file that references it.

10. **Update .gitignore**

   Append the following entries to `.gitignore` if they are not already present:
   ```
   # harness — per-session state (not shared)
   harness/state/current-task.yaml
   harness/state/last-session-summary.md
   ```
   If `.gitignore` does not exist, create it with these entries.

11. **Activate harness-orchestrator as main agent**

   Read `.claude/settings.json` (create if missing). Add or merge the `"agent"` field:
   ```json
   {
     "agent": "harness:harness-orchestrator"
   }
   ```
   Preserve any existing fields in the file (e.g., `enabledPlugins`, `extraKnownMarketplaces`).
   This makes the harness orchestrator the default main-thread agent for all future sessions in this project.

12. **Finish cleanly**
   End with:
   - files created or updated
   - which approval rules were kept and which were removed (from dynamic approvals scan)
   - what was inferred vs confirmed
   - remaining unknowns
   - a short reminder that the user can now work in plain language — the orchestrator is now active

## Guardrails

- Keep generated files concise and editable by humans.
- Do not fill templates with fake certainty.
- Mark uncertain items as `inferred` or place them in unknowns.
- Prefer repository evidence over assumptions.
