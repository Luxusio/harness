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

Create the structure:

```
CLAUDE.md                        # root entrypoint (if not exists)
.claude/settings.json            # agent config
.claude/harness/manifest.yaml    # initialization marker
.claude/harness/critics/
  plan.md                        # plan critic playbook
  runtime.md                     # runtime critic playbook
  document.md                    # document critic playbook
.claude/harness/tasks/           # task folder convention
doc/common/
  CLAUDE.md                      # common root index
```

**What NOT to create by default:**
- `scripts/harness/*.sh` (only if actual test/build commands are confidently known)
- `.claude/harness/constraints/` (only if user asks or project has clear architectural boundaries)
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

Include `doc/common/CLAUDE.md` in always_load_paths if notes were created.

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

### Phase 8: Create initial notes

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

### Phase 9: Setup .gitignore

Append harness entries if not already present:
```
.claude/harness/tasks/
```

Only add other entries if those directories were actually created.

### Phase 10: Activate harness agent

Ensure `.claude/settings.json` has `"agent": "harness:harness"`.

### Phase 11: Finish

Report:
- Files created or updated
- Notes created (OBS/REQ/INF counts)
- What was detected vs. asked
- Remaining unknowns

## Guardrails

- **No placeholder scripts.** "No test runner detected → PASS" is forbidden.
- **No fake scaffolding.** Only create files that have real content.
- Keep generated files concise and human-editable.
- Mark uncertain items clearly.
- Minimize friction — only ask about destructive/overwrite operations.
