# Execution Modes Reference

updated: 2026-04-01

> **Compatibility / maintenance reference only.**
> This document is NOT the agent-facing canonical routing source.
> For task routing, use: `python3 plugin/scripts/hctl.py context --task-dir <dir> --json`
> Execution mode is derived automatically by `hctl start` and stored as a compatibility field in TASK_STATE.yaml.

> **Note:** Execution mode (`light | standard | sprinted`) and orchestration mode (`solo | subagents | team`) are orthogonal axes. This document covers execution modes. See `plugin/docs/orchestration-modes.md` for orchestration modes.

This document is a compatibility reference for the three execution modes. Agents should rely on `hctl context` output rather than reading this document at runtime.

---

## Mode Definitions

### Mode A — light

Low-ceremony mode for small, contained tasks. Reduces artifact requirements and uses a simplified critic rubric.

**Intended for:** Docs-only changes, single-file fixes, investigations, answers, low-blast-radius edits.

**Loop:** compact plan contract → implement → single runtime/doc check

### Mode B — standard

The default mode. Full v2 loop with all artifacts required.

**Intended for:** Normal feature additions, single-root bugfixes, standard QA tasks.

**Loop:** plan contract → critic-plan PASS → implement → self-check breadcrumbs → runtime QA → writer/DOC_SYNC → critic-document → close

### Mode C — sprinted

Enhanced mode for high-risk, cross-surface, or structurally complex tasks. Adds sprint contract, detailed risk matrix, and explicit rollback steps.

**Intended for:** Cross-root changes, multi-surface changes (app+api+db), destructive operations (migrations, schema changes, major dependency upgrades), prior-blocked tasks, ambiguous specs.

**Loop:** enhanced plan (sprint contract + risk matrix + rollback) → critic-plan PASS (enhanced rubric) → implement → self-check breadcrumbs → runtime QA → writer/DOC_SYNC → critic-document → close

#### Architecture check promotion in sprinted mode

Architecture constraint checks are hints by default and never affect verdicts for light or standard mode tasks. In sprinted mode, the check can be **automatically promoted to required evidence** when the task carries structural risk.

**All three conditions must be met for promotion:**
1. `execution_mode` is `sprinted` (set in TASK_STATE.yaml)
2. `risk_tags` contain at least one of: `structural`, `migration`, `schema`, `cross-root`
3. `doc/harness/constraints/check-architecture.*` file exists in the repo

**When all three conditions are met:**
- The architecture check script is executed during runtime QA
- Its output is included in the evidence bundle under an "Architecture Check" section
- A failing architecture check blocks the runtime PASS unless a deviation justification is provided

**When any condition is not met (the common case):**
- Architecture checks remain advisory hints
- Script absence is not a warning — most repos have no `doc/harness/constraints/` directory and this is expected
- No verdict impact, no configuration required

**Example: promotion kicks in**
> Task: add a new shared package to a monorepo (execution_mode: sprinted, risk_tags: [cross-root, structural], check-architecture.sh exists)
> → Architecture check is required for runtime PASS

**Example: promotion does not kick in**
> Task: fix a bug in a single API route (execution_mode: standard, no structural risk_tags)
> → Architecture check is a hint only; absence or failure has no verdict impact

---

## Signal Matrix

The harness evaluates these signals after lane classification to select an execution mode. Higher-weight mode wins when signals conflict.

| Signal | Mode indicated |
|--------|---------------|
| Lane = `answer` | light |
| Lane = `investigate` | light |
| Lane = `docs-sync` | light |
| Single file change | light |
| Small predicted diff | light |
| No API/DB/infra surfaces | light |
| Normal feature, single root | standard |
| `browser_required: true` (no prior failures) | standard |
| 2+ roots estimated from request | sprinted |
| 2+ repo surfaces (e.g., app+api, app+db, app+infra) | sprinted |
| Prior `blocked_env` state | sprinted |
| Runtime FAIL count ≥ 2 | sprinted |
| Destructive/structural flag (migration, schema, major dep upgrade) | sprinted |
| Large predicted diff | sprinted |
| `browser_required: true` AND FAIL count ≥ 2 | sprinted |
| Ambiguous spec requiring significant assumptions | sprinted |

**Tie-break rule:** When signals point to different modes, the higher mode wins (sprinted > standard > light).

---

## Artifact Requirements Per Mode

| Artifact | light | standard | sprinted |
|----------|-------|----------|----------|
| `TASK_STATE.yaml` | required | required | required |
| `REQUEST.md` | required | required | required |
| `PLAN.md` (compact) | required | — | — |
| `PLAN.md` (full) | — | required | — |
| `PLAN.md` (enhanced + sprint contract) | — | — | required |
| `CRITIC__plan.md` (simplified rubric) | required | — | — |
| `CRITIC__plan.md` (full rubric) | — | required | — |
| `CRITIC__plan.md` (enhanced rubric) | — | — | required |
| `HANDOFF.md` (minimal) | required | required | required |
| `DOC_SYNC.md` | if repo-mutating | if repo-mutating | if repo-mutating |
| `CRITIC__runtime.md` | if repo-mutating | if repo-mutating | if repo-mutating |
| `CRITIC__document.md` | if docs changed | if docs changed | if docs changed |
| Sprint contract (in PLAN.md) | no | no | required |
| Risk matrix (in PLAN.md) | no | no | required |
| Rollback steps (in PLAN.md) | no | no | required |
| Dependency graph (in PLAN.md) | no | no | required |

---

## Plan Format Summary

### Light — compact format

Required sections: Scope in, Acceptance criteria, Verification contract, Required doc sync.

Omitted sections (unless genuinely needed): Scope out, User-visible outcomes, Touched files/roots, QA mode, Hard fail conditions, Risks/rollback, Open blockers.

### Standard — full format

All PLAN.md sections required. See `plugin/skills/plan/SKILL.md` for the full template.

### Sprinted — enhanced format

All standard sections required, plus:
- **Sprint contract**: surfaces, roots, rollback trigger, staged delivery flag
- **Risk matrix**: table with likelihood, impact, mitigation per identified risk
- **Rollback steps**: explicit ordered steps (vague "revert" is not sufficient)
- **Dependency graph**: cross-component/cross-service dependencies

---

## Critic Rubric Summary

### Light rubric (critic-plan, mode A)

Checks: scope defined, acceptance criteria testable, verification contract executable.

Does NOT fail for: missing Scope out, missing Hard fail conditions, missing Risks/rollback.

### Standard rubric (critic-plan, mode B)

Full rubric — all PLAN.md fields required. See `plugin/agents/critic-plan.md` for complete evaluation criteria.

### Sprinted rubric (critic-plan, mode C)

All standard checks plus:
- Sprint contract present and complete (surfaces, roots, rollback trigger, staged delivery)
- Risk matrix present with likelihood/impact/mitigation per risk
- Rollback steps specific and ordered (not vague)
- Dependency graph present for multi-surface changes

---

## Auto-Escalation Rules

Execution mode may upgrade mid-task but **never downgrade**.

| Trigger | Escalation |
|---------|-----------|
| Actual diff grows beyond initial estimate | light → standard or standard → sprinted |
| Additional roots discovered during implementation | standard → sprinted |
| Runtime FAIL reveals systemic issues | standard → sprinted |
| Destructive flag discovered post-plan | any → sprinted |
| `blocked_env` encountered mid-task | any → sprinted |

When escalating: update `execution_mode` in `TASK_STATE.yaml`, re-plan with the new format, re-run critic-plan with the new rubric.

---

## Planning Mode

Planning mode is orthogonal to execution mode. It controls planning depth, not risk level or evaluation rigor.

### planning_mode values

| Value | When selected | Effect |
|-------|--------------|--------|
| `standard` | Default. Bugfixes, refactors, investigations, single-endpoint features. | Standard PLAN.md flow. |
| `broad-build` | Broad product/build requests: greenfield apps, dashboards, multi-root platforms. | Generates longform spec trio (`01_product_spec.md`, `02_design_language.md`, `03_architecture.md`) before PLAN.md. |

### broad-build trigger conditions

**Required (ALL):** lane is `build`, request is a broad product/build request.

**Plus 2+ of:** short high-level request (1-4 sentences), greenfield/new app/dashboard/site, no file path anchors, browser/UI emphasis, 2+ roots estimated, too many assumptions needed for immediate PLAN.md.

**Exclusions (ANY blocks broad-build):** clear bugfix, single endpoint/component, performance/enforcement/refactor, request already has detailed technical spec.

### broad-build artifacts

| Artifact | Purpose | NOT for |
|----------|---------|---------|
| `01_product_spec.md` | Problem definition, users, flows, must/should/nice-to-have, out of scope | Code design, file structure |
| `02_design_language.md` | Interaction model, layout, UI/UX constraints, visual priorities | CSS specs, component APIs |
| `03_architecture.md` | High-level modules, data flow, integration points, persistence | Class hierarchies, function signatures |

These narrow a broad request into a concrete PLAN.md contract. They do NOT replace PLAN.md.

### Backward compatibility

`planning_mode` field absence = `standard`. Older tasks are unaffected.

---

## CHECKS.yaml close_gate Policy

CHECKS.yaml includes a top-level `close_gate` field that controls close behavior:

| Value | Effect | When set |
|-------|--------|----------|
| `standard` | Only `failed` criteria block close (existing behavior) | Default, legacy tasks |
| `strict_high_risk` | ALL non-`passed` criteria block close (`planned`, `implemented_candidate`, `failed`, `blocked`) | High-risk tasks |

### strict_high_risk triggers

Set `close_gate: strict_high_risk` when ANY of:
- `execution_mode: sprinted`
- `review_overlays` contains `security` or `performance`
- `risk_tags` contains `structural`, `migration`, `schema`, or `cross-root`

### Backward compatibility

`close_gate` field absence = `standard`. Older CHECKS.yaml files and tasks without CHECKS.yaml are unaffected.

### Scope removal in strict mode

To remove a criterion from a strict task, re-plan (update PLAN.md acceptance criteria, re-sync CHECKS.yaml, re-pass critic-plan). No runtime exemptions or ad-hoc waivers.

---

## Review Overlay Integration

Review overlays are orthogonal to execution modes — any mode (light, standard, sprinted) can have overlays active. Overlays add domain-specific review criteria on top of mode-based rubrics.

### How overlays work

1. During planning (step 3.5), the harness evaluates prompt keywords and predicted file paths
2. Matching overlays are recorded in `TASK_STATE.yaml` as `review_overlays: [security, performance, ...]`
3. Critics read the overlay list and apply additional checks when present
4. If no overlays match, `review_overlays: []` — critics operate with standard behavior

### Available overlays

| Overlay | Trigger signals | Additional critic checks |
|---------|----------------|------------------------|
| `security` | auth/login/token/injection keywords, auth/api/middleware paths | Threat surface, permission boundary, secret handling, authz evidence |
| `performance` | performance/latency/benchmark keywords, hot path/DB/cache paths | Performance contract in plan, numeric before/after evidence |
| `frontend-refactor` | component/ui/hook/state keywords, app/components/pages paths | State boundary, dependency direction, testability, UI interaction evidence |
| `observability` | readiness true + (performance overlay, fail count >= 2, or investigation keywords: intermittent/flaky/cross-service/latency/p95/p99) | Observability evidence in runtime bundle (logs/metrics/traces when stack UP; advisory fallback when DOWN) |

### Overlay + mode combinations

| Mode | Empty overlays | With overlays |
|------|---------------|---------------|
| light | Simplified rubric, minimal artifacts | Simplified rubric + overlay-specific checks |
| standard | Full rubric, all artifacts | Full rubric + overlay-specific checks |
| sprinted | Enhanced rubric + sprint contract | Enhanced rubric + sprint contract + overlay-specific checks |

Overlays never change the execution mode itself — they only add review depth within the selected mode.

## TASK_STATE.yaml Storage

`execution_mode` is stored as a top-level field immediately after `lane`:

```yaml
task_id: TASK__<slug>
status: planned
lane: <sub-lane>
execution_mode: light | standard | sprinted
planning_mode: standard | broad-build
mutates_repo: <true|false>
...
```

The field is set by the harness (or plan skill) after lane classification and before any artifact creation. Critics read this field to select the correct rubric.

---

## Examples

### Example 1: Docs-only change → light

**Request:** "Update the README to add the new API endpoint."

**Signals:** Lane = `docs-sync`, single file, small diff, no code surfaces.

**Mode selected:** light

**Artifacts:** Compact PLAN.md (Scope in + Acceptance criteria + Verification contract), minimal HANDOFF.md, DOC_SYNC.md (repo-mutating), CRITIC__runtime.md.

---

### Example 2: Normal feature → standard

**Request:** "Add pagination to the `/users` endpoint."

**Signals:** Single root (api), standard feature, moderate diff, no destructive changes.

**Mode selected:** standard

**Artifacts:** Full PLAN.md with all sections, full CRITIC__plan.md, HANDOFF.md, DOC_SYNC.md, CRITIC__runtime.md.

---

### Example 3: Frontend + API + DB change → sprinted

**Request:** "Add a new billing module with a new DB table, API endpoints, and a React billing page."

**Signals:** 3 surfaces (app+api+db), 3+ roots, large predicted diff, schema change (new table).

**Mode selected:** sprinted

**Artifacts:** Enhanced PLAN.md with sprint contract, risk matrix, rollback steps, dependency graph. Enhanced CRITIC__plan.md. Full runtime + document verification.

---

### Example 4: Prior blocked task resumes → sprinted

**Request:** Resuming a task that previously hit `blocked_env`.

**Signals:** Prior `blocked_env` state in TASK_STATE.yaml.

**Mode selected:** sprinted (regardless of original mode)

**Rationale:** A previously blocked task has demonstrated environmental complexity that warrants stronger planning and evaluation.
