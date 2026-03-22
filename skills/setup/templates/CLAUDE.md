# {{PROJECT_NAME}} AI Operating Manual

This file is the primary instruction surface for AI-assisted work in this repository. It provides **reference data** for the runtime loop. The orchestrator agent handles the step-by-step procedure — this file tells it where to find things and what rules to follow.

## How to handle requests

Follow the orchestrator's runtime loop for every substantial request. This section provides the reference data the loop needs.

### 1. Project context
- Manifest: `.claude-harness/manifest.yaml`
- Router: `.claude-harness/router.yaml`
- Approvals: `.claude-harness/policies/approvals.yaml`
- Memory policy: `.claude-harness/policies/memory-policy.yaml`

### 2. Intent routing

| Intent | Signals | Workflow summary |
|--------|---------|-----------------|
| feature | build, add, create, implement, new | `.claude-harness/workflows/feature.md` |
| bugfix | fix, broken, error, regression, failing | `.claude-harness/workflows/bugfix.md` |
| tests | test, coverage, case, prove | `.claude-harness/workflows/tests.md` |
| refactor | refactor, cleanup, simplify, untangle | `.claude-harness/workflows/refactor.md` |
| docs | document, update docs, write guide | `.claude-harness/workflows/docs-sync.md` |
| decision | from now on, always, never, policy | `.claude-harness/workflows/decision-capture.md` |
| brownfield | legacy, unfamiliar, old code, map | `.claude-harness/workflows/brownfield-adoption.md` |
| validation | validate, verify, prove, evidence | `.claude-harness/workflows/validation-loop.md` |
| architecture | boundary, dependency, layer, module | `.claude-harness/workflows/architecture-guardrails.md` |
| memory | remember, record, capture, store | `.claude-harness/workflows/repo-memory-policy.md` |

### 3. Memory files for sync
- Recent decisions: `.claude-harness/state/recent-decisions.md`
- Unknowns: `.claude-harness/state/unknowns.md`
- Current task: `.claude-harness/state/current-task.yaml`
- Last session summary: `.claude-harness/state/last-session-summary.md` — overwritten each session, what was done last time
- Constraints: `docs/constraints/project-constraints.md`
- Decisions: `docs/decisions/`
- Domain knowledge: `docs/domains/`
- Runbooks: `docs/runbooks/`
- Architecture: `docs/architecture/`

### 4. Summary output format
After every substantial task, report:
- **Changed**: what was modified
- **Validated**: what evidence proves the change works
- **Recorded**: what durable knowledge was captured
- **Unknown**: what remains unresolved
- **Follow-up**: what needs attention next

## Memory rules

1. **Never store hypotheses as confirmed facts.** Hypotheses go in `.claude-harness/state/unknowns.md`. Only confirmed findings go in `docs/`.
2. **Prefer executable memory over prose.** Enforcement priority: test > validation script > config assertion > documentation.
3. **Append to recent-decisions.md after any durable change.** Format: `- [YYYY-MM-DD] <type>: <description>`

## Always do

- Read `.claude-harness/manifest.yaml` before substantial work.
- Load only the domain docs and policies relevant to the current task.
- Check `.claude-harness/policies/approvals.yaml` before touching high-risk zones.
- Treat `docs/constraints/` and `docs/decisions/` as authoritative for confirmed project rules.
- Run the narrowest validation that proves the change, then widen only if risk requires it.

## Validation commands

- build: `{{BUILD_COMMAND}}`
- test: `{{TEST_COMMAND}}`
- lint/typecheck: `{{LINT_COMMAND}}`
- dev: `{{DEV_COMMAND}}`

## Project info

- mode: `{{PROJECT_MODE}}`
- type: `{{PROJECT_TYPE}}`

## Key journeys

{{KEY_JOURNEYS_BULLETS}}

## Brownfield guidance

{{BROWNFIELD_SECTION}}
