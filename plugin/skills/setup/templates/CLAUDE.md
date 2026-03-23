# {{PROJECT_NAME}} AI Operating Manual

This file is the primary instruction surface for AI-assisted work in this repository. It provides **reference data** for the runtime loop. The orchestrator agent handles the step-by-step procedure — this file tells it where to find things and what rules to follow.

## How to handle requests

Follow the orchestrator's runtime loop for every substantial request. This section provides the reference data the loop needs.

### 1. Project context
- Manifest: `harness/manifest.yaml`
- Router: `harness/router.yaml`
- Approvals: `harness/policies/approvals.yaml`
- Memory policy: `harness/policies/memory-policy.yaml`

### Intent routing

`harness/router.yaml` is the authoritative routing file.
Use it for:
- intent names and signal examples
- execution mode (`direct_response`, `specialists`, `skill`)
- internal workflow procedure selection
- primary agent order

Do not maintain a second routing table in this file.

Key rules:
- Direct answers do not mutate files
- Workflow intents must check approvals before risky edits
- Memory sync happens after substantial mutating work

### Approval gates and risk context

- `harness/policies/approvals.yaml` — approval gate source of truth. The orchestrator reads this to decide whether to ask-first before touching a sensitive area.
- `harness/manifest.yaml` `risk_zones` field — descriptive risk context. Lists which paths/areas are sensitive. Informs setup and human review but is not itself an enforcement gate.

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
- Check `harness/policies/approvals.yaml` (approval gates) before touching sensitive areas.
- Treat `harness/docs/constraints/` and `harness/docs/decisions/` as authoritative for confirmed project rules.
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
