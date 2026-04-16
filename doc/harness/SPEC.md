# harness Architecture Specification

tags: [harness, spec, architecture]
status: draft
created: 2026-04-09
task_ref: TASK__harness-architecture

---

## Overview

harness is the next-generation harness that bundles gstack-quality skills natively — users install harness only and get investigate, health, review, checkpoint, learn, and retro without installing gstack. It preserves harness's canonical loop and documentation discipline, and adds automatic intent routing so users never need to memorize skill commands.

---

## Canonical Loop

Every repo-mutating task follows this fixed sequence:

```
plan → develop → verify → close
```

No step may be skipped. The loop may cycle back (verify inadequacy → return to develop or plan) but always closes forward through close. Documentation (HANDOFF.md, DOC_SYNC.md) is part of the develop step, not a separate phase.

### Loop Step Definitions

| Step | Owner | Description |
|------|-------|-------------|
| intent | user | States goal in natural language. harness reads, classifies, routes. |
| plan | plan-skill | Produces PLAN.md. Critic-plan must PASS before develop begins. |
| develop | developer | Implements changes. Scope bounded by PLAN.md. |
| verify | runtime-critic | See Verify Phase below. |
| document | writer | Produces DOC_SYNC.md. Critic-document PASS required when applicable. |
| close | harness | Gates on both invariants. Emits task close artifact. |

---

## Verify Phase

The verify phase has two distinct roles. Both must pass before the loop advances to document.

### Role 1 — Operation Check

Confirms the implementation works as built.

- Commands run without error
- Expected outputs match actual outputs
- Acceptance criteria in CHECKS.yaml are met
- Evidence bundle is produced (commands run, outputs captured)

Operation check answers: "Does it work?"

### Role 2 — Intent Adequacy Check

Confirms the implementation satisfies the original user intent, not merely the literal spec.

- The delivered behavior matches what the user actually asked for
- Edge cases implied by intent are covered
- If the plan was too narrow to satisfy intent, this surfaces it

Intent adequacy check answers: "Does it solve what the user wanted?"

**If operation check passes but intent adequacy fails:** return to develop (implementation gap).
**If intent adequacy reveals the plan was wrong:** return to plan (scope gap).
**Both must pass** before proceeding to document.

---

## Two Invariants

These are the only hard invariants in harness. Everything else is best-practice guidance.

1. **Canonical loop structure is preserved.** The sequence intent → plan → develop → verify → document → close is never collapsed or reordered.

2. **verify success is required before close.** A task may not be closed while either verify role (operation check or intent adequacy) is failing or unevaluated.

Stale PASS does not count: if files change after a PASS verdict, that verdict is invalidated.

---

## Responsibility Boundaries

### What harness does

- Reads user intent and auto-routes to the appropriate specialist
- Enforces the canonical loop for all repo-mutating tasks
- Maintains structured task artifacts (PLAN.md, CHECKS.yaml, HANDOFF.md, DOC_SYNC.md)
- Produces runtime critic verdicts with evidence bundles
- Runs document sync and document critic passes
- Displays session welcome preamble on start
- Provides natively owned specialist skills (investigate, health, review, checkpoint, learn, retro)

### What harness does not do

- Browser-based QA (browser_qa_supported: false)
- gstack telemetry, gstack-config, gstack-slug, gstack-update-check
- YC-flavored preambles or "Boil the Lake" intros
- Require users to memorize slash commands
- Ship, deploy, canary, design-* workflows
- Require gstack to be installed

---

## gstack Differentiators

| Dimension | gstack | harness |
|-----------|--------|----------|
| Skill invocation | User types `/skill` manually | Auto-routing based on intent |
| Learning curve | Must know skill names and when to use them | Zero — just describe the task |
| Documentation | No project-level auto-documentation | DOC_SYNC + critic-document enforced |
| Telemetry/config | gstack-config, telemetry prompts, YC preamble | None — clean session start |
| Runtime dependency | gstack binaries required | Fully native, no external dependency |
| Canonical loop | No enforced loop | Enforced: plan → verify before close |

### Auto-Routing (zero learning curve)

When a user states an intent in natural language, harness reads the pattern and routes without prompting. The user says "there's a bug in X" and the investigate specialist activates. No `/investigate` required.

See `AUTO_ROUTING.md` for the full intent pattern → specialist mapping table.

## Bundled Skills

harness bundles 6 gstack-quality skills natively in `plugin2/skills/`. Users get all of these without installing gstack:

| Skill | Purpose |
|-------|---------|
| investigate | Root-cause analysis for bugs, errors, and regressions |
| health | Code quality and project health scanning |
| review | PR and diff review |
| checkpoint | Session save and resume |
| learn | Learnings capture and retrieval |
| retro | Periodic retrospective |

### Bundling Principles

- **gstack infra stripped**: preamble bash block, `gstack-update-check`, `gstack-config`, `gstack-slug`, `gstack-telemetry-log`, PROACTIVE/SKILL_PREFIX flags, YC references — all removed
- **Core logic preserved**: the skill's actual workflow, analysis steps, and output format are kept intact
- **Path remapped**: `~/.gstack/projects/*/` → `.harness/` (project-local, no home dir dependency)
- **Context block added**: minimal `_BRANCH` / `_PROJECT` detection without gstack binaries

See `IMPORT_LIST.md` for per-skill import decisions and stripping scope.

---

## Session Welcome Message

### Design Principles

The welcome message must be:
- Brief (fits on screen without scrolling)
- Oriented (tells the user where they are and what's active)
- Harness-specific (not gstack's commercial preamble)
- Action-oriented (what to do next, not what harness is)

### Welcome Message Draft

```
harness — ready.

Project: {project_name}
Branch:  {git_branch}
Tasks:   {open_task_count} open  |  {blocked_task_count} blocked

Loop: intent → plan → develop → verify → document → close
Auto-routing is on. Just describe what you want.

Type a task, ask a question, or say "show tasks" to see open work.
```

### Display Conditions

- Shown once per session at first harness interaction
- `{project_name}` — from manifest.yaml project field or repo basename
- `{git_branch}` — from `git branch --show-current`
- `{open_task_count}` / `{blocked_task_count}` — from task directory scan
- If task scan fails, omit the Tasks line silently

### Preamble vs gstack Comparison

gstack's preamble runs a multi-step bash block, prompts for telemetry, introduces "Boil the Lake", and may trigger PROACTIVE and routing setup dialogs. harness's welcome is a single static display with minimal dynamic fields. No prompts, no setup dialogs, no external binary calls.

---

## Agent Roles (Responsibility Map)

| Agent | Canonical loop role | Notes |
|-------|--------------------|----|
| harness | orchestrator | intent reading, routing, loop enforcement, welcome message |
| plan-skill | plan | produces PLAN.md, scopes work |
| developer | develop | implements within PLAN.md scope |
| runtime-critic | verify (both roles) | operation check + intent adequacy |
| writer | document | DOC_SYNC.md, changelog |
| document-critic | document | CRITIC__document.md verdict |
| investigate | verify support | called by harness when debugging needed |
| health | develop support | code quality scan on demand or by routing |
| review | develop/verify support | pre-merge diff review |
| checkpoint | cross-loop | state save/restore, context bridging |
| learn | cross-loop | learnings capture and retrieval |
| retro | meta | periodic retrospective, not in per-task loop |
