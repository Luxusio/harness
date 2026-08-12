---
name: plan-eng-review
user-invocable: false
description: Review and lock an implementation plan's architecture, tests, failure handling, and performance.
---

> **Codex runtime delta:** Ask questions as plain conversational prose with
> lettered options and wait for the next turn; Codex has no structured
> `AskUserQuestion` tool. Use `apply_patch` for approved plan edits. Read the
> installed threat/rollback rubric path named below.

# Engineering plan review

Review and edit the plan only; do not implement product code. Apply the shared
plan rules in `plugin-codex/internal-skills/plan/SKILL.md`, including
search-before-building, repo ownership, and conversational ask format. Prefer the smallest explicit,
well-tested design; flag duplication, accidental complexity, and missing edges.

## Premise and scope gate

Read the plan, referenced design/spec files, every existing file it proposes to
change or extend, relevant tests, root `TODOS.md`, recent commits on those
surfaces, and up to five relevant entries from `doc/harness/learnings.jsonl`.

Before detailed review:

1. Verify referenced files, imports, dependencies, and test commands exist.
2. Check for architecture/data-flow diagrams, error handling, test plan, and
   rollback when the change is risky.
3. Map each sub-problem to an existing implementation candidate:

```text
| Sub-problem | Existing candidate | Reuse/extend/replace/new | Why |
```

4. Compare the proposed scope with the minimum viable diff. New modules need a
   reason. Plans touching 8+ files, 2+ services, or a new top-level module must
   justify that complexity or shrink.
5. Reconcile overlapping `TODOS.md` entries: include them in acceptance criteria
   or keep them explicitly deferred.
6. Challenge the premise, boundaries, migration strategy, and irreversible
   choices. For a high-impact ambiguity, stop and ask with 2-3 options covering
   effort, blast radius, and rollback.

Emit a `Review Readiness Dashboard`:

```text
| Item | Status |
| PLAN.md | present/missing |
| Architecture/data-flow diagram | present/missing |
| Test plan | present/missing |
| Error handling | present/missing |
| Rollback, if required | present/missing/N/A |
| Prior learnings | N loaded |
```

## Findings and decisions

Evaluate every section below. For each finding include section, severity,
confidence 1-10, concrete `file:line` evidence, impact, and smallest fix.

- 9-10: verified in code; report normally.
- 7-8: strong evidence; report normally.
- 5-6: report as medium confidence and state what to verify.
- 3-4: appendix only.
- 1-2: omit unless potential P0.

For every non-trivial choice, ask separately in prose. Give 2-3 lettered
options, one-line effort/risk/maintenance tradeoffs, and a recommendation grounded
in explicitness, DRY, testability, or minimal diff. Do not batch findings or move
to the next section before answers are resolved. A confirmed regression test is
mandatory and needs no approval.

## 1. Architecture review

MUST READ `${HARNESS_PLUGIN_ROOT}/internal-skills/plan-eng-review/rubrics-threat-rollback.md` and answer
its 6 security and 4 rollback questions inline. This is a plan-time threat and
recovery check, not a substitute for security review.

Draw every new component and dependency before individual findings:

```text
ASCII DEPENDENCY GRAPH
NEW: component -> existing service -> datastore/external system
LEGEND: -> calls/depends on; ==> owns; -.-> optional
```

Review:

- component boundaries, ownership, dependency direction, and shared state;
- input-to-output data flow, validation, trust boundaries, auth/authz, secrets,
  PII, and privilege changes;
- synchronous chains, fan-out, backpressure, bottlenecks, scaling, and single
  points of failure;
- one realistic production failure per new codepath/integration and its handling;
- migration, compatibility, rollback, observability, and partial-failure recovery;
- build, publication, installation, and update path for new distributed artifacts;
- diagrams required in the plan or non-obvious implementation code.

After the graph, state coupling, scaling, and security-boundary notes for every
dependency arrow.

## 2. Code quality review

Review organization, module boundaries, DRY violations, validation and error
semantics, cleanup/resource handling, concurrency edges, under/over-engineering,
and debt created by the plan. Verify nearby ASCII diagrams remain accurate.
Prefer extending an owned abstraction over adding parallel helpers or wrappers.

## 3. Test review

> **Never compress Section 3.**

Read the actual planned/touched code and tests. Trace every entry point through
input, transformation, side effect/output, branch, early return, error, retry,
and downstream call. Also trace user flows, invalid/empty/boundary inputs,
concurrency, duplicate actions, stale state, interruption, and visible recovery.

Detect the test framework from repository instructions/configuration. For every
entity, find the exact existing test and grade it:

- `***`: happy, edge, and error behavior;
- `**`: happy behavior only;
- `*`: smoke/existence assertion;
- `GAP`: no behavioral coverage.

Choose test level by boundary:

- unit for pure/local behavior;
- integration/E2E for 3+ component flows, auth, payment, destructive paths, or
  integration failures hidden by mocks;
- eval for prompt, system-instruction, tool-definition, or critical LLM changes.

Produce the complete `Test Diagram` and companion table, with one row/branch for
every new or changed entity:

```text
| Entity/branch/flow | Type | Test | Quality | Gap? | Level |

CODE PATH COVERAGE
file
`- function
   |- [*** TESTED] branch -> test:file
   `- [GAP ->E2E] error/edge -> no test

USER/DATA FLOW COVERAGE
flow
|- [TESTED] step/edge -> test:file
`- [GAP] recovery/boundary -> no test

COVERAGE: tested/total; quality counts; gaps; E2E/eval needs
```

A regression means changed existing behavior lacks a test for the newly exposed
failure. Add its regression test to the plan as `CRITICAL` without asking.

For every gap, add a plan requirement naming the test file, inputs/setup, exact
observable assertion, and unit/integration/E2E/eval level. Ensure PLAN.md has:

```text
## Test Plan
### What to test
### How to test
### Commands
### Expected signals
### Fallbacks if unavailable
```

For prompt/LLM changes, name eval suites, cases, baselines, and ask the user to
confirm eval scope. `No issues found` is valid only after listing files read and
paths traced and showing the complete diagram.

## 4. Performance review

Review query counts/N+1 access, algorithms and worst-case inputs, memory and
resource lifetime, network/disk round trips, caches and invalidation, batching,
backpressure, concurrency limits, startup/build impact, and measurable budgets.
Require a benchmark or measurement when performance is an acceptance claim.

## Required plan/output contracts

The reviewed plan must contain:

- `NOT in scope`, with a rationale for every deferral.
- `What already exists`, including reuse verdicts.
- Architecture/data-flow ASCII diagrams for non-trivial flows and identified
  implementation files that need maintained diagram comments.
- Complete `Test Plan` and test-coverage diagram/table.
### Failure Modes Registry

- A failure-mode row for every new codepath:

```text
| Failure mode | Likelihood | Blast radius | Detection | Mitigation | Critical gap? |
```

Mark `Critical gap? = YES` when mitigation is absent and blast radius is high or
critical; resolve each before implementation through an individual question.

### Worktree parallelization strategy

- A workstream dependency table and parallel lanes when 2+ independent modules
  exist. Group shared-module/dependent work sequentially and flag merge conflicts;
  otherwise state `Sequential implementation, no parallelization opportunity.`
- Rollback and distribution notes where applicable.

Present each potential TODO individually with What, Why, Pros, Cons, Context, and
dependencies, then ask: add to `TODOS.md`, skip, or build now. Never persist a
vague or unapproved TODO.

Finish with:

```text
ENGINEERING PLAN REVIEW
Premise/scope: accepted/reduced, with rationale
Architecture: N findings; threat/rollback rubric answered
Code quality: N findings
Test review: diagram produced; N gaps; N regressions
Performance: N findings
NOT in scope: written
What already exists: written
Failure modes: N critical gaps
TODOs proposed: N
Parallelization: N lanes (N parallel/N sequential)
Unresolved decisions: ...
```

Also emit an `Engineering Review Report` with counts for files/codepaths reviewed, severities, test gaps, and
architecture issues. Recommend a design review for UI scope, DX review when
`dx_scope: true`, and CEO re-review when scope changed. Log only genuine 5+
minute operational discoveries to `doc/harness/learnings.jsonl`.
