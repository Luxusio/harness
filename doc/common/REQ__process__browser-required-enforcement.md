# REQ process browser-required-enforcement
tags: [req, root:common, source:task, status:active]
summary: browser_required: true in TASK_STATE.yaml must have real enforcement power via critic-runtime and setup templates. Plan skill no longer participates.
source: TASK__browser-required-enforce (2026-04-08)
updated: 2026-04-08
superseded_detail: TASK__revert-plan-skill-browser (2026-04-08) removed plan skill enforcement; critic-runtime is now the sole gate.
freshness: current
verified_at: 2026-04-08T00:00:00Z
confidence: high
invalidated_by_paths:
  - plugin/agents/critic-runtime.md
  - plugin/skills/setup/templates/doc/harness/critics/runtime.md

## Rule

When `TASK_STATE.yaml` carries `browser_required: true`, the following pipeline layers must honour the flag:

| Layer | Required behavior |
|-------|-------------------|
| `critic-runtime.md` | Read TASK_STATE.yaml and check `browser_required`. If `true`: load browser-first calibration (mandatory, not optional) AND require browser verification steps as a hard gate. |
| setup template `runtime.md` | Trigger condition for browser-first block must include `TASK_STATE.yaml browser_required: true` alongside manifest-level triggers (`manifest.browser.enabled: true`, `qa.default_mode: browser-first`). |

## Removed enforcement (2026-04-08)

`plan/SKILL.md` previously injected browser verification steps into PLAN.md when `browser_required: true`. This was removed in TASK__revert-plan-skill-browser as unnecessary complexity. The plan skill now has zero occurrences of `browser_required`. Enforcement is concentrated in critic-runtime as a hard gate, which is sufficient.

## Constraint

All enforcement is conditional on `browser_required: true`. When the flag is `false` or absent, existing non-browser verification paths must remain unchanged. This invariant must not be regressed.

## Why

Prior to TASK__browser-required-enforce, the flag existed in TASK_STATE.yaml schema but was a dead value — critic-runtime and the plan skill did not inspect it, and the setup template only checked manifest-level triggers. Tasks that legitimately required browser verification had no automated enforcement of that requirement.
