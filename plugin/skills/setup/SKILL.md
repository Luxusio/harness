---
name: setup
description: Bootstrap the universal loop runtime in the current repository. Handles greenfield scaffolding and brownfield truth extraction. Tiered confirmation — auto for safe ops, user approval for destructive/overwrite ops only.
argument-hint: [optional focus]
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, AskUserQuestion, Agent
---

You are initializing the harness universal loop runtime in the current repository.

After setup, the user works in plain language. The harness agent handles routing through the loop.

Optional focus from user: `$ARGUMENTS`

## Procedure

### Phase 1: Repo census

- Check if `.claude/harness/manifest.yaml` already exists. If so, ask: repair, upgrade, or re-run from scratch.
- Never overwrite user-authored notes silently.
- Scan manifests, lockfiles, README, docs, tests, scripts, CI, routes, migrations.

### Phase 2: Safe observation

Run only non-destructive commands (`npm run`, `pytest --collect-only`, health check, etc.).

Detect project shape:
- greenfield vs brownfield (source files, config files present?)
- project type: web app / api / worker / library / monorepo / other
- languages, frameworks, package manager
- build/test/dev commands
- obvious domain boundaries (auth, billing, infra, etc.)
- existing constraints (lint rules, CI checks, architectural boundaries)

### Phase 3: Brownfield truth extraction (if brownfield)

For existing repos, extract truth from scattered sources before imposing structure:

**Source scanning:**

| Source | Extract |
|--------|---------|
| README.md | Project goals, setup instructions, architecture overview |
| package.json / pyproject.toml / go.mod | Dependencies, scripts, project metadata |
| CI config (.github/workflows, .gitlab-ci.yml) | Build/test/deploy commands, env requirements |
| Existing docs/ | Architecture decisions, API docs, design docs |
| Test files | Verified behaviors, integration points |
| Migration files | Schema evolution, data model history |
| Scripts/ | Operational procedures, deployment steps |
| .env.example / docker-compose.yml | Environment dependencies, service topology |

**Truth classification:**

Classify each extracted fact into one of three buckets:

| Bucket | Meaning | Becomes |
|--------|---------|---------|
| **Confirmed** | Directly verified by code, tests, or runtime | OBS notes |
| **Inferred** | Reasonable assumption from available evidence | INF notes |
| **Conflicting** | Multiple sources disagree | Flagged for user resolution |

Structure extraction results into:
- **Project goals** → REQ notes (only if explicitly stated, otherwise INF)
- **Architecture assumptions** → INF notes with `verify_by`
- **Critical flows** → OBS notes (if tests prove them) or INF notes
- **Verify commands** → manifest runtime config
- **Unknowns** → maintenance queue items
- **Conflicts** → flagged for user in confirmation step

### Phase 4: Ask minimal questions (max 5)

Only ask what the repo cannot tell you:
- primary project type if unclear
- key user journeys or critical flows
- high-risk areas
- build/test commands if not detectable
- **For brownfield**: confirm or correct extracted assumptions

### Phase 5: Architecture constraints (default, not optional)

Generate architecture constraints by default. This is a core part of setup, not an addon.

```
.claude/harness/constraints/
  architecture.md          # boundary rules document
  check-architecture.sh    # executable constraint checker
```

**`architecture.md`** content based on detected project shape:

| Project shape | Default constraints |
|--------------|-------------------|
| Monorepo | Package boundary rules, shared dependency rules |
| Layered app | Layer dependency direction (e.g., UI → service → data, not reverse) |
| API service | Route/handler separation, middleware rules |
| Library | Public API surface rules, backward compatibility |
| Simple/unclear | Minimal: file organization, test co-location |

**`check-architecture.sh`** — executable validator:
- Import direction checks (grep-based)
- Forbidden dependency checks
- File placement rules
- Returns exit code 0 (pass) or 1 (fail) with violation details

Constraints are rules paired with their checker. A rule without enforcement is just a suggestion.

### Phase 6: Bootstrap generation

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
  settings.json                          # agent config
  harness/
    manifest.yaml                        # initialization marker + runtime config
    critics/
      plan.md
      runtime.md
      document.md
    constraints/
      architecture.md                    # boundary rules (default)
      check-architecture.sh              # constraint checker (default)
    tasks/
    maintenance/
      QUEUE.md
      COMPACTION_LOG.md
    archive/
```

Note: Hook scripts are built into the plugin and do not need to be copied to target projects.

**Executable QA scaffolding:**
```
scripts/harness/
  verify.sh          # main verification entry point
  smoke.sh           # smoke test runner
  healthcheck.sh     # health check probe
  reset-db.sh        # DB reset / seed script (or placeholder)
```

**CLAUDE.md** at project root with harness bootstrap instructions.

### Phase 7: Create initial notes

Notes include freshness tracking fields:

**REQ__project__primary-goals.md** — from user's stated goals
- `status: active`, `confidence: high`, `source_kind: user`

**OBS__repo__workspace-layout.md** — from repo scan
- `status: active`, `freshness: fresh`, `source_kind: runtime`, `last_verified_at: <today>`

**INF__arch__initial-stack-assumptions.md** — from inference
- `status: active`, `freshness: fresh`, `confidence: medium`, `verify_by: <specific check>`

**For brownfield**: additional notes from truth extraction (Phase 3)

### Phase 8: Critic playbook generation

Generate project-specific critic playbooks from templates:
- `.claude/harness/critics/plan.md` — project-specific plan checks
- `.claude/harness/critics/runtime.md` — environment map, must_verify, prefer commands, health checks, seed/reset commands, persistence checks
- `.claude/harness/critics/document.md` — note hygiene, index sync, supersede history, freshness rules, constraint checking, new root / archive / compaction rules

Fill `{{PROJECT_SUMMARY}}`, `{{MUST_VERIFY}}`, `{{PREFER_COMMANDS}}`, `{{ENVIRONMENT_MAP}}` from detected project shape.

### Phase 9: Create manifest.yaml

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
constraints:
  architecture_doc: .claude/harness/constraints/architecture.md
  architecture_check: .claude/harness/constraints/check-architecture.sh
workflow:
  contract_required: true
  qa_required_for_repo_mutations: true
  persistence_required: true
  docs_sync_required: true
  document_critic: critic-document
  constraints_enforced: true
```

### Phase 10: Generate executable QA scripts

Adapt to detected project shape:
- **web app**: browser-first smoke + route probes + persistence checks
- **api**: curl/http smoke + DB or side-effect checks
- **cli/worker**: example commands + log/output checks
- **library**: tests/examples + minimal reproducible command

### Phase 11: Obvious root candidates

If obvious domain boundaries were detected (auth, billing, etc.):
- Propose to critic-document via `harness:critic-document`
- Only create if PASS
- Each root gets its own CLAUDE.md index

### Phase 12: Tiered confirmation

**NOT everything needs user approval.** Tier the confirmation:

| Tier | Action | Requires approval? |
|------|--------|-------------------|
| **Auto** | Create new files in empty directories | No — proceed |
| **Auto** | Create `.claude/harness/` structure | No — proceed |
| **Auto** | Create `doc/` structure | No — proceed |
| **Auto** | Create `scripts/harness/` scaffolding | No — proceed |
| **Auto** | Generate critic playbooks | No — proceed |
| **Auto** | Generate constraints | No — proceed |
| **Confirm** | Modify existing `CLAUDE.md` | Yes — show diff |
| **Confirm** | Modify existing `.gitignore` | Yes — show diff |
| **Confirm** | Modify existing `.claude/settings.json` | Yes — show diff |
| **Confirm** | Create files that conflict with existing | Yes — show conflict |
| **Confirm** | Brownfield truth extraction results | Yes — show classified facts |

For **Confirm** tier items, present a focused diff:
- Files to modify (with before/after)
- Conflicts detected
- Inferred facts that need user validation (brownfield)

Proceed with Auto tier immediately. Batch Confirm tier items into a single review.

### Phase 13: Write files

After auto + confirmed items are resolved, write all files. Then:
- `chmod +x scripts/harness/*.sh`
- `sed -i 's/\r$//' scripts/harness/*.sh`
- `chmod +x .claude/harness/constraints/check-architecture.sh` (if generated)

### Phase 14: Setup .gitignore

Harness operational artifacts (tasks, maintenance queue, archive) are ephemeral and should not be committed.

- If `.gitignore` exists, check whether it already contains harness entries.
- If not, append the contents of `${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/gitignore-harness`.
- If `.gitignore` does not exist, create it with those entries.

Do NOT gitignore:
- `doc/` — durable knowledge (the whole point)
- `.claude/harness/critics/` — project-specific playbooks
- `.claude/harness/manifest.yaml` — initialization marker
- `.claude/harness/constraints/` — architectural rules and checkers
- `.claude/settings.json` — agent config
- `scripts/harness/` — executable QA scaffolding

### Phase 15: Activate harness agent

Read `.claude/settings.json` (create if missing). Merge the agent field:
```json
{
  "agent": "harness:harness"
}
```
Preserve any existing fields.

### Phase 16: Finish

Report:
- files created or updated
- roots registered
- notes created (REQ/OBS/INF counts)
- constraints generated
- what was inferred vs confirmed
- **For brownfield**: extracted truths summary (confirmed / inferred / conflicting)
- remaining unknowns
- maintenance queue items (if any)
- reminder: user can now work in plain language — the harness runtime is active

## Guardrails

- Keep generated files concise and human-editable
- Do not fill templates with fake certainty
- Mark uncertain items as INF with `verify_by`
- Prefer repository evidence over assumptions
- **Brownfield**: never silently override existing project structure
- **Brownfield**: classify extracted facts honestly — "inferred" is not "confirmed"
- **Constraints**: always generate rule + checker pairs, never rules alone
- **Confirmation**: minimize friction — only ask about destructive/overwrite operations
