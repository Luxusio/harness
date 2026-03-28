---
name: setup
description: Bootstrap the harness completion firewall in the current repository. Minimal scaffolding — no placeholders.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

Bootstrap harness in the current repository.

After setup, the harness agent gates task completion to prevent false "done" claims.

Optional focus from user: `$ARGUMENTS`

## Procedure

### Phase 1: Repo census

- Check if `.claude/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run.
- Scan for: manifests, lockfiles, README, tests, scripts, CI config.

### Phase 2: Detect project shape

Run only non-destructive commands. Detect:
- Project type: web app / api / worker / library / monorepo / other
- Languages, frameworks, package manager
- Build/test/dev commands (actual commands, not guesses)

### Phase 3: Ask minimal questions (max 3)

Only ask what the repo cannot tell you:
- Primary project type if unclear
- Build/test commands if not detectable
- Key user journeys or critical flows

### Phase 4: Bootstrap

Create the minimal structure:

```
CLAUDE.md                        # root entrypoint (if not exists)
.claude/settings.json            # agent config
.claude/harness/manifest.yaml    # initialization marker
.claude/harness/critics/
  plan.md                        # plan critic playbook
  runtime.md                     # runtime critic playbook
  document.md                    # document critic playbook (optional)
.claude/harness/tasks/           # task folder convention
```

**What NOT to create by default:**
- `doc/` tree with REQ/OBS/INF notes (only if user wants durable docs or repo already uses them)
- `scripts/harness/*.sh` (only if actual test/build commands are confidently known)
- `.claude/harness/constraints/` (only if user asks or project has clear architectural boundaries)
- `.claude/harness/maintenance/` (not needed)
- Placeholder scripts that default to PASS

### Phase 5: Generate CLAUDE.md

If root `CLAUDE.md` doesn't exist, create one:
```markdown
# CLAUDE.md
updated: <date>

# Operating mode
- Default agent is harness — a thin loop controller with completion gates.
- `.claude/harness/manifest.yaml` is the initialization marker.
- Work in plain language. The harness routes requests and gates completion.
```

Docs are optional. Only mention them if actually created.

### Phase 6: Generate manifest.yaml

```yaml
version: 3
initialized_at: <date>
entrypoint: CLAUDE.md
```

Only add `runtime` section if actual commands were detected. Never add placeholder paths.

### Phase 7: Generate critic playbooks

From templates at `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/.claude/harness/critics/`.
Fill project-specific values from detected shape.

### Phase 8: Setup .gitignore

Append harness entries if not already present:
```
.claude/harness/tasks/
```

Only add other entries if those directories were actually created.

### Phase 9: Activate harness agent

Ensure `.claude/settings.json` has `"agent": "harness:harness"`.

### Phase 10: Finish

Report:
- Files created or updated
- What was detected vs. asked
- Remaining unknowns

## Guardrails

- **No placeholder scripts.** "No test runner detected → PASS" is forbidden.
- **No fake scaffolding.** Only create files that have real content.
- Keep generated files concise and human-editable.
- Mark uncertain items clearly.
- Minimize friction — only ask about destructive/overwrite operations.
