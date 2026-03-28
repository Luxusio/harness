---
name: setup
description: Bootstrap the harness execution environment — critic playbooks, QA scaffolding, and runtime config. No placeholders.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

Bootstrap harness in the current repository.

After setup, the harness agent gates task completion and agents can run verify/smoke loops immediately.

Optional focus from user: `$ARGUMENTS`

## Success criteria

Setup is complete when an agent can:
1. Create a task with a plan
2. Run verification commands from the manifest
3. Get critic verdicts
4. Close the task through the completion gate

## Procedure

### Phase 1: Repo census

- Check if `.claude/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run.
- Scan for: manifests, lockfiles, README, tests, scripts, CI config.

### Phase 2: Detect project shape

Run only non-destructive commands. Detect:
- Project type: web app / api / worker / library / cli / monorepo / other
- Languages, frameworks, package manager
- Build/test/dev commands (actual commands, not guesses)
- Health endpoints, smoke URLs, DB connections
- Existing test infrastructure (test runner, fixtures, CI)

### Phase 3: Ask minimal questions (max 3)

Only ask what the repo cannot tell you:
- Primary project type if unclear
- Build/test commands if not detectable
- Key user journeys or critical flows

### Phase 4: Bootstrap structure

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

### Phase 5: Generate CLAUDE.md

If root `CLAUDE.md` doesn't exist, create one:
```markdown
# CLAUDE.md
updated: <date>

# Operating mode
- Default agent is harness — a thin loop controller with completion gates.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> critic-runtime PASS -> close.
- Work in plain language. The harness routes requests and gates completion.
```

Include `doc/common/CLAUDE.md` in always_load_paths if notes were created.

### Phase 6: Generate manifest.yaml

```yaml
version: 3
initialized_at: <date>
entrypoint: CLAUDE.md
```

Add `runtime` section with detected commands. QA scripts live in the plugin at `${CLAUDE_PLUGIN_ROOT}/scripts/` — they read project-specific config from the manifest:

```yaml
runtime:
  smoke_command: "npm test"          # or pytest, go test, etc.
  healthcheck_command: "curl -sf http://localhost:3000/health"
  reset_command: "npm run db:reset"  # optional, omit if no DB
  healthchecks:
    - http://localhost:3000/health
```

The plugin scripts (`verify.sh`, `smoke.sh`, `healthcheck.sh`, `reset-db.sh`) read these fields automatically. Projects can also override with local `scripts/harness/*.sh` which take priority.

If commands are unknown, add:

```yaml
runtime: unknown
runtime_missing:
  - test_command
  - build_command
  - dev_command
```

**Never omit runtime silently.** Either populate it or mark it unknown with specific gaps.

### Phase 7: Configure QA runtime

QA scripts are bundled in the plugin (`${CLAUDE_PLUGIN_ROOT}/scripts/`). Setup does NOT copy scripts into the project. Instead, populate `manifest.yaml` runtime fields so the plugin scripts know what to run.

| Plugin script | Reads from manifest | Fallback |
|---------------|-------------------|----------|
| `verify.sh` | runs smoke.sh + healthcheck.sh | — |
| `smoke.sh` | `runtime.smoke_command` | `scripts/harness/smoke.sh` in project |
| `healthcheck.sh` | `runtime.healthcheck_command` | `scripts/harness/healthcheck.sh` in project |
| `reset-db.sh` | `runtime.reset_command` | `scripts/harness/reset-db.sh` in project |

**Project-shape guidance for manifest fields:**
- **web app**: `smoke_command: "npm test"`, healthchecks list, reset_command
- **api**: `smoke_command: "curl -sf http://localhost:3001/health"`, DB reset
- **cli/worker**: `smoke_command: "<tool> --version && <tool> example-input"`
- **library**: `smoke_command: "npm test"` or equivalent

**Rules:**
- Populate manifest runtime fields with real detected commands
- If a command is unknown, omit the field (the plugin script will print SKIP and exit non-zero)
- Only create project-local `scripts/harness/` if the project needs custom multi-step verification beyond a single command

### Phase 8: Generate critic playbooks

From templates at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.claude/harness/critics/`.
Fill project-specific values from detected shape:
- Runtime playbook gets actual verify commands, health endpoints, preferred verification order
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

Only create when the project has clear boundaries to enforce (monorepo, layered app, strict separation):

```
.claude/harness/constraints/
  architecture.md            # human-readable rules
  check-architecture.sh      # machine-executable checks
```

### Phase 11: Setup .gitignore

Append harness entries if not already present:
```
.claude/harness/tasks/
```

### Phase 12: Activate harness agent

Ensure `.claude/settings.json` has `"agent": "harness:harness"`.

### Phase 13: Smoke test

Run `${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh` to validate the setup. Report result.
If it fails, note the failures — do not silently skip.

### Phase 14: Finish

Report:
- Files created or updated
- Notes created (OBS/REQ/INF counts)
- Runtime commands detected vs. unknown
- Smoke test results from Phase 13
- Remaining unknowns and next steps

## Guardrails

- **No placeholder scripts.** "No test runner detected → PASS" is forbidden.
- **No fake scaffolding.** Only create files that have real content.
- **Runtime must be explicit.** Either populated or marked `unknown` with gaps listed.
- Keep generated files concise and human-editable.
- Mark uncertain items clearly.
- Minimize friction — only ask about destructive/overwrite operations.
