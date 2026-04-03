# Orchestration Modes Reference

updated: 2026-04-01

> **Compatibility / maintenance reference only.**
> This document is NOT the agent-facing canonical routing source.
> For task routing, use: `mcp__plugin_harness_harness__task_context`
> Orchestration mode is derived automatically by `mcp__plugin_harness_harness__task_start` and stored as a compatibility field in TASK_STATE.yaml.

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
| Single-file, docs-only, answer, investigate, or obviously tiny task | solo |
| Non-trivial task, but disjoint writable ownership is unclear | subagents |
| Team-preferred task, but provider/readiness is missing | fallback-subagents |
| Broad-build request with partitionable surfaces | team |
| Maintenance / harness / multi-root work with safe ownership split | team |
| Cross-layer work (app+api+tests), disjoint file ownership | team |
| Parallel exploration or review across non-overlapping areas | team |

**Tie-break rule:** Prefer `team` for non-trivial work when ownership is safely partitionable. Prefer `subagents` over `solo` when the task is not small but a safe team split is unclear.

### Prohibition rules for team

Do NOT select `team` when:
- Multiple workers would need to edit the same file concurrently
- Task steps form a sequential dependency chain (B cannot start until A is complete)
- Task is a small bugfix (solo or subagents is sufficient overhead-free)

---

## Recommended Selection Algorithm

```
if lane in (docs-only, answer, investigate) or task_is_small:
    return solo

if broad_build or maintenance_task or multi_root or multi_surface_request:
    if disjoint_ownership and team_provider_available and readiness_probe_passes:
        return team
    return subagents  # team-preferred fallback

if task_is_non_trivial:
    return subagents

return solo
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
- `task_start` scaffolds `TEAM_PLAN.md` and `TEAM_SYNTHESIS.md`
- Source writes stay blocked until `TEAM_PLAN.md` is semantically complete (required headings, no placeholders, explicit worker ownership, no overlapping writable paths)
- After that, source writes are restricted to declared owned writable paths; shared read-only or unowned paths are blocked
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
| Owned writable paths | Exhaustive per-worker path list; must be disjoint across workers |
| Shared read-only paths | Paths all workers may read but not write |
| Forbidden writes | Paths each worker must not touch |
| Synthesis strategy | How lead will merge worker outputs |
| Documentation ownership (optional) | Explicit workers for `DOC_SYNC.md` (`writer`) and `CRITIC__document.md` (`critic-document`) |

Once those sections are complete and ownership is semantically valid, the lead should run `mcp__plugin_harness_harness__team_bootstrap` (or `hctl.py team-bootstrap --task-dir ... --write-files`) before fan-out. That writes `team/bootstrap/index.json`, per-worker briefs, and role-scoped env snippets such as `team/bootstrap/worker-a.developer.env` or `team/bootstrap/reviewer.writer.env`.

After that bootstrap step, `mcp__plugin_harness_harness__team_dispatch` (or `hctl.py team-dispatch --task-dir ... --write-files`) can freeze the actual launch surface under `team/bootstrap/provider/`: a provider prompt, provider-specific launcher helper, per-phase worker prompts, and headless `run-*.sh` helpers. Synthesis owners also receive explicit `synthesis` and `handoff_refresh` phase helpers so `TEAM_SYNTHESIS.md` / `HANDOFF.md` refreshes can be relaunched from the same generated pack. Then `mcp__plugin_harness_harness__team_launch` (or `hctl.py team-launch --task-dir ... --write-files`) becomes the default fan-out entrypoint: it auto-refreshes stale bootstrap/dispatch artifacts if needed, writes `team/bootstrap/provider/launch.json`, and points the lead at the provider launcher or implementer dispatcher from one command. For native Claude teams, that launch surface now exposes the frozen lead prompt and an auto-execute fallback to the implementer dispatcher so `team-launch --execute` can still fan out when the provider path itself is interactive-only. Once fan-out has started, `mcp__plugin_harness_harness__team_relaunch` (or `hctl.py team-relaunch --task-dir ... --write-files`) can select and optionally spawn the current best worker/phase recovery target without rebuilding prompts by hand. `task_context` and `SESSION_HANDOFF.json` surface when this launch layer is missing or stale so the lead refreshes it before contributors fan out.

### Artifact-driven team status

`task_context` derives `team_status` from the artifact state rather than trusting a stale YAML field:

- `planned` — `TEAM_PLAN.md` exists but is still scaffold/incomplete
- `running` — `TEAM_PLAN.md` is complete, `TEAM_SYNTHESIS.md` is not
- `complete` — `TEAM_SYNTHESIS.md` is complete
- `degraded` — a degraded round was recorded and synthesis has not been refreshed yet
- `fallback` — a fallback path (for example `subagents`) was used

`task_context` can also be personalized without mutating the parent shell environment: pass `team_worker` and `agent_name` to the MCP `task_context` tool (or `hctl.py context --team-worker ... --agent-name ...`) to preview the exact contributor / writer / critic-document pack that will be handed to a spawned worker.

### TEAM_SYNTHESIS.md (required before close)

Minimum required fields:

| Field | Description |
|-------|-------------|
| Merge summary | What each worker produced and how results were combined |
| Conflict resolutions | Any path or logical conflicts encountered and how resolved |
| Final artifact list | All artifacts produced across all workers |

### team/worker-<name>.md (required before synthesis)

Each contributor worker listed in `TEAM_PLAN.md` should leave a summary under `team/worker-<name>.md`. When the roster includes an explicit `lead` / `integrator` synthesis owner, that worker may skip a worker summary and instead own `TEAM_SYNTHESIS.md`, the final runtime verification (`CRITIC__runtime.md` / `QA__runtime.md`), and the final `HANDOFF.md` refresh.

Minimum required fields:

| Field | Description |
|-------|-------------|
| Completed work | What the worker finished or explicitly left incomplete |
| Owned paths handled | Concrete owned paths or globs that this worker actually touched; may be `none` only when no write happened |
| Verification | Commands or checks the worker ran |
| Residual risks | Remaining risks, or `none` |

`TEAM_SYNTHESIS.md` should be refreshed after the latest worker summary update. A stale synthesis file does not satisfy close.

`TEAM_SYNTHESIS.md` is not the last step for repo-mutating team tasks: the synthesis owner must rerun final runtime verification after the latest synthesis refresh. If `CRITIC__runtime.md` predates the newest `TEAM_SYNTHESIS.md`, close is blocked until verification is rerun.

After that final runtime verification, the writer must refresh `DOC_SYNC.md`. If `TEAM_PLAN.md` explicitly names documentation owners, the documentation pass becomes worker-scoped as well: those workers are surfaced in `hctl context`, copied into `SESSION_HANDOFF.json`, and enforced by both the prewrite gate and the `write_artifact.py` backend for `DOC_SYNC.md` / `CRITIC__document.md`. Pass `team_worker` (or set `HARNESS_TEAM_WORKER`) when calling the MCP `write_*` tools for these artifacts so the backend can verify the correct worker owner. If documentation review is required, `critic-document` must then rerun against the refreshed `DOC_SYNC.md` / final verification state before team close can continue.

`HANDOFF.md` must also be refreshed after the newest close artifact, including final runtime verification, `DOC_SYNC.md`, and `CRITIC__document.md` when present. A stale handoff blocks team close for the same reason a stale synthesis does.

When a team task is interrupted or repeatedly fails, `SESSION_HANDOFF.json` now carries a `team_recovery` block so the next session can resume from the blocked phase (`TEAM_PLAN.md`, pending worker summaries, `TEAM_SYNTHESIS.md`, final runtime verification, or documentation sync) instead of reopening broad repo exploration. That block also includes per-worker recovery facts (artifact path, owned writable paths, handled paths, verification excerpt, residual-risk excerpt), explicit `synthesis_workers`, documentation owners for the doc pass, and a signal when `HANDOFF.md` must be refreshed from newer close artifacts.

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
