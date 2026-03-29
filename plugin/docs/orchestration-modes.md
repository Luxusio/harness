# Orchestration Modes Reference

updated: 2026-03-30

> **See also:** `plugin/docs/execution-modes.md` for execution modes (`light | standard | sprinted`). Orchestration mode and execution mode are orthogonal axes — they are selected independently and combined freely.

---

## Overview

Orchestration mode controls **how many agents work on a task and how their work is coordinated**. It is selected after execution mode and before plan creation, then stored in `TASK_STATE.yaml`.

The three modes:

| Mode | Who works | Parallelism |
|------|-----------|-------------|
| `solo` | Lead agent only | None — sequential |
| `subagents` | Lead + helper sub-agents | Parallel helpers (research/search/verify) |
| `team` | Lead + worker agents with disjoint file ownership | Full parallel across independent roots/layers |

---

## Mode Definitions

### solo

All work is done by the lead agent, sequentially. No sub-agents are spawned.

**Intended for:** Single-file changes, docs-only tasks, investigations, answers, tasks with sequential step dependencies, or tasks where same-file conflict risk is high.

### subagents

The lead spawns helper sub-agents for parallelisable auxiliary work (research, search, verification). Workers do not own writable file sets — they return results to the lead.

**Intended for:** Tasks where some parallelism is useful but full worker ownership separation is not needed or not available. Useful for research-heavy planning, parallel verification passes, or multi-angle investigation.

### team

The lead spawns N worker agents, each with a disjoint set of writable paths. Workers execute in parallel and report back. The lead synthesises results before close.

**Intended for:** Cross-layer work (app + api + tests), tasks with 2+ independent roots, parallel exploration or review across clearly non-overlapping areas.

---

## Orthogonal to Execution Mode

Orchestration mode is **independent** of execution mode. Any combination is valid:

| Execution mode | Orchestration mode | Example |
|---------------|-------------------|---------|
| light | solo | Docs-only single-file fix |
| light | subagents | Docs investigation with parallel search helpers |
| standard | solo | Normal single-root feature |
| standard | subagents | Feature requiring parallel research |
| standard | team | Feature spanning app + api with disjoint ownership |
| sprinted | solo | Complex cross-surface task done sequentially |
| sprinted | team | Cross-layer migration with parallel workers |

---

## Selection Rules

### Signal table

| Signal | Mode indicated |
|--------|---------------|
| Single-file or small-diff task | solo |
| Sequential step dependencies | solo |
| Same-file conflict risk | solo |
| Lane = `docs-only`, `answer`, `investigate` | solo |
| Helper tasks needed (research/search/verify), no file writes | subagents |
| Parallelism useful but team readiness not confirmed | subagents |
| Cross-layer work (app+api+tests), disjoint file ownership | team |
| 2+ independent roots from request + manifest | team |
| Parallel exploration or review across non-overlapping areas | team |
| Team provider available and readiness probe passes | team |

**Tie-break rule:** When signals conflict, prefer the less complex mode (solo < subagents < team). Only escalate to team when file ownership is clearly disjoint.

### Prohibition rules for team

Do NOT select `team` when:
- Multiple workers would need to edit the same file concurrently
- Task steps form a sequential dependency chain (B cannot start until A is complete)
- Task is a small bugfix (solo or subagents is sufficient overhead-free)

---

## Recommended Selection Algorithm

```
if lane in (docs-only, answer, investigate):
    return solo

if single_file or small_diff or sequential_dependencies or same_file_conflict_risk:
    return solo

if cross_layer and disjoint_ownership and team_provider_available and readiness_probe_passes:
    if independent_roots >= 2 or parallel_layers_evident:
        return team

if parallelism_useful and not sequential_dependencies:
    return subagents

return solo  # default
```

---

## Escalation and Downgrade Rules

| Transition | When allowed |
|-----------|-------------|
| solo → subagents | Parallelism becomes useful mid-task |
| subagents → team | Scope grows to cross-layer with disjoint ownership |
| team → fallback-subagents | Team provider unavailable or readiness probe fails |
| team → fallback-solo | Last resort — subagents also unavailable |

Escalations are always permitted. Downgrades are fallbacks only (record in `fallback_used`).

**Modes never downgrade for performance reasons** — only for provider/readiness failures.

---

## Provider Selection Policy

When `orchestration_mode: team`, the lead selects a team provider in this priority order:

1. **native** — Claude Code built-in team support (preferred; lowest overhead)
2. **omc** — oh-my-claudecode team orchestration (fallback if native blocked)
3. **none** — no team provider available; downgrade to subagents or solo

Provider preference is read from `manifest.teams.provider`. If not set, default priority applies.

### Approval-free operation

When `manifest.teams.auto_activate: true` and `manifest.teams.approval_mode: preapproved`:
- No user confirmation is required before spawning workers
- Lead proceeds directly to TEAM_PLAN.md creation and worker spawn
- All worker activity is recorded in TASK_STATE.yaml for auditability

---

## Readiness Probe

Before selecting `team`, the harness performs a lightweight readiness probe:

1. Check `manifest.teams.*` exists and specifies a provider
2. Confirm provider is not blocked (e.g., native team support enabled in current environment)
3. Confirm file ownership can be cleanly partitioned (no shared writable paths across workers)

If the probe fails at any step, downgrade to `subagents` (or `solo` if subagents also unavailable) and record `fallback_used`.

---

## Team Artifacts

### TEAM_PLAN.md (required before spawning workers)

Minimum required fields:

| Field | Description |
|-------|-------------|
| Worker roster | List of workers with role names |
| Owned writable paths | Exhaustive per-worker path list |
| Shared read-only paths | Paths all workers may read but not write |
| Forbidden writes | Paths each worker must not touch |
| Synthesis strategy | How lead will merge worker outputs |

### TEAM_SYNTHESIS.md (required before close)

Minimum required fields:

| Field | Description |
|-------|-------------|
| Merge summary | What each worker produced and how results were combined |
| Conflict resolutions | Any path or logical conflicts encountered and how resolved |
| Final artifact list | All artifacts produced across all workers |

---

## TASK_STATE.yaml Team Fields

```yaml
orchestration_mode: solo | subagents | team
team_provider: none | native | omc | fallback-subagents | fallback-solo
team_status: n/a | planned | running | degraded | fallback | complete | skipped
team_size: 0
team_reason: ""
team_plan_required: false
team_synthesis_required: false
fallback_used: none | subagents | solo
```

`team_reason` should be a brief phrase explaining why team mode was selected (or why a fallback was used), for auditability.

---

## Examples

### Scenario 1: Docs-only change (solo)

**Request:** "Update the README to add the new API endpoint."

**Signals:** Lane = `docs-sync`, single file, no cross-layer work.

**Orchestration mode selected:** solo

**Reason:** No parallelism needed; single file, sequential.

---

### Scenario 2: Research-heavy feature (subagents)

**Request:** "Investigate all places the auth token is validated and summarise the pattern."

**Signals:** Lane = `investigate`, parallelism useful for multi-file search, no file writes from workers needed.

**Orchestration mode selected:** subagents

**Reason:** Parallel search helpers accelerate investigation; no owned writable paths needed.

---

### Scenario 3: Cross-layer feature (team)

**Request:** "Add a new billing module with a DB table, API endpoints, and a React billing page."

**Signals:** 3 surfaces (app + api + db), 3 independent roots, disjoint file ownership possible, team provider available.

**Orchestration mode selected:** team

**Reason:** Workers can own app/, api/, and db/ independently; no shared writable paths; full parallelism beneficial.

---

### Scenario 4: Prior blocked task — sprinted + solo

**Request:** Resuming a task that previously hit `blocked_env` on a single-root API service.

**Signals:** Single root (api/), blocked_env recovery, sequential recovery steps needed.

**Orchestration mode selected:** solo (execution mode: sprinted)

**Reason:** Recovery is sequential; team overhead not warranted for single-root work. Execution mode escalated to sprinted for stronger planning and evaluation.
