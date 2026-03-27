---
name: setup
description: Bootstrap harness structure, durable knowledge roots, critic playbooks, executable QA scaffolding, and hooks in the current repository.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

You are initializing the harness durable knowledge system in the current repository.

After setup, the user works in plain language. The harness agent handles routing.

Optional focus from user: `$ARGUMENTS`

## Procedure

### 1. Repo census
- Check if `.claude/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run from scratch.
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
  common/
    CLAUDE.md                            # common root index
    REQ__project__primary-goals.md       # from user answers
    OBS__repo__workspace-layout.md       # from repo scan
    INF__arch__initial-stack-assumptions.md  # from inference
```

**Operational structure:**
```
.claude/
  settings.json                          # agent config (hooks are provided by the plugin)
  harness/
    manifest.yaml                        # initialization marker + runtime config
    critics/
      plan.md
      runtime.md
      document.md
    tasks/
    maintenance/
      QUEUE.md
      COMPACTION_LOG.md
    archive/
```

Note: Hook scripts (task-created-gate, subagent-stop-gate, task-completed-gate, post-compact-sync, session-end-sync) are built into the plugin and do not need to be copied to target projects.

**Executable QA scaffolding:**
```
scripts/harness/
  verify.sh          # main verification entry point
  smoke.sh           # smoke test runner
  healthcheck.sh     # health check probe
  reset-db.sh        # DB reset / seed script (or placeholder)
```

**CLAUDE.md** at project root with harness bootstrap instructions.

### 5. Create initial notes

**REQ__project__primary-goals.md** — from user's stated goals
**OBS__repo__workspace-layout.md** — from repo scan
**INF__arch__initial-stack-assumptions.md** — from inference

### 6. Critic playbook generation
Generate project-specific critic playbooks from templates:
- `.claude/harness/critics/plan.md` — project-specific plan checks
- `.claude/harness/critics/runtime.md` — environment map, must_verify, prefer commands, health checks, seed/reset commands, persistence checks
- `.claude/harness/critics/document.md` — note hygiene, index sync, supersede history, new root / archive / compaction rules

Fill `{{PROJECT_SUMMARY}}`, `{{MUST_VERIFY}}`, `{{PREFER_COMMANDS}}`, `{{ENVIRONMENT_MAP}}` from detected project shape.

### 7. Create manifest.yaml
Generate `.claude/harness/manifest.yaml`:

```yaml
version: 3
initialized_at: <date>
entrypoint: CLAUDE.md
always_load_paths:
  - doc/common/CLAUDE.md
registered_roots:
  - common
runtime:
  verify_script: scripts/harness/verify.sh
  smoke_script: scripts/harness/smoke.sh
  reset_script: scripts/harness/reset-db.sh
  healthchecks: []
workflow:
  contract_required: true
  qa_required_for_repo_mutations: true
  persistence_required: true
  docs_sync_required: true
  document_critic: critic-document
```

### 8. Generate executable QA scripts
Adapt to detected project shape:
- **web app**: browser-first smoke + route probes + persistence checks
- **api**: curl/http smoke + DB or side-effect checks
- **cli/worker**: example commands + log/output checks
- **library**: tests/examples + minimal reproducible command

### 9. Obvious root candidates
If obvious domain boundaries were detected (auth, billing, etc.):
- Propose to critic-document via `harness:critic-document`
- Only create if PASS
- Each root gets its own CLAUDE.md index

### 10. Structure-critic review
Submit the full proposed structure to `harness:critic-document` for approval before writing.

### 11. Reviewable diff
Present the full list of files to be created/modified as a diff summary.
Do not write files until the user confirms.
Show:
- files to create (new)
- files to modify (existing)
- what was inferred vs confirmed

### 12. Write files
After user confirmation, write all files. Then:
- `chmod +x scripts/harness/*.sh`
- `sed -i 's/\r$//' scripts/harness/*.sh`

### 13. Setup .gitignore
Harness operational artifacts (tasks, maintenance queue, archive) are ephemeral and should not be committed.

- If `.gitignore` exists, check whether it already contains `harness` entries.
- If not, append the contents of `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/gitignore-harness`.
- If `.gitignore` does not exist, create it with those entries.

Do NOT gitignore:
- `doc/` — durable knowledge (the whole point)
- `.claude/harness/critics/` — project-specific playbooks
- `.claude/harness/manifest.yaml` — initialization marker
- `.claude/settings.json` — agent config
- `scripts/harness/` — executable QA scaffolding

### 14. Optional architecture constraints
Only generate when the repo shape benefits from machine constraints (monorepo, layered app, strict boundary rules, etc.):

```
.claude/harness/constraints/
  architecture.md
  check-architecture.sh
```

### 15. Activate harness agent

Read `.claude/settings.json` (create if missing). Merge the agent field:
```json
{
  "agent": "harness:harness"
}
```
Preserve any existing fields.

### 16. Finish
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
