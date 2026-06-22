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
| **plan** | 7-phase dual-voice review pipeline writes PLAN.md + CHECKS.yaml |
| **develop** | Implement per-AC, checkpoint progress, run quality audit, dogfood |
| **verify** | QA/UX subagent starts are hook-recorded in SUBAGENT_RECEIPTS.jsonl |
| **close** | Gate: PLAN.md exists + runtime_verdict = PASS + CHECKS terminal |

After close, the Goal child-task executor performs a self-improvement pass — surfaces friction signals into `learnings.jsonl`, promotes recurring keys into Tier 2 patterns, and prunes stale entries.

## TASK_STATE (7 fields)

```yaml
task_id: TASK__<slug>
status: created|planning|implementing|verifying|closed
runtime_verdict: pending|PASS|FAIL|BLOCKED_ENV
touched_paths: []
plan_session_state: closed|context_open|write_open
closed_at: null
updated: <ISO8601>
```

`BLOCKED_ENV` keeps the task open — QA has surfaced an environmental blocker that cannot be resolved without user action. `task_close` refuses to close anything except fresh `PASS`.

## Acceptance ledger (CHECKS.yaml)

Each AC gets a stable ID and status lifecycle:

```yaml
- id: AC-001
  title: "what passes when satisfied"
  status: open → implemented_candidate → passed | failed | deferred
  kind: functional | verification | doc | performance | security | bugfix
  completeness: 7       # 0-10, plan-time score
  root_cause: ""         # required for kind=bugfix (Iron Law)
  reopen_count: 0
```

Writes go through `scripts/update_checks.py` only. Direct edits are blocked by the prewrite gate.

### Iron Law

`kind: bugfix` ACs cannot be promoted to `implemented_candidate` or `passed` without `root_cause`. No fix without confirmed cause.

```bash
python3 scripts/update_checks.py --task-dir TASK_DIR --ac AC-001 \
  --status implemented_candidate --root-cause "off-by-one in loop bound"
```

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

QA/UX agents return findings in their final response. Codex and Claude hooks
record subagent starts to `SUBAGENT_RECEIPTS.jsonl`; `task_verify(reconcile_acs=true)`
uses that hook-owned receipt to set runtime verification and promote open
CHECKS.yaml entries. QA agents never hold `Edit`/`Write` on source files.
Dogfooder remains a non-gating backlog pass after QA/UX.

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
| `goal_queue_runner.py` | Persistent Goal child-task queue runner with heartbeat, recover, event log, failure policy, and optional harness-close verification | `doc/harness/goal-queue.json` |
| `goal_queue_migrate.py` | Existing-repo migration for pre-native Goal queue state and stale CLAUDE routing blocks | `doc/harness/goal-queue.json` |
| `task_pack_runner.py` | Ordered task-pack state for multi-step harness requests; records the user's known ordered work and makes the next task deterministic after each close | task pack state files |
| `verify_runner.py` | Deterministic manifest `verify_commands` runner with optional parallel execution | stdout |
| `req_detector.py` | Detect observable behavior that needs a durable `REQ__*.md` | stdout |
| `req_scaffold.py` | Create or update durable REQ scaffolds before observable source work | `doc/<area>/REQ__*.md` |
| `update_checks.py` | Atomic CHECKS.yaml AC status transitions (plan-first) | task-local |
| `runbook_memory.py` | Capture approved runbooks and pending setup-command candidates | `doc/harness/runbooks.yaml` |
| `hygiene_scan.py` | Close-time hygiene scan: Tier A/B auto-apply + doc archive pass | `doc/harness/.hygiene-pending.json` |
| `doc_hygiene.py` | Content-signal KEEP/REMOVE/REVIEW classifier; archives stale docs via `git mv` | `doc/harness/.hygiene-pending.json` |
| `hygiene_followup.py` | Post-close scheduler that creates one standalone hygiene review task from pending items | `doc/harness/tasks/TASK__hygiene-review-pending-docs/` |
| `hygiene_restore.py` | Restore an archived file back to original location via `git mv` | — |
| `maintain_restore.py` | Legacy wrapper for old archive restore commands | — |
| `background_registry.py` | Shared registry for Claude subagent lifecycle records used by Stop hook auto-wait | `doc/harness/runtime/background.json` |
| `background_hook.py` | SubagentStart/SubagentStop hook adapter for `background_registry.py` | `doc/harness/runtime/background.json` |
| `_gate_response.py` | Shared hook deny/allow response helper | — |
| `qa_delegation_gate.py` | Browser QA delegation guard for protected MCP calls | — |
| `verification_gap_check.py` | Resume-time warning for missing verification evidence | — |
| `drift_warn.py` | SessionStart drift detector: reminds dev-of-harness users when installed plugin lags source (silent in non-dev / non-harness repos) | — |

Activated via optional manifest keys: `health_components`, `benchmark_components`, `audit_categories`. These scripts print their results; durable follow-up belongs in REQ/GUIDE/ADR/POLICY, skills, patterns, or tests.

## Tiered learning

```
CLAUDE.md                     # Tier 1: key facts, loaded every session
doc/harness/patterns/*.md     # Tier 2: detailed patterns, read when relevant
doc/harness/learnings.jsonl   # Tier 3: raw signals, session-transient
```

The post-close self-improvement pass in the Goal child-task executor auto-promotes keys with 2+ occurrences from Tier 3 → Tier 2, prunes stale entries (>90 days, keeps eureka/calibration forever), and reports Tier 1 candidates. `qa_codifier.py` separately turns validated QA failures into regression tests.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| SessionStart | `note_freshness.py` | Flip changed notes current → suspect |
| Stop | `stop_gate.py` | Warn if open tasks remain |
| SubagentStart | `background_hook.py` | Register active Claude subagent work for Stop hook auto-wait |
| SubagentStop | `background_hook.py` | Mark Claude subagent work complete |
| PreToolUse | `prewrite_gate.py` | Artifact ownership + plan-first rule |
| PreToolUse | `hook_pre_tool_use.py` | Codex plugin wrapper for PreToolUse gates |
| PreToolUse (Bash) | `mcp_bash_guard.py` | Block Bash-layer mutations of source / protected / workflow-control paths |
| UserPromptSubmit | `prompt_memory.py` | Inject `[harness-context]` block on each prompt (active task + verdict + open ACs) |
| UserPromptSubmit | `hook_user_prompt_submit.py` | Codex plugin wrapper for prompt memory |
| PostToolUse (Bash) | `tool_routing.py` | Emit `[harness-hint]` on known failures (wrong test command, missing script) |
| PostToolUse (Bash) | `hook_post_tool_use.py` | Codex plugin wrapper for tool routing |
| SessionStart | `hook_session_start.py` | Codex plugin wrapper for startup context |
| Stop | `hook_stop.py` | Codex plugin wrapper for stop gating |
| (task_start) | `environment_snapshot.py` | One-shot probe invoked from `task_start`; writes `ENVIRONMENT_SNAPSHOT.md` into the task dir |

All hooks are fail-safe (C-12): `|| true` tail, `timeout ≤ 10`. A broken hook degrades gracefully; it never blocks the session. Gates signal decisions via stdout JSON (`hookSpecificOutput.permissionDecision`), so blocking survives the `|| true` wrapper while a script crash still exits 0.

## MCP tools

11 tools via `plugin/mcp/harness_server.py`:

| Tool | Purpose |
|------|---------|
| `task_start` | Create/resume task, return context |
| `task_context` | Refresh task state |
| `task_verify` | Sync paths, compute verification from subagent-start receipts, optionally reconcile ACs |
| `task_close` | Gate: all verdicts PASS → close |
| `task_blocked` | Park a task on a genuine environment blocker |
| `goal_start` | Start/sync native goal state |
| `goal_context` | Read active goal and child task queue |
| `goal_add_task` | Attach or update a child task under the goal |
| `goal_next_task` | Return the next queued/active child task |
| `goal_finish` | Mark the active goal complete or blocked |
| `write_plan` | Write PLAN.md / PLAN.meta.json plus optional CHECKS.yaml / AUDIT_TRAIL.md |

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |

Normal usage is `/harness:setup` once per repository, then native `/goal` for both bounded work and broad product-building work. Goal owns child tasks directly: focused work can stay as one child task, while broad work grows the Goal queue as bugs, pages, domains, or follow-up gaps are discovered. `run`, `plan`, `develop`, `goal-queue`, and the four review sub-skills (`plan-ceo-review`, `plan-design-review`, `plan-eng-review`, `plan-devex-review`) are internal orchestration details and are not invoked directly.

Existing repositories from the pre-native Goal queue model should run setup
Repair/Upgrade, or run the migration directly:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/goal_queue_migrate.py" --repo "$(pwd)"
```

The migration is idempotent. It converts `doc/harness/autopilot.yaml` into
`doc/harness/goal-queue.json`, archives the legacy state under
`doc/harness/legacy/`, removes stale `Default agent is harness` lines, and
replaces old marked `## Harness routing` blocks with the Goal child-task queue
block.

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
