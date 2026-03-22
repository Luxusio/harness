# harness AI Operating Manual

This file is the primary instruction surface for AI-assisted work in **this repository** (i.e., the harness plugin repo itself). It provides **reference data** for the runtime loop. The orchestrator agent handles the step-by-step procedure — this file tells it where to find things and what rules to follow.

> **Source of truth:** The shipped plugin source lives under `plugin/`. No parallel prompt tree exists at the root. Plugin development modifies `plugin/agents`, `plugin/skills`, `plugin/hooks`, and `plugin/scripts`.

## How to handle requests

Follow the orchestrator's runtime loop for every substantial request. This section provides the reference data the loop needs.

### 1. Project context
- Manifest: `harness/manifest.yaml`
- Router: `harness/router.yaml`
- Approvals: `harness/policies/approvals.yaml`
- Memory policy: `harness/policies/memory-policy.yaml`

### 2. Intent routing

| Intent | Signals | Skill |
|--------|---------|-------|
| answer / explain | why, how, what, explain | Direct answer with context |
| requirements | document, spec, clarify, plan | requirements-curator → docs-scribe |
| feature | build, add, create, implement, new | feature-workflow |
| bugfix | fix, broken, error, regression, failing | bugfix-workflow |
| tests | test, coverage, case, prove | test-expansion |
| refactor | refactor, cleanup, simplify, untangle | refactor-workflow |
| docs | document, update docs, write guide | docs-sync |
| decision | from now on, always, never, policy | decision-capture |
| brownfield | legacy, unfamiliar, old code, map | brownfield-adoption |
| validation | validate, verify, prove, evidence | validation-loop |
| architecture | boundary, dependency, layer, module | architecture-guardrails |
| memory | remember, record, capture, store | repo-memory-policy |
| other | (no match) | Direct response with manifest context |

### 3. Memory files for sync
- Recent decisions: `harness/state/recent-decisions.md`
- Unknowns: `harness/state/unknowns.md`
- Requirements: `harness/docs/requirements/`
- Current task: `harness/state/current-task.yaml`
- Last session summary: `harness/state/last-session-summary.md` — overwritten each session, what was done last time
- Constraints: `harness/docs/constraints/project-constraints.md`
- Decisions: `harness/docs/decisions/`
- Domain knowledge: `harness/docs/domains/`
- Runbooks: `harness/docs/runbooks/`
- Architecture: `harness/docs/architecture/`

### 4. Summary output format
After every substantial task, report:
- **Changed**: what was modified
- **Validated**: what evidence proves the change works
- **Recorded**: what durable knowledge was captured
- **Unknown**: what remains unresolved
- **Follow-up**: what needs attention next

## Memory rules

1. **Never store hypotheses as confirmed facts.** Hypotheses go in `harness/state/unknowns.md`. Only confirmed findings go in `harness/docs/`.
2. **Prefer executable memory over prose.** Enforcement priority: test > validation script > config assertion > documentation.
3. **Record when future work is affected.** Append to `harness/state/recent-decisions.md` when: a rule/constraint changed, a non-obvious root cause was found, an architecture pattern was clarified, a risk zone was identified, or a workflow was modified. Do NOT record routine code changes, typo fixes, or one-off answers. Format: `- [YYYY-MM-DD] <type>: <description>`

## Always do

- Read `harness/manifest.yaml` before substantial work.
- Load only the domain docs and policies relevant to the current task.
- Check `harness/policies/approvals.yaml` before touching high-risk zones.
- Treat `harness/docs/constraints/` and `harness/docs/decisions/` as authoritative for confirmed project rules.
- Run the narrowest validation that proves the change, then widen only if risk requires it.

## Validation commands

- build: (none — plugin is config/docs only)
- test: `claude --plugin-dir ./plugin --print "list harness skills"`
- lint/typecheck: (none)
- dev: `claude --plugin-dir ./plugin`

## Project info

- mode: `greenfield`
- type: `library`

## Key journeys

- Plugin development: modify `plugin/skills`, `plugin/agents`, `plugin/hooks`, `plugin/scripts` → test with `claude --plugin-dir ./plugin` → verify shipped behavior loads and runs correctly
- Marketplace deployment: push to GitHub → `/plugin marketplace add` → `/plugin install` → verify in fresh session

## Command surface

- `/harness:setup` — bootstrap a project (run once)
- `/harness:validate` — optional diagnostic to check control plane health
- Daily work proceeds in plain language; no slash commands are required after setup

## Brownfield guidance

This is a greenfield project. Consider creating `src/`, `tests/`, `docs/` as the codebase grows. See `harness/docs/architecture/README.md` for scaffold hints.
