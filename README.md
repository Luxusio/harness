# harness

Execution harness for AI-assisted repository work. Enforces a **plan → develop → verify → close** loop on every repo-mutating task. No step skipped.

## Install

Run these commands in Claude Code:

```bash
# 1. Register the marketplace
/plugin marketplace add https://github.com/Luxusio/harness

# 2. Install the plugin
/plugin install harness
```

The GitHub install path registers this repo's root marketplace manifest
(`.claude-plugin/marketplace.json`), which points Claude Code at `./plugin`.
For local development installs, use `python3 install.py --claude-only`; it
copies the same root marketplace manifest plus the plugin payload into
`~/.claude/harness-dev/`, then registers that installed mirror root.

Contributors / local development → see [CONTRIBUTING.md](CONTRIBUTING.md).

## Setup

Run in your target project:

```
/harness:setup
```

Setup walks through 4 questions (project type, commands, QA strategy, health scoring), then generates:

```
CLAUDE.md                        # root entrypoint (creates or appends)
doc/harness/manifest.yaml        # project config + initialization marker
doc/harness/critics/             # plan.md, runtime.md, document.md playbooks
```

## The loop

Every repo-mutating task follows this sequence:

```
plan → develop → verify → close
```

| Step | What happens |
|------|-------------|
| **plan** | 7-phase dual-voice review pipeline writes PLAN.md and declares required lenses in TASK.json |
| **develop** | Implement per-AC, checkpoint progress, run quality audit, dogfood |
| **verify** | Ordered review/QA completions are hook-recorded and checked against TASK.json lens declarations |
| **close** | Gate: PLAN.md exists + ordered review/QA receipts yield runtime_verdict = PASS |

After every child close, the Goal executor performs self-improvement before
selecting the next child: it surfaces friction signals into `learnings.jsonl`,
promotes recurring keys into Tier 2 patterns, and
prunes stale entries.

## TASK.json (4 fields)

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "close_receipt_fingerprint": null
}
```

`task_id` is derived from the canonical directory, verdicts are derived from
`RECEIPTS.jsonl`, and `BLOCKED.md` represents a parked environmental blocker.
On successful close, `close_receipt_fingerprint` contains the exact receipt
stream fingerprint. Legacy task control artifacts are unsupported rather than
migrated or read.

## Acceptance criteria

Stable AC IDs and their success conditions live directly in `PLAN.md`.
Independent review and QA completion is recorded in one `RECEIPTS.jsonl`;
Harness does not create or reconcile a second acceptance ledger.

## Agents

All under `plugin/agents/`. Narrow tool surface — each agent gets only what its role demands.

| Agent | Role |
|-------|------|
| `developer` | Implements PLAN.md per AC |
| `dogfooder` | Post-QA power-user pass; finds friction + missing workflows |
| `qa-browser` | Browser-first runtime QA via Chrome DevTools MCP |
| `qa-api` | API runtime QA via curl / httpie |
| `qa-cli` | CLI / library runtime QA |
| `qa-desktop` | Native GUI runtime QA via X11 tooling |
| `ux-browser` / `ux-api` / `ux-cli` / `ux-desktop` | Surface-specific UX review; judges whether the implemented experience is shippable |

QA/UX agents return findings in their final response. Lifecycle hooks own the
unified `RECEIPTS.jsonl`; `task_verify` enforces plan-declared lenses and
review-before-QA ordering. See
[the Codex lifecycle ADR](doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md)
for acquisition/identity/completion and
[the artifact ADR](doc/harness/patterns/ADR__consolidated-task-artifacts.md)
for storage/schema/gate semantics.
QA agents never hold `Edit`/`Write` on source files.
Dogfooder remains a non-gating backlog pass after QA/UX.

Task lifecycle calls do not run Git change detection, capture HEAD baselines,
or invalidate receipts after source edits. Nested repositories and submodules
therefore need no special tracking. The plan declares applicable lenses;
post-QA edits and scope drift are developer-owned. Explicit setup, installer,
release, and diagnostic commands may still inspect Git or their concrete
payload when needed.

## Quality scripts

All under `plugin/scripts/`. Stdlib only.

| Script | Purpose | Output |
|--------|---------|--------|
| `health.py` | Weighted composite 0-10 score | stdout |
| `benchmark.py` | Numeric metric snapshot | stdout |
| `audit.py` | Generic categorized audit (CSO-style) | stdout |
| `canary.py` | Visual regression baseline + sha/pixel diff | `doc/harness/visual-baselines/` |
| `search_learnings.py` | Keyword/filter search over Tier 3 learnings | reads `learnings.jsonl` |
| `promote_learnings.py` | Tier 3 → Tier 2 promotion + stale pruning | `doc/harness/patterns/` |
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/` |
| `inject_checkpoint.py` | Manual resume helper for latest checkpoint context | `doc/harness/checkpoints/` |
| `retro.py` | Weekly retrospective (git + tasks + learnings) | stdout; `--save` writes `doc/harness/retros/` |
| `qa_codifier.py` | Parses QA transcripts → regression tests under `tests/regression/` | — |
| `golden_replay.py` | Record/replay runtime smoke runs for deterministic regression | `doc/harness/replays/` |
| `contract_lint.py` | CONTRACTS.md managed-block lint and skill weight checks | — |
| `runtime_services.py` | Start/status/log helper for manifest-declared runtime services | `doc/harness/runtime/` |
| `setup_finalize.py` | Canonical setup validation, legacy manifest migration, operational ignores, and success-only version stamping | `.gitignore`, `doc/harness/manifest.yaml`, `doc/harness/.version` |
| `verify_runner.py` | Deterministic manifest `verify_commands` runner with optional parallel execution | stdout |
| `req_detector.py` | Detect observable behavior that needs a durable `REQ__*.md` | stdout |
| `req_scaffold.py` | Create or update durable REQ scaffolds before observable source work | `doc/<area>/REQ__*.md` |
| `install_verified.py` | Stateless trusted post-QA harness installer; every verified call reinstalls the complete payload from an isolated snapshot | stdout / exit status |
| `runbook_memory.py` | Capture approved runbooks and pending setup-command candidates | `doc/harness/runbooks.yaml` |
| `subagent_lifecycle.py` | Receipt-backed Claude lifecycle handling, active-work queries, and trusted stop-only inference | task `RECEIPTS.jsonl` |
| `background_hook.py` | SubagentStart/SubagentStop adapter for direct unified-receipt publication | task `RECEIPTS.jsonl` |
| `_gate_response.py` | Shared hook deny/allow response helper | — |
| `verification_gap_check.py` | Resume-time warning for missing verification evidence | — |
| `drift_warn.py` | SessionStart drift detector: reminds dev-of-harness users when installed plugin lags source (silent in non-dev / non-harness repos) | — |

Activated via optional manifest keys: `health_components`, `benchmark_components`, `audit_categories`. These scripts print their results; durable follow-up belongs in REQ/GUIDE/ADR/POLICY, skills, patterns, or tests.

## Tiered learning

```
CLAUDE.md                     # Tier 1: key facts, loaded every session
doc/harness/patterns/*.md     # Tier 2: detailed patterns, read when relevant
doc/harness/learnings.jsonl   # Tier 3: raw signals, session-transient
```

The post-close self-improvement pass auto-promotes keys with 2+ occurrences
from Tier 3 → Tier 2, prunes stale entries (>90 days, keeps
eureka/calibration forever), and reports Tier 1 candidates. It completes before
`goal_next_task`, so every child contributes to subsequent work.
`qa_codifier.py` separately turns validated QA failures into regression tests.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| Stop | `stop_gate.py` | Warn if open tasks remain |
| SubagentStart | `background_hook.py` | Register active Claude subagent work for Stop hook auto-wait |
| SubagentStop | `background_hook.py` | Mark Claude work complete; infer the correlated start when this runtime emitted no start event |
| PreToolUse (direct writes) | `prewrite_gate.py` | Artifact ownership + plan-first rule |
| PreToolUse (selected mutation/lifecycle tools) | `hook_pre_tool_use.py` | Codex wrapper for direct-write/Bash gates and spawn registration recovery |
| PreToolUse (Bash) | `mcp_bash_guard.py` | Block Bash-layer mutations of source / protected / workflow-control paths |
| UserPromptSubmit | `prompt_memory.py` | Inject stored `[harness-context]` state without Git |
| UserPromptSubmit | `hook_user_prompt_submit.py` | Codex wrapper that injects `$harness:run` routing plus prompt memory |
| PostToolUse (Bash) | `tool_routing.py` | Emit `[harness-hint]` on known failures (wrong test command, missing script) |
| PostToolUse (Bash/Goal) | `hook_post_tool_use.py` | Route Bash failures and native `create_goal` synchronization |
| SessionStart | `hook_session_start.py` | Codex plugin wrapper for startup context |
| Explicit note maintenance | `note_freshness.py --paths ...` | Mark selected durable notes suspect without automatic Git scanning |
| Codex SessionStart/spawn PreToolUse + MCP background | `codex_hook_registration.py`, `codex_lifecycle_watcher.py` | Register the root rollout at startup or immediately before spawn, then bind runtime subagent starts and completions from MCP-hosted daemon threads without a detached process |
| Stop | `hook_stop.py` | Codex plugin wrapper for stop gating |

All hooks are fail-safe (C-12): `|| true` tail, `timeout ≤ 10`. A broken hook degrades gracefully; it never blocks the session. Gates signal decisions via stdout JSON (`hookSpecificOutput.permissionDecision`), so blocking survives the `|| true` wrapper while a script crash still exits 0.

## MCP tools

11 tools via `plugin/mcp/harness_server.py`:

| Tool | Purpose |
|------|---------|
| `task_start` | Create/resume task, return context |
| `task_context` | Refresh task state |
| `task_verify` | Compute verification from ordered review/QA completion receipts, optionally reconcile ACs |
| `task_close` | Gate: all verdicts PASS → close |
| `task_blocked` | Park a task on a genuine environment blocker |
| `goal_start` | Start/sync native goal state |
| `goal_context` | Read active Goal and ordered children |
| `goal_add_task` | Attach or update a child task under the goal |
| `goal_next_task` | Return the next queued/active child task |
| `goal_finish` | Mark the active goal complete or blocked |
| `write_plan` | Write PLAN.md and update TASK.json required lenses |

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:run` | Codex public entry for any repository-mutating workflow |

Normal usage is `/harness:setup` once per repository. On Codex, `$harness:run`
is implicitly selected for plain repository mutation and may also be invoked
explicitly; it loads the internal canonical workflow. Native `/goal` remains
the explicit/broad objective container and the run skill attaches ordered child
tasks through `goal_add_task` and continues them through `goal_next_task`. On
Claude Code, native Goal/task routing remains the entry. `plan`, `develop`, and
the four plan-review sub-skills remain internal. Pre-native orchestration state
is unsupported and is neither read nor migrated.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json    # plugin manifest
  .mcp.json                     # MCP server config
  CLAUDE.md                     # runtime rules
  hooks/hooks.json              # hook config
  mcp/harness_server.py         # 8-tool MCP server
  agents/                       # developer, dogfooder, critic-document, qa-{api,browser,cli,desktop}
  skills/                       # 5 user-facing + 4 review sub-skills
  scripts/                      # _lib.py + 17 stdlib scripts
```

## Development

```bash
# Validate plugin structure
claude plugin validate plugin/

# Run tests
python3 -m pytest tests/ -x --tb=short

# Smoke test a script
python3 plugin/scripts/health.py --dry-run
python3 plugin/scripts/retro.py --days 7
python3 plugin/scripts/search_learnings.py "test"
```

## Self-dogfooding

This repo uses harness on itself. After installing locally (see [CONTRIBUTING.md](CONTRIBUTING.md)), start a new Claude Code session — the harness SessionStart hooks activate automatically. Use `/harness:setup` to repair or upgrade if needed. The `MAINTENANCE` marker in a task dir bypasses plan-first for urgent fixes.
