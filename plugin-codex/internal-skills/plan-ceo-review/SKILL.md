---
name: plan-ceo-review
user-invocable: false
description: |
  CEO/founder-mode plan review. Challenge premises, test ambition, and make
  explicit scope decisions in SCOPE EXPANSION, SELECTIVE EXPANSION, HOLD SCOPE,
  or SCOPE REDUCTION mode.
---

# CEO Plan Review

> **Codex runtime notes:** Use conversational prose with lettered options and wait for
> the next turn where the Claude source says `AskUserQuestion`. Use bare Harness MCP
> names and `HARNESS_PLUGIN_ROOT` if needed. Apply edits with `apply_patch`. The CEO-plan
> adversarial spec pass is single-voice in this skill because it has no Agent primitive.

Review the plan only. Do not implement code. Apply the shared rules in
`plugin-codex/internal-skills/plan/SKILL.md` for evidence, context recovery, repository
ownership, search, completeness, and conversational asks.

## Contract

- The user owns every scope change. Never silently add, remove, or defer scope.
- Ask one decision per finding in plain prose. Give 2-3 lettered options, recommend one
  with why, and wait for the next turn. If there is no finding, say so and continue.
- Base claims on the current plan, repository evidence, task-local decisions, and
  relevant external evidence. Do not re-ask recorded decisions.
- Name files, interfaces, failure modes, user effects, effort, and risk.
- No `TBD`. Every accepted finding needs an implementation and verification path.
- Required review sections may be not applicable only with evidence.
- Finish with exactly `DONE`, `DONE_WITH_CONCERNS`, or `BLOCKED`.

## Applicability and modes

Use this lens for strategic scope, ambition, premise, or product-direction review.
Choose and then hold one mode:

| Mode | Scope posture | Required behavior |
|---|---|---|
| SCOPE EXPANSION | Push up | Define the 10x experience and platonic ideal; offer each expansion for opt-in. |
| SELECTIVE EXPANSION | Hold baseline, offer additions | Review baseline rigorously; offer each expansion neutrally for cherry-picking. |
| HOLD SCOPE | Preserve | Strengthen architecture, safety, testing, operations, and rollout without expansion. |
| SCOPE REDUCTION | Push down | Identify the smallest coherent outcome and ask before removing anything. |

Defaults: greenfield → EXPANSION; enhancement → SELECTIVE; bug/refactor → HOLD;
plans touching more than 15 files → consider REDUCTION. Explicit user language wins.
Expansion decisions use `add to scope`, `defer to TODOS.md`, or `skip`. Accepted
items become part of every later section; rejected items remain in `NOT in scope`.

## Evidence preflight

Before Step 0:

1. Read the active task plan and task-local prior decisions.
2. Inspect recent history, current diff, TODO/FIXME hotspots, architecture docs,
   relevant design docs, and prior learnings/patterns.
3. Search the product/category landscape and synthesize tried-and-true practice,
   current practice, and first-principles disagreement.
4. In EXPANSION or SELECTIVE mode, identify 2-3 repository patterns worth copying
   and 1-2 anti-patterns.
5. If the problem itself is still unstable, offer `harness:setup` scope sharpening.

## Step 0: premise and scope decisions

Complete these before the technical review.

### A. Premise challenge

List the plan's 3-5 load-bearing premises:

| Premise | stated / assumed / proven / unknown | blast radius if wrong | evidence |
|---|---|---|---|

Ask about the highest-blast-radius assumed or unknown premise. Determine the actual
user/business outcome, whether the plan solves it directly, and the cost of doing
nothing.

### B. Existing-code leverage

Map every sub-problem:

| Sub-problem | Existing asset | reuse / refactor / rebuild | reason |
|---|---|---|---|

`None found` is valid. Rebuild requires a concrete mismatch; prefer capturing existing
flow outputs over parallel machinery.

### C. Dream state and alternatives

Describe current state → this plan → 12-month ideal. Flag path dependency away from
the ideal. Compare at least two credible approaches:

| Approach | Effort | Risk | Pros | Cons |
|---|---|---|---|---|

One must be the smallest viable diff and one the best long-term architecture. Give
them equal weight and obtain user approval before selecting a mode.

### D. Mode-specific scope

- EXPANSION: define the 10x experience, platonic ideal, and at least five small delight
  opportunities; convert them into individual opt-in proposals.
- SELECTIVE: first find avoidable complexity and the minimum baseline, then surface
  10x, delight, and platform opportunities as individual choices.
- HOLD: challenge excess complexity while preserving the approved boundary.
- REDUCTION: split must-ship value from follow-up work and ask before every cut.

For EXPANSION and SELECTIVE, persist decisions in
`doc/harness/tasks/<task-id>/ceo-plan.md` with vision, mode, proposal table, accepted
scope, and deferred items. Re-read it adversarially for completeness, consistency,
clarity, scope, and feasibility; fix and retry up to three times, then record stable
unresolved concerns.

### E. Temporal decisions and mode confirmation

Surface decisions an implementer would otherwise meet during foundations, core logic,
integration, and verification. Show human and agent-assisted effort when useful.
Confirm the mode and chosen implementation approach before proceeding.

## Technical review

Evaluate every applicable section. For each finding, record evidence, user impact,
recommended resolution, owner, and verification; obtain the user's decision before
writing it into the plan.

### 1. Architecture

Map boundaries, dependency changes, state machines, coupling, 10x/100x constraints,
single points of failure, auth/data boundaries, integration failures, and rollback.
Diagram the system and every non-trivial state/data flow. In expansion modes also test
architectural elegance, platform leverage, and fit of accepted additions.

### 2. Error and rescue map

For every fallible method/path, name the trigger and exception/error class, rescue
behavior, retry/degradation/re-raise policy, log context, user-visible result, and test.
Flag catch-all handling and silent continuation. For AI calls separately cover empty,
malformed, invalid structured output, refusal, timeout, and upstream throttling.

### 3. Security and threat model

Assess new surfaces, validation boundaries, authorization/IDOR, secrets, dependency
risk, data classification, SQL/command/template/prompt injection, and audit logging.
For each threat state likelihood, impact, mitigation, and test.

### 4. Data and interaction edges

Trace happy, nil, empty, invalid, upstream-error, conflict, stale, partial, and
concurrent paths. For UI/async work cover duplicate action, navigation away, timeout,
retry while active, large/zero result sets, duplicate jobs, and partial completion.

### 5. Code quality

Check repository fit, duplication, naming, error patterns, defensive boundaries,
over/under-engineering, and branch-heavy methods. Prefer the smallest explicit design
that covers the accepted behavior.

### 6. Tests

Inventory every new UX flow, data flow, branch, async job, integration, and rescue
path. Map each to unit/integration/system/E2E evidence and happy, failure, boundary,
concurrency, hostile-QA, and chaos cases as applicable. Check pyramid balance,
flakiness, load needs, and required prompt/LLM evals.

### 7. Performance

Check query shape and indexes, memory bounds, caches, job payload/runtime/retry,
estimated p99 slow paths, and DB/Redis/HTTP pool pressure.

### 8. Observability

Require signals sufficient to determine success and reconstruct failures: structured
logs, metrics, trace propagation, alerts, dashboards, admin tooling, and runbooks.
Expansion modes may propose operational delight only through explicit opt-in.

### 9. Deployment

Cover migration compatibility and locks, feature flags, rollout ordering, mixed-version
windows, staging parity, rollback steps, smoke tests, and first-five-minute/first-hour
checks.

### 10. Long-term trajectory

Record code/operations/test/docs debt, path dependency, knowledge concentration,
ecosystem fit, one-year readability, and reversibility from 1-5. In expansion modes
also assess phase-2/platform trajectory and whether accepted additions are load-bearing.

### 11. Design and UX

Run only when UI scope exists. Review information hierarchy; loading, empty, error,
success, and partial states; journey coherence; design-system fit; responsive behavior;
accessibility; trust; and generic-UI risk. Diagram screens/states. Recommend the deeper
design lens for material UI scope.

## Required plan outputs

The reviewed plan must contain:

- mode and selected approach;
- premise table, existing-code leverage map, and current→plan→ideal delta;
- accepted scope plus `NOT in scope` with rationale and follow-up home;
- architecture/data/state/error/deploy/rollback diagrams where applicable;
- full Error & Rescue Registry and Failure Modes Registry;
- security, test, observability, rollout, and long-term findings;
- expansion decision record and CEO plan path when applicable;
- proposed TODOs, each decided separately;
- unresolved decisions and reviewer concerns;
- compact completion summary with issue/gap counts by section.

A failure row is critical when it can remain unrescued or partial and is untested or
silent. Critical gaps and any movement away from the 12-month ideal require an explicit
user decision; never auto-approve them.
