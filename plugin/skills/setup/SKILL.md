---
name: setup
description: Bootstrap doc/ structure, durable knowledge roots, critic playbooks, agents, skills, and hooks in the current repository.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

You are initializing the harness durable knowledge system in the current repository.

After setup, the user works in plain language. The harness agent handles routing.

Optional focus from user: `$ARGUMENTS`

## Procedure

### 1. Repo census
- Check if `doc/CLAUDE.md` already exists. If so, ask: repair, upgrade, or re-run from scratch.
- Never overwrite user-authored notes silently.
- Scan manifests, lockfiles, README, docs, tests, scripts, CI, routes, migrations.

### 2. Safe observation
- Run only non-destructive commands (`npm run`, `pytest --collect-only`, health check, etc.)
- Detect project shape:
  - greenfield vs brownfield (source files, config files present?)
  - project type: web app / api / worker / library / monorepo / other
  - languages, frameworks, package manager
  - build/test/dev commands
  - obvious domain boundaries (auth, billing, infra, etc.)

### 3. Ask minimal questions (max 5)
Only ask what the repo cannot tell you:
- primary project type if unclear
- key user journeys or critical flows
- high-risk areas
- build/test commands if not detectable

### 4. Bootstrap generation
Create the following structure. Adapt templates from `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/`.

**Doc structure:**
```
doc/
  CLAUDE.md                              # root registry
  common/
    CLAUDE.md                            # common root index
    REQ__project__primary-goals.md       # from user answers
    OBS__repo__workspace-layout.md       # from repo scan
    INF__arch__initial-stack-assumptions.md  # from inference
```

**Operational structure:**
```
.claude/
  settings.json                          # hooks config
  agents/
    harness.md
    developer.md
    writer.md
    critic-plan.md
    critic-runtime.md
    critic-write.md
    critic-structure.md
  skills/
    plan/SKILL.md
    maintain/SKILL.md
  hooks/
    task-created-gate.sh
    subagent-stop-gate.sh
    task-completed-gate.sh
    post-compact-sync.sh
    session-end-sync.sh
  harness/
    critics/
      plan.md
      runtime.md
      write.md
      structure.md
    tasks/
    maintenance/
      QUEUE.md
      COMPACTION_LOG.md
    archive/
```

**CLAUDE.md** at project root with harness bootstrap instructions.

### 5. Create initial notes

**REQ__project__primary-goals.md** — from user's stated goals:
```markdown
# REQ project primary-goals
tags: [req, root:common, source:user, status:active]
summary: <project goals from user>
source: user request on <date>
updated: <date>
```

**OBS__repo__workspace-layout.md** — from repo scan:
```markdown
# OBS repo workspace-layout
tags: [obs, root:common, source:filesystem, status:active]
summary: <observed repo structure>
evidence: filesystem scan at setup time
updated: <date>
```

**INF__arch__initial-stack-assumptions.md** — from inference:
```markdown
# INF arch initial-stack-assumptions
tags: [inf, root:common, confidence:<level>, status:active]
summary: <inferred tech stack>
basis: <what files/configs led to this inference>
updated: <date>
verify_by: <how to confirm>
```

### 6. Critic playbook generation
Generate project-specific critic playbooks from templates:
- `.claude/harness/critics/plan.md` — project-specific plan checks
- `.claude/harness/critics/runtime.md` — environment map, must_verify, prefer commands
- `.claude/harness/critics/write.md` — note hygiene rules
- `.claude/harness/critics/structure.md` — structure governance rules

Fill `{{PROJECT_SUMMARY}}`, `{{MUST_VERIFY}}`, `{{PREFER_COMMANDS}}`, `{{ENVIRONMENT_MAP}}` from detected project shape.

### 7. Obvious root candidates
If obvious domain boundaries were detected (auth, billing, etc.):
- Propose to critic-structure via `harness:critic-structure`
- Only create if PASS
- Each root gets its own CLAUDE.md index

### 8. Structure-critic review
Submit the full proposed structure to `harness:critic-structure` for approval before writing.

### 9. Reviewable diff
Present the full list of files to be created/modified as a diff summary.
Do not write files until the user confirms.
Show:
- files to create (new)
- files to modify (existing)
- what was inferred vs confirmed

### 10. Write files
After user confirmation, write all files. Then:
- `chmod +x .claude/hooks/*.sh`
- `sed -i 's/\r$//' .claude/hooks/*.sh`

### 11. Setup .gitignore
Harness operational artifacts (tasks, maintenance queue, archive) are ephemeral and should not be committed.

- If `.gitignore` exists, check whether it already contains `harness` entries.
- If not, append the contents of `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/gitignore-harness`.
- If `.gitignore` does not exist, create it with those entries.

Entries to add:
```
# harness — operational artifacts (ephemeral, not durable knowledge)
.claude/harness/tasks/
.claude/harness/maintenance/
.claude/harness/archive/
```

Do NOT gitignore:
- `doc/` — durable knowledge (the whole point)
- `.claude/harness/critics/` — project-specific playbooks
- `.claude/settings.json`, `.claude/hooks/` — shared config

### 12. Activate harness agent

Read `.claude/settings.json` (create if missing). Merge the agent field:
```json
{
  "agent": "harness:harness"
}
```
Preserve any existing fields.

### 13. Finish
Report:
- files created or updated
- roots registered
- notes created (REQ/OBS/INF counts)
- what was inferred vs confirmed
- remaining unknowns
- reminder: user can now work in plain language — the harness agent is now active

## Guardrails

- Keep generated files concise and human-editable
- Do not fill templates with fake certainty
- Mark uncertain items as INF with verify_by
- Prefer repository evidence over assumptions
