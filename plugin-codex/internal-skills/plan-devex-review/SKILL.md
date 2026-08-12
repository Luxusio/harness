---
name: plan-devex-review
user-invocable: false
description: |
  Interactive developer-experience plan review for APIs, CLIs, SDKs, libraries,
  platforms, documentation, and agent skills. Uses developer evidence, competitive
  benchmarks, journey friction, and eight scored DX dimensions.
---

# Developer Experience Plan Review

> **Codex runtime notes:** Use plain conversational prose with lettered options and wait
> for the next turn where the Claude source says `AskUserQuestion`. Use bare Harness MCP
> names and `HARNESS_PLUGIN_ROOT` if needed. Apply edits with `apply_patch`. Read Hall of
> Fame sections from the Claude tree at
> `plugin/skills/plan-devex-review/dx-hall-of-fame.md`; there is no Codex copy.

Review the plan only. Apply the shared evidence, context-recovery, ownership, search,
completeness, and conversational-ask rules in
`plugin-codex/internal-skills/plan/SKILL.md`.

## Contract

- The target developer, not an abstract "user", anchors every claim.
- Gather evidence before scoring. Cite actual commands, docs, interfaces, errors,
  timestamps, and competitor sources.
- Ask one decision per friction point or finding in plain prose. Recommend one option
  with developer impact and why; wait. Never silently change a public interface or scope.
- Preserve explicit compatibility, migration, and deprecation decisions.
- Read only the current pass from the Hall of Fame source; do not load it all.
- Update the plan only after the relevant decision. No `TBD` or invented evidence.

## Applicability

Classify the plan from evidence as one or more of:

- API/Service: endpoints, GraphQL/gRPC, webhooks
- CLI Tool: commands, flags, terminal flows
- Library/SDK: package install/import and callable interfaces
- Platform: deploy, hosting, infrastructure, provisioning
- Documentation: guides, examples, references
- Claude Code Skill: `SKILL.md`, Claude Code, agents, MCP

State the primary type and ask for confirmation. If none apply, exit and recommend the
engineering or design lens; do not manufacture DX scope.

## DX principles and modes

Judge against: zero friction at T0; incremental learning; learn by doing; strong
defaults with escape hatches; errors that state problem + cause + fix; production-like
examples; fast feedback; and an early magical moment.

| Mode | Scope | Behavior |
|---|---|---|
| DX EXPANSION | Competitive advantage | Score rigorously, then offer best-in-class additions individually. |
| DX POLISH | Approved DX scope | Resolve every meaningful touchpoint gap without expansion. |
| DX TRIAGE | Adoption blockers | Trace Install and Hello World; raise only scores below 5. |

Defaults: new developer product → EXPANSION; existing-product enhancement → POLISH;
urgent bug/ship → TRIAGE. Explicit user choice wins and must not drift.

Score calibration: 9-10 best-in-class; 7-8 good; 5-6 usable with friction; 3-4 adoption
harm; 1-2 broken; 0 absent. TTHW tiers: <2m champion, 2-5m competitive, 5-10m needs
work, >10m blocking.

## Evidence preflight

Before Step 0:

1. Read the active plan, README/install/quickstart files, package metadata, design docs,
   examples/tutorials, actual CLI help/API surface, and representative error text.
2. Inspect recent DX-related history and task-local prior decisions so resolved choices
   are not re-raised.
3. Load only relevant DX learnings.
4. If no public DX artifact exists for an applicable product, flag a critical gap.
5. Report a compact readiness check: plan, detected type, persona evidence, interface
   evidence, artifacts, and prior context.

## Step 0: evidence and user decisions

Complete A-G before scoring.

### A. Developer persona

Infer 2-3 plausible personas from repository evidence and ask the user to select or
correct the primary one. Capture:

| Field | Required content |
|---|---|
| Who | role and maturity |
| Context | why/when they use the product |
| Tolerance | time/steps before abandonment |
| Expects | assumed tools, guarantees, and workflows |

Do not continue without a confirmed persona.

### B. Empathy narrative

Trace the actual getting-started path in a 150-250 word first-person narrative. Name
what the persona opens, runs, sees, feels, and where uncertainty starts. Ask the user to
confirm or correct it; the corrected narrative becomes a plan output.

### C. Competitive benchmark

Search current onboarding/TTHW evidence for three relevant competitors or comparable
tools. If search is unavailable, label any fallback benchmark as reference data. Record:

| Tool | TTHW | Notable DX choice | Source |
|---|---|---|---|

Estimate this plan's steps and TTHW, then ask the user to choose the target tier.

### D. Magical moment

Define the first moment that proves value for this persona. Offer concrete delivery
vehicles appropriate to the product, such as a sandbox, one-command demo, walkthrough,
or guided tutorial. State effort, conversion tradeoff, and competitive precedent.
Persist the chosen vehicle and implementation requirements.

### E. Mode

Recommend EXPANSION, POLISH, or TRIAGE from maturity and urgency, then obtain explicit
selection.

### F. Journey trace

Trace Discover, Install, Hello World, Real Usage, Debug, and Upgrade through real files,
commands, output, and errors. Ask separately about each evidenced friction point.
TRIAGE traces only Install and Hello World. EXPANSION additionally offers a best-in-class
improvement at each stage. Produce a resolved/deferred journey map.
The final deliverable uses the full nine-stage Hall of Fame journey-map template.

### G. First-time roleplay

Using the confirmed persona and evidence, create a timestamped T+0:00 through outcome
confusion log. Ask which confusion points belong in the plan and annotate their final
status.

## Scoring method

For every pass:

1. Recall specific Step 0 evidence and load only that pass's Hall of Fame section.
2. Score 0-10 and describe what 10 means for this product/persona.
3. Name each gap and its developer/adoption effect.
4. Ask about genuine choices one at a time; apply approved fixes to the plan.
5. Re-score. Stop at 10 or at the user's accepted residual gap.

EXPANSION may offer separate opt-in improvements after resolving the base score. POLISH
fixes all meaningful gaps. TRIAGE raises only adoption blockers. Evaluate all eight
passes; `no finding` requires evidence, not omission.

## Eight DX passes

### 1. Getting started

Can the confirmed persona reach meaningful output and the chosen magical moment within
the target TTHW? Check install prerequisites, first run, auth bootstrap, sandbox/free
trial, copy-paste quickstart, expected output, and competitive delta. Specify an ideal
sequence of at most three steps with time budgets when feasible.

### 2. API, CLI, and SDK design

Check persona fit, naming grammar, defaults, consistency, completeness, discoverability,
retries/rate limits/idempotency/offline behavior, and progressive disclosure. The
simplest call must be useful and production-shaped; one example should teach correct use.

### 3. Errors and debugging

Trace at least three concrete error paths. For each compare current vs desired output,
including problem, cause, exact location/parameter, fix, stable code/docs link, and
structured fields where applicable. Check permission/sandbox blast radius, verbose mode,
and stack-trace signal.

### 4. Documentation and learning

Check information findability within two minutes, beginner/expert layering, runnable
contextual examples, tutorials versus reference, interactive practice, search, and
version alignment.

### 5. Upgrade and migration

Check breaking-change inventory, compatibility policy, actionable deprecation warnings,
stepwise migration guides, codemods where useful, semantic/version policy, and rollback.
Never assume a compatibility period or breaking change without the plan/user deciding it.

### 6. Environment and tooling

Check editor/types support, CI/non-interactive use, mocks/test utilities, local feedback
speed, OS/architecture/container/proxy coverage, reproducibility, dry-run/verbose modes,
fixtures, and sample apps.

### 7. Community and ecosystem

Check license/source availability, support channels, runnable real examples, extension
model, contribution path, and pricing transparency. Mark non-applicable items with the
product/business evidence that makes them irrelevant.

### 8. Measurement and feedback

Check measurable TTHW, journey drop-off signals, feedback channels, periodic friction
audits, and whether a later DX review can compare the shipped experience to this plan.
Avoid telemetry proposals that ignore privacy or cannot drive a decision.

### Conditional Claude Code Skill checklist

When the product type includes Claude Code Skill, load only that checklist from the Hall
of Fame reference. Report missing items and ask separately for design decisions. It is
not a ninth scored pass.

## Required plan outputs

The reviewed plan must contain:

- Developer Persona Card and corrected Developer Empathy Narrative;
- Competitive DX Benchmark and selected TTHW target;
- Magical Moment Specification;
- Developer Journey Map and First-Time Developer Confusion Report;
- `What already exists` and `NOT in scope` with rationale;
- one score/finding/recommendation row for each of the eight passes plus TTHW;
- concrete accepted changes, separately decided TODOs, and compatibility/migration
  decisions;
- DX Implementation Checklist covering install, first output, errors, interface defaults,
  examples, upgrade path, CI, types where applicable, changelog/search/community;
- unresolved decisions and a compact report with persona, friction, score average,
  TTHW, and severity counts.

Any score below 6 is critical DX debt with its adoption impact. TTHW above 10 minutes is
a blocking finding unless the user explicitly accepts it with rationale.
