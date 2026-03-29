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

- Check if `.claude/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run.
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
- Tie → ask user (max 1 question)
- Frontend + API both present → `.claude/launch.json` lists both; frontend first

### Phase 3: Ask minimal questions (max 3)

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
.claude/harness/manifest.yaml    # initialization marker + runtime config
.claude/harness/critics/
  plan.md                        # plan critic playbook
  runtime.md                     # runtime critic playbook
  document.md                    # document critic playbook
.claude/harness/tasks/           # task folder convention
doc/common/
  CLAUDE.md                      # common root index
```

**Minimum output set:**
- `CLAUDE.md`
- `.claude/settings.json`
- `.claude/harness/manifest.yaml`
- `.claude/harness/critics/{plan,runtime,document}.md`
- `.claude/harness/tasks/`
- `doc/common/CLAUDE.md`
- `doc/common/REQ__project__primary-goals.md`
- `doc/common/OBS__repo__workspace-layout.md`
- `doc/common/INF__arch__initial-assumptions.md`
- (web frontend) `.claude/launch.json`
- (web frontend) `.mcp.json`

### Phase 5: Generate CLAUDE.md

If root `CLAUDE.md` doesn't exist, create one:
```markdown
# CLAUDE.md
updated: <date>

# Operating mode
- Default agent is harness — an execution harness with verdict invalidation.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> runtime QA -> writer/DOC_SYNC -> critic-document (when needed) -> close.
- The only hard gate is at task completion: critic verdicts must PASS. Stale PASS (after file changes) does not count.
- DOC_SYNC.md is mandatory for all repo-mutating tasks.
- Browser-first QA is default for web frontend projects when manifest declares browser_qa_supported.
- Work in plain language. The harness routes requests and gates completion.
```

Include `doc/common/CLAUDE.md` in always_load_paths if notes were created.

### Phase 6: Generate manifest.yaml

Generate from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.claude/harness/manifest.yaml`.

For **web frontend / fullstack_web** projects, use the full manifest with `project`, `runtime`, `qa`, and `browser` sections populated from detection results.

For **non-web** projects (api, cli, worker, library), use a simplified manifest without `browser` or `project.primary_frontend` sections.

The manifest `version` must be `4`.

### Phase 7: Configure QA runtime

**Browser-first (web frontend / fullstack_web):**
- Set `qa.default_mode: browser` and `browser.enabled: true` in manifest
- Generate `.claude/launch.json` from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.claude/launch.json`
  - Populate `{{CONFIG_NAME}}` with framework name (e.g. "Next.js Dev")
  - Populate `{{RUNTIME_EXECUTABLE}}` with package manager (`npm`, `pnpm`, `yarn`, `bun`)
  - Populate `{{RUNTIME_ARGS}}` with dev script args (e.g. `["run", "dev"]`)
  - Populate `{{CWD}}` with frontend workspace path (`.` for root)
  - Populate `{{PORT}}` with detected port
- Generate `.mcp.json` from template at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.mcp.json`

**Command-line fallback (api, cli, worker, library):**
- Set `qa.default_mode: cli` and `browser.enabled: false` in manifest
- Skip `.claude/launch.json` and `.mcp.json`

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

From templates at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.claude/harness/critics/`.
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

Only create when the project has clear boundaries (monorepo, layered app, strict separation). Constraints are **HINTS only** — they inform critic playbooks but do not hard-block execution.

**Detection triggers for constraint generation:**
- Monorepo with multiple workspaces that should not cross-import
- Layered architecture (domain/infrastructure/presentation) with observable layer violations
- Naming conventions detectable from existing files (PascalCase components, snake_case modules)
- Test location rules (colocated vs. separate test directory)

When any trigger is detected, populate the `constraints:` section in `manifest.yaml` and optionally create:

```
.claude/harness/constraints/
  architecture.md            # human-readable constraint rules
  check-architecture.sh      # machine-executable checks (optional)
```

Each constraint entry must have:
- `rule`: plain-language description
- `check`: executable shell command that exits non-zero on violation (if automatable)

If constraints are not detectable from repo shape, skip this phase entirely. Do not scaffold empty constraint files.

### Phase 11: Web frontend setup

Only when `browser_qa_supported: true`:

1. Write `.mcp.json` to project root (from template — enables chrome-devtools MCP server)
2. Write `.claude/launch.json` (from template — configures dev server auto-launch)
3. Record in manifest: `browser.status: configured`

If `.mcp.json` already exists, merge `mcpServers.chrome-devtools` entry rather than overwriting.

### Phase 12: Setup .gitignore

Append harness entries if not already present:
```
.claude/harness/tasks/
```

### Phase 13: Activate harness agent

Ensure `.claude/settings.json` has `"agent": "harness:harness"`.

### Phase 14: Smoke test

Run `${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh` to validate the setup. Report result.
If it fails, note the failures — do not silently skip.

For web frontend projects, also verify:
- `.mcp.json` contains `chrome-devtools` server entry
- `.claude/launch.json` has a valid configuration with correct port

### Phase 15: Finish report

Report:
- Files created or updated
- Notes created (OBS/REQ/INF counts)
- Runtime commands detected vs. unknown
- **Project shape detected** (web_frontend / fullstack_web / api / cli / etc.)
- **Browser QA status**: enabled (with entry URL) | disabled (reason)
- Smoke test results from Phase 14
- Remaining unknowns and next steps

## Guardrails

- **No placeholder scripts.** "No test runner detected → PASS" is forbidden.
- **No fake scaffolding.** Only create files that have real content.
- **Runtime must be explicit.** Either populated or marked `unknown` with gaps listed.
- **Browser detection is automatic.** Never ask whether to enable browser QA — detect it.
- Keep generated files concise and human-editable.
- Mark uncertain items clearly.
- Minimize friction — only ask about destructive/overwrite operations.
- QA scripts in `${CLAUDE_PLUGIN_ROOT}/scripts/` are executed directly from the plugin.
