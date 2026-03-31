---
name: setup
description: Bootstrap the harness execution environment — critic playbooks, QA scaffolding, browser-first detection, and runtime config. No placeholders.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

Bootstrap harness in the current repository.

After setup, the harness agent gates task completion and agents can run verify/smoke loops immediately. For web frontend projects, browser-first QA is configured automatically.

Optional focus from user: `$ARGUMENTS`

## Success criteria

Setup is complete when an agent can:
1. Create a task with a plan
2. Run verification commands from the manifest
3. Get critic verdicts
4. Close the task through the completion gate
5. (web frontend) Launch browser QA via chrome-devtools MCP

## Procedure

### Phase 1: Repo census

- Check if `doc/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run.
- Scan for: manifests, lockfiles, README, tests, scripts, CI config.

### Phase 2: Detect project shape

Run only non-destructive commands. Detect:
- Project type: web frontend / fullstack_web / api / worker / library / cli / monorepo / other
- Languages, frameworks, package manager
- Build/test/dev commands (actual commands, not guesses)
- Health endpoints, smoke URLs, DB connections
- Existing test infrastructure (test runner, fixtures, CI)

**Web Frontend Auto-Detection (4-signal process):**

#### 1st Signal: Framework/Package
Read `package.json` dependencies and devDependencies. Look for:
- `next`, `react`, `vite`, `vue`, `nuxt`, `svelte`, `astro`, `@angular/core`, `remix`, `solid-start`, `gatsby`

#### 2nd Signal: Structure
Check for presence of:
- `src/app`, `src/pages`, `app/`, `pages/`, `public/`, `index.html`
- `vite.config.*`, `next.config.*`, `astro.config.*`, `nuxt.config.*`, `angular.json`

#### 3rd Signal: Executability
Check `package.json` scripts for `dev`, `start`, `preview`. For monorepos, check workspace-level scripts. Extract port hints from `.env*`, config files, and known framework defaults:
- Next.js → 3000, Vite → 5173, Nuxt → 3000, Astro → 4321, Angular → 4200, SvelteKit → 5173

#### 4th Signal: Exclusion Rules
Server-only packages without UI signals → browser QA disabled:
- `express`, `fastify`, `@nestjs/core`, `django`, `flask`, `spring-boot`

Mobile/native only → browser QA disabled:
- `react-native`, `expo`

#### Detection Result
Determine and record:
```
project_shape: web_frontend | fullstack_web | api | cli | worker | library | monorepo
browser_qa_supported: true | false
frontend_candidates[]        # list of scored workspace paths
primary_frontend             # highest-scoring candidate
```

#### Monorepo Rules
- Score each workspace using all 4 signals
- Highest-scoring workspace = primary frontend
- Tie → ask user via `AskUserQuestion` (max 1 question)
- Frontend + API both present → `doc/harness/launch.json` lists both; frontend first

### Phase 3: Ask minimal questions (max 3)

**Use `AskUserQuestion` tool for every question** — never plain text. This provides a clickable UI for faster user responses.

Only ask what the repo cannot tell you:
- Primary project type if unclear
- Build/test commands if not detectable
- Key user journeys or critical flows

For monorepo ties, ask which workspace is the primary frontend (counts toward the 3-question cap).

### Phase 4: Bootstrap core structure

Create the core structure:

```
CLAUDE.md                        # root entrypoint (if not exists)
.claude/settings.json            # agent config
doc/harness/manifest.yaml    # initialization marker + runtime config
doc/harness/critics/
  plan.md                        # plan critic playbook
  runtime.md                     # runtime critic playbook
  document.md                    # document critic playbook
doc/harness/review-overlays/
  security.md                  # security review overlay
  performance.md               # performance review overlay
  frontend-refactor.md         # frontend refactor review overlay
doc/harness/tasks/           # task folder convention
doc/common/
  CLAUDE.md                      # common root index
```

**Minimum output set:**
- `CLAUDE.md`
- `.claude/settings.json`
- `doc/harness/manifest.yaml`
- `doc/harness/critics/{plan,runtime,document}.md`
- `doc/harness/review-overlays/{security,performance,frontend-refactor}.md`
- `doc/harness/tasks/`
- `doc/common/CLAUDE.md`
- `doc/common/REQ__project__primary-goals.md`
- `doc/common/OBS__repo__workspace-layout.md`
- `doc/common/INF__arch__initial-assumptions.md`
- (web frontend) `doc/harness/launch.json`
- (web frontend) `.mcp.json`

### Phase 5: Generate CLAUDE.md

If root `CLAUDE.md` doesn't exist, create one:
```markdown
# CLAUDE.md
updated: <date>

# Operating mode
- Default agent is harness — an execution harness with verdict invalidation.
- `doc/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> runtime QA -> writer/DOC_SYNC -> critic-document (when needed) -> close.
- The only hard gate is at task completion: critic verdicts must PASS. Stale PASS (after file changes) does not count.
- DOC_SYNC.md is mandatory for all repo-mutating tasks.
- Browser-first QA is default for web frontend projects when manifest declares browser_qa_supported.
- Work in plain language. The harness routes requests and gates completion.
```

Include `doc/common/CLAUDE.md` in always_load_paths if notes were created.

### Phase 6: Generate manifest.yaml

Generate from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/doc/harness/manifest.yaml`.

For **web frontend / fullstack_web** projects, use the full manifest with `project`, `runtime`, `qa`, and `browser` sections populated from detection results.

For **non-web** projects (api, cli, worker, library), use a simplified manifest without `browser` or `project.primary_frontend` sections.

The manifest `version` must be `4`.

#### registered_roots field

Always include `registered_roots` in the manifest. This list tells the memory retrieval system which `doc/*` subdirectories contain notes.

```yaml
registered_roots:
  - common
```

Default is `["common"]` for single-workspace projects.

**For monorepos or multi-surface projects**, suggest additional roots based on detected workspaces or source surfaces. Each additional root should correspond to a distinct `doc/<root>/` directory that will be created alongside `doc/common/`.

Detection rules:
- Single workspace → `registered_roots: [common]`
- Monorepo with N workspaces → suggest one root per workspace (e.g., `[common, frontend, api, worker]`)
- Multi-surface (app + api detected in same repo) → suggest surface-level roots (e.g., `[common, app, api]`)
- Always include `common` as the base root; it is never removed

When suggesting additional roots, create the corresponding `doc/<root>/CLAUDE.md` index file (empty index) so the directory exists and is indexed from the start.

### Phase 7: Configure QA runtime

**Browser-first (web frontend / fullstack_web):**
- Set `qa.default_mode: browser` and `browser.enabled: true` in manifest
- Generate `doc/harness/launch.json` from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/doc/harness/launch.json`
  - Populate `{{CONFIG_NAME}}` with framework name (e.g. "Next.js Dev")
  - Populate `{{RUNTIME_EXECUTABLE}}` with package manager (`npm`, `pnpm`, `yarn`, `bun`)
  - Populate `{{RUNTIME_ARGS}}` with dev script args (e.g. `["run", "dev"]`)
  - Populate `{{CWD}}` with frontend workspace path (`.` for root)
  - Populate `{{PORT}}` with detected port
- Generate `.mcp.json` from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.mcp.json`

**Command-line fallback (api, cli, worker, library):**
- Set `qa.default_mode: cli` and `browser.enabled: false` in manifest
- Skip `doc/harness/launch.json` and `.mcp.json`

**Project-shape guidance for manifest runtime fields:**
- **web frontend / fullstack_web**: `dev_command: "npm run dev"`, `test_command: "npm test"`, healthchecks list, reset_command if DB detected
- **api**: `smoke_command: "curl -sf http://localhost:3001/health"`, DB reset
- **cli/worker**: `smoke_command: "<tool> --version && <tool> example-input"`
- **library**: `smoke_command: "npm test"` or equivalent

**Rules:**
- Populate manifest runtime fields with real detected commands
- If a command is unknown, omit the field (plugin script prints SKIP and exits non-zero)
- QA scripts (verify, smoke, healthcheck, reset-db, browser-smoke, persistence-check) live in `${CLAUDE_PLUGIN_ROOT}/scripts/` and are executed directly — no project-local copy needed

### Phase 8: Generate critic playbooks

From templates at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/doc/harness/critics/`.
Fill project-specific values from detected shape:
- Runtime playbook gets actual verify commands, health endpoints, preferred verification order
- For web frontend projects, runtime playbook adds browser QA steps using chrome-devtools MCP
- Plan playbook gets project-specific acceptance patterns
- Document playbook gets project doc conventions

### Phase 9: Create initial notes

Generate notes from what was actually detected — not templates with placeholders.

**From repo scan (always):**
- `doc/common/OBS__repo__workspace-layout.md` — observed project structure, languages, frameworks, commands
  - Content comes from Phase 2 detection results — real facts, not guesses

**From user answers (if goals were stated):**
- `doc/common/REQ__project__primary-goals.md` — stated project goals and requirements

**From inference (if stack assumptions were made):**
- `doc/common/INF__arch__initial-assumptions.md` — inferred assumptions with `verify_by` instructions

Update `doc/common/CLAUDE.md` index to list created notes.

**Rules:**
- Only create notes with real content from detection/conversation — never empty templates
- OBS must have actual evidence (what was observed)
- INF must have a concrete `verify_by` (how to check)
- If nothing meaningful was detected, skip note creation

### Phase 10: Optional architecture constraints

Only create when the project has clear boundaries (monorepo, layered app, strict separation). Constraints are **HINTS only** by default — they inform critic playbooks but do not hard-block execution.

**Detection triggers for constraint generation:**
- Monorepo with multiple workspaces that should not cross-import
- Layered architecture (domain/infrastructure/presentation) with observable layer violations
- Naming conventions detectable from existing files (PascalCase components, snake_case modules)
- Test location rules (colocated vs. separate test directory)

When any trigger is detected, populate the `constraints:` section in `manifest.yaml` and optionally create:

```
doc/harness/constraints/
  architecture.md            # human-readable constraint rules
  check-architecture.sh      # machine-executable checks (optional)
```

Each constraint entry must have:
- `rule`: plain-language description
- `check`: executable shell command that exits non-zero on violation (if automatable)

If constraints are not detectable from repo shape, skip this phase entirely. Do not scaffold empty constraint files.

**Architecture check promotion (automatic):**

Under normal conditions, architecture checks are hints — their absence or failure does not affect critic verdicts. However, the architecture check result is automatically promoted to **required evidence** for a runtime PASS when ALL three conditions are met:

1. `execution_mode` is `sprinted` (from TASK_STATE.yaml)
2. `risk_tags` contain at least one of: `structural`, `migration`, `schema`, `cross-root`
3. `doc/harness/constraints/check-architecture.*` file exists in the repo

This promotion is **automatic** — no user configuration is needed. For most repos (no constraints directory), architecture checks are skipped entirely and do not affect verdicts. For light and standard mode tasks, architecture checks are always hints regardless of risk tags. See `plugin/docs/architecture-promotion.md` for complete reference.

### Phase 11: Web frontend setup

Only when `browser_qa_supported: true`:

1. Write `.mcp.json` to project root (from template — enables chrome-devtools MCP server)
2. Write `doc/harness/launch.json` (from template — configures dev server auto-launch)
3. Record in manifest: `browser.status: configured`

If `.mcp.json` already exists, merge `mcpServers.chrome-devtools` entry rather than overwriting.

### Phase 11b: Detect tooling readiness

Probe for available tooling and set the `tooling:` and `profiles:` fields in manifest.yaml.

#### chrome-devtools MCP readiness

Check whether the chrome-devtools MCP server is usable:

```bash
# 1. Is .mcp.json present and does it contain a chrome-devtools entry?
grep -q "chrome-devtools" .mcp.json 2>/dev/null

# 2. Is npx available? (the server launches via npx)
command -v npx &>/dev/null
```

Rules:
- Both conditions true → set `tooling.chrome_devtools_ready: true`
- Either condition false → set `tooling.chrome_devtools_ready: false`
- If `browser.enabled: true` but `chrome_devtools_ready: false` → flag in finish report as a gap (browser QA is configured but MCP server may not launch)

#### ast-grep readiness

Check for the ast-grep binary:
```bash
command -v ast-grep &>/dev/null || command -v sg &>/dev/null
```

- Binary found → set `tooling.ast_grep_ready: true`
- Binary not found → set `tooling.ast_grep_ready: false`

Set `profiles.ast_grep_enabled: false` by default (user enables explicitly after confirming queries work).

#### LSP / cclsp readiness

Check for LSP infrastructure:
```bash
# Check for cclsp (harness-preferred LSP bridge)
command -v cclsp &>/dev/null

# Check for generic LSP configs in project
ls .claude/lsp*.json .vscode/settings.json 2>/dev/null
```

Also check for language-specific LSP servers matching detected languages:
- TypeScript/JavaScript → `typescript-language-server`, `vtsls`
- Python → `pylsp`, `pyright`
- Go → `gopls`
- Rust → `rust-analyzer`

Rules:
- `cclsp` binary found → set `tooling.cclsp_ready: true`
- Any LSP server matching project languages found → set `tooling.lsp_ready: true`
- Neither found → both `false`

Set `profiles.symbol_lane_enabled: false` by default (user enables explicitly).

#### Observability feasibility

Check Docker availability and project kind:
```bash
command -v docker &>/dev/null && docker info &>/dev/null 2>&1
```

Rules:
- Docker available AND project kind is `web_frontend`, `fullstack_web`, `api`, or `worker` → set `tooling.observability_ready: true`
- Otherwise → set `tooling.observability_ready: false`

Set `profiles.observability_enabled: false` by default (requires explicit opt-in due to resource cost).

Report detected tooling in Phase 15 finish report, including `chrome_devtools_ready` status. If `browser.enabled: true` but `chrome_devtools_ready: false`, flag explicitly as a gap.

### Phase 11c: Team readiness detection

Probe for team execution capabilities and populate the `teams:` section in manifest.yaml.

Run `${CLAUDE_PLUGIN_ROOT}/scripts/team_readiness.py` to detect:

#### Native team readiness
- `claude --version` available and version supports native teams
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` environment variable set
- Optional: `tmux` available for terminal-based team sessions

#### OMC team readiness
- `command -v omc` succeeds
- `.omc/` directory exists in project or home directory

#### Populate manifest teams section

Based on readiness results, populate the `teams:` section in manifest.yaml:

```yaml
teams:
  provider: auto
  native_ready: <detected>
  omc_ready: <detected>
  auto_activate: true
  approval_mode: preapproved
  teammate_mode: auto
  default_size: 3
  max_size: 5
  fallback: subagents
  safe_only:
    require_disjoint_files: true
    forbid_same_file_edits: true
    forbid_heavy_dependency_chains: true
```

Rules:
- `provider: auto` by default (prefers native if ready, then omc, then fallback)
- `native_ready` and `omc_ready` reflect actual detection results
- `auto_activate: true` and `approval_mode: preapproved` mean the harness does not ask the user for team permission
- If native team readiness is detected, consider adding `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` to `.claude/settings.json` env section
- `fallback: subagents` means if team provider fails, fall back to subagents mode

Report team readiness status in Phase 15 finish report.

### Phase 12: Setup .gitignore

Append harness entries if not already present:
```
doc/harness/tasks/
```

### Phase 13: Activate harness agent

Ensure `.claude/settings.json` has at least:

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "agent": "harness:harness",
  "permissions": {
    "allow": [
      "Skill(harness:plan)",
      "Skill(harness:plan *)",
      "Skill(harness:maintain)",
      "Skill(harness:maintain *)"
    ]
  }
}
```

Rationale:
- the main thread runs as `harness:harness`
- `harness` invokes `harness:plan` / `harness:maintain` through the `Skill` tool
- plugin-shipped agents cannot rely on `permissionMode` or `mcpServers` frontmatter, so browser QA must be configured through project/session scope tools such as `.mcp.json`

### Phase 14: Smoke test

Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py` to validate the setup. Report result.
If it fails, note the failures — do not silently skip.

For web frontend projects, also verify:
- `.mcp.json` contains `chrome-devtools` server entry
- `doc/harness/launch.json` has a valid configuration with correct port

### Phase 15: Finish report

Report:
- Files created or updated
- Notes created (OBS/REQ/INF counts)
- Runtime commands detected vs. unknown
- **Project shape detected** (web_frontend / fullstack_web / api / cli / etc.)
- **Browser QA status**: enabled (with entry URL) | disabled (reason)
- Smoke test results from Phase 14
- **Team readiness**: native (ready/not ready), omc (ready/not ready), provider: auto
- Remaining unknowns and next steps

**Always end the finish report with this notice:**

> ⚠️ **Restart Claude Code** to apply the new harness configuration.
> The agent, hooks, and manifest settings take effect only after a fresh session.

## Guardrails

- **No placeholder scripts.** "No test runner detected → PASS" is forbidden.
- **No fake scaffolding.** Only create files that have real content.
- **Runtime must be explicit.** Either populated or marked `unknown` with gaps listed.
- **Browser detection is automatic.** Never ask whether to enable browser QA — detect it.
- Keep generated files concise and human-editable.
- Mark uncertain items clearly.
- Minimize friction — only ask about destructive/overwrite operations.
- QA scripts in `${CLAUDE_PLUGIN_ROOT}/scripts/` are executed directly from the plugin.
