# Inference Application Guide

This file defines what to DO with project signals detected during `/harness:setup`. The model handles detection natively — this file defines confidence tiers, default values, and placeholder fill rules.

## Confidence Tiers

| Tier | Behavior | When to use |
|------|----------|-------------|
| **HIGH** | Write value silently. No user confirmation needed. | Direct evidence from a config file field (e.g., `scripts.test` in `package.json`) |
| **MEDIUM** | Write value, mark with `# inferred:medium`. Inform user but don't block. | Pattern match from project structure (e.g., `src/` exists → `main_source_dir: src/`) |
| **LOW** | Do NOT write. Ask the user first. | Ambiguous signals, multiple conflicting options, or no clear evidence |

### Marking Convention

Inferred values in `manifest.yaml` use inline YAML comments:
```yaml
build_cmd: "npm run build"  # inferred:high
main_source_dir: "src/"     # inferred:medium
# test_cmd: ???              # inferred:low — ask user
```

HIGH-confidence values have no marker (they are effectively confirmed by config file evidence).
MEDIUM values are always marked.
LOW values are not written — they become questions for the user.

## Placeholder-to-Signal Mapping

### Project Identity

| Placeholder | Signal Source | Confidence | Fallback |
|-------------|-------------|-----------|----------|
| `{{PROJECT_NAME}}` | `package.json:name`, `pyproject.toml:name`, `Cargo.toml:name`, directory basename | HIGH (if from config), MEDIUM (if basename) | Directory basename |
| `{{PROJECT_MODE}}` | Presence of source files → brownfield; empty/README-only → greenfield | HIGH | greenfield |
| `{{PROJECT_TYPE}}` | Framework detection (see below) | MEDIUM | "other" |

### Languages and Frameworks

| Placeholder | Signal Source | Confidence | Fallback |
|-------------|-------------|-----------|----------|
| `{{LANGUAGE_1}}` | Config files present (`package.json`→JS/TS, `pyproject.toml`→Python, `Cargo.toml`→Rust, `go.mod`→Go, **`build.gradle`/`build.gradle.kts`→Java/Kotlin**, `pom.xml`→Java) | HIGH | (ask user) |
| `{{FRAMEWORK_1}}` | Dependencies list (e.g., `next` in deps→Next.js, `flask` in deps→Flask, `gin` in deps→Gin) | MEDIUM | (none) |
| `{{PACKAGE_MANAGER}}` | Lockfile present: `package-lock.json`→npm, `yarn.lock`→yarn, `pnpm-lock.yaml`→pnpm, `poetry.lock`→poetry, `Cargo.lock`→cargo, `Cargo.toml`→cargo, **`gradlew`→gradle, `pom.xml`→maven** | HIGH | (ask user if ambiguous) |

### Commands

| Placeholder | Signal Source | Confidence | Fallback |
|-------------|-------------|-----------|----------|
| `{{DEV_COMMAND}}` | `package.json:scripts.dev` or `.start`, `Makefile:dev` target, `pyproject.toml:scripts` | HIGH (if explicit field exists) | (none — leave blank) |
| `{{BUILD_COMMAND}}` | `package.json:scripts.build`, `Makefile:build`, `cargo build`, `go build ./...`, **`build.gradle`/`build.gradle.kts` + `gradlew`→`./gradlew build`, `pom.xml`→`mvn compile`** | HIGH (if explicit or gradlew), MEDIUM (if maven) | (none) |
| `{{TEST_COMMAND}}` | `package.json:scripts.test`, `pytest.ini`/`pyproject.toml:[tool.pytest]`→`pytest`, `Cargo.toml`→`cargo test`, `go.mod`→`go test ./...`, **`gradlew`→`./gradlew test`, `pom.xml`→`mvn test`** | HIGH (if explicit or gradlew), MEDIUM (if maven) | (none) |
| `{{LINT_COMMAND}}` | `package.json:scripts.lint`, `.eslintrc*`→`npx eslint .`, `ruff.toml`/`pyproject.toml:[tool.ruff]`→`ruff check .`, `clippy`→`cargo clippy`, **`build.gradle.kts` + detekt plugin→`./gradlew detekt`, `build.gradle` + checkstyle plugin→`./gradlew checkstyleMain`** | HIGH (if explicit), MEDIUM (if config file exists or plugin detected in build file) | (none) |

### User Journeys and Risk Zones

| Placeholder | Signal Source | Confidence | Fallback |
|-------------|-------------|-----------|----------|
| `{{KEY_JOURNEY_1}}` | Cannot be inferred — always ask user | LOW | (ask user) |
| `{{KEY_JOURNEY_2}}` | Cannot be inferred — always ask user | LOW | (ask user) |
| `{{KEY_JOURNEYS_BULLETS}}` | Derived from confirmed key journeys | — | (empty) |
| `{{RISK_PATH_1}}` | Detected from: `migrations/`, `db/`, `auth/`, `billing/`, `infra/`, `deploy/` | MEDIUM | (none if no risky dirs found) |
| `{{RISK_REASON_1}}` | Derived from path type (e.g., `migrations/` → "Database schema and migration risk") | MEDIUM | (none) |
| `{{RISK_PATH_2}}` | Second detected risk path | MEDIUM | (none) |
| `{{RISK_REASON_2}}` | Derived from path type | MEDIUM | (none) |
| `{{SETUP_DATE}}` | Current date | HIGH | Today's date |

### Approval Signal Detection

These signals are used during dynamic approvals population (not placeholders — they directly affect `approvals.yaml` content):

| Signal | Detection Method | Approval Kind |
|--------|-----------------|---------------|
| Git submodules | `.gitmodules` file exists → parse for submodule paths | `submodule_change` |
| Environment files | `.env*` files at root or in service directories | `env_secrets_change` |
| CI/CD configs | `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile` | `infra_change` |
| Docker configs | `Dockerfile*`, `docker-compose*.yml` | `infra_change` |

## Decision Rules

1. **If a config file has an explicit field** (e.g., `scripts.test` in `package.json`), use it at HIGH confidence.
2. **If a config file exists but the specific field doesn't** (e.g., `package.json` exists but no `scripts.lint`), check for related config files (`.eslintrc*`). If found, infer at MEDIUM.
3. **If multiple conflicting signals exist** (e.g., both `jest.config.js` and `vitest.config.ts`), treat as LOW — ask the user.
4. **If no signal exists**, leave the placeholder blank or use the fallback default. Never guess.
5. **Key journeys are always LOW** — they require domain knowledge that cannot be inferred from files.
6. **If `gradlew` wrapper exists**, always prefer `./gradlew` over system `gradle`. If both `gradlew` and `pom.xml` exist, prefer Gradle (Gradle takes precedence).
7. **`settings.gradle`/`settings.gradle.kts` present** → MEDIUM confidence signal for multi-module Gradle project; treat sub-project structure as present.

## Scope Limitations

- **Monorepo**: If multiple build config files exist at different levels (`package.json`, `build.gradle.kts`, `pyproject.toml`), or workspace config is detected (`workspaces`, `nx.json`, `turbo.json`, `lerna.json`), or `services/`/`packages/`/`apps/` directories contain independent projects:
  - Set `project.type: monorepo` in manifest.
  - Detect each service's language and framework independently.
  - Generate `validate.sh`, `smoke.sh`, and `arch-check.sh` with per-service scope iteration.
  - Treat root-level command inferences as LOW (ask user which service is primary).
  - Per-service command inferences follow normal confidence rules within each service directory.
- **Polyglot**: If 2+ languages are detected with roughly equal presence, infer the primary from the root config file. If ambiguous, treat as LOW.
