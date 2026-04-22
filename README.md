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

### Local development (symlink)

```bash
# Clone the repo
git clone https://github.com/Luxusio/harness harness-plugin
cd harness-plugin

# Symlink into Claude Code plugins directory
ln -s "$(pwd)/plugin" ~/.claude/plugins/harness

# Verify
claude plugin validate ~/.claude/plugins/harness
```

Then in Claude Code:

```
/plugin install harness
```

### Uninstall

```bash
# Marketplace install
/plugin uninstall harness

# Symlink install
rm ~/.claude/plugins/harness
```

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
| **verify** | QA agent(s) write CRITIC__runtime.md with runtime_verdict |
| **close** | Gate: PLAN.md + HANDOFF.md exist + runtime_verdict = PASS |

After close, `/harness:run` runs a self-improvement pass — surfaces friction signals into `learnings.jsonl`, promotes recurring keys into Tier 2 patterns, and prunes stale entries.

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
| `developer` | Implements PLAN.md per AC, writes HANDOFF.md |
| `dogfooder` | Post-QA power-user pass; finds friction + missing workflows |
| `qa-browser` | Browser-first runtime QA via Chrome DevTools MCP |
| `qa-api` | API runtime QA via curl / httpie |
| `qa-cli` | CLI / library runtime QA |

QA agents write the runtime verdict via `mcp__harness__write_critic_runtime`. They never hold `Edit`/`Write` on source files.

## Quality scripts

All under `plugin/scripts/`. Stdlib only.

| Script | Purpose | State file |
|--------|---------|------------|
| `health.py` | Weighted composite 0-10 score | `doc/harness/health-history.jsonl` |
| `benchmark.py` | Numeric metrics vs baseline, WARN/REGR thresholds | `doc/harness/benchmark/` |
| `audit.py` | Generic categorized audit (CSO-style) | `doc/harness/audits/` |
| `canary.py` | Visual regression baseline + sha/pixel diff | `doc/harness/visual-baselines/` |
| `search_learnings.py` | Keyword/filter search over Tier 3 learnings | reads `learnings.jsonl` |
| `promote_learnings.py` | Tier 3 → Tier 2 promotion + stale pruning | `doc/harness/patterns/` |
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/` |
| `retro.py` | Weekly retrospective (git + learnings + health) | `doc/harness/retros/` |
| `qa_codifier.py` | Parses QA transcripts → regression tests under `tests/regression/` | — |
| `golden_replay.py` | Record/replay runtime smoke runs for deterministic regression | `doc/harness/replays/` |
| `update_checks.py` | Atomic CHECKS.yaml AC status transitions (plan-first) | task-local |
| `write_plan_artifact.py` | CLI writer for PLAN.md / PLAN.meta.json / CHECKS.yaml / AUDIT_TRAIL.md | task-local |
| `hygiene_scan.py` | SessionStart auto-hygiene: Tier A/B auto-apply + doc archive pass | `doc/harness/.maintain-pending.json` |
| `doc_hygiene.py` | Content-signal KEEP/REMOVE/REVIEW classifier; archives stale docs via `git mv` | `doc/harness/.maintain-pending.json` |
| `maintain_restore.py` | Restore an archived file back to original location via `git mv` | — |

Activated via optional manifest keys: `health_components`, `benchmark_components`, `audit_categories`. Health falls back to `test_command` when no components declared.

## Tiered learning

```
CLAUDE.md                     # Tier 1: key facts, loaded every session
doc/harness/patterns/*.md     # Tier 2: detailed patterns, read when relevant
doc/harness/learnings.jsonl   # Tier 3: raw signals, session-transient
```

The post-close self-improvement pass (`/harness:run`) auto-promotes keys with 2+ occurrences from Tier 3 → Tier 2, prunes stale entries (>90 days, keeps eureka/calibration forever), and reports Tier 1 candidates. `qa_codifier.py` separately turns validated QA failures into regression tests.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| SessionStart | `inject_checkpoint.py` | Resume briefing from latest checkpoint |
| SessionStart | `note_freshness.py` | Flip changed notes current → suspect |
| SessionStart | `contract_lint.py` | Detect CONTRACTS.md drift |
| Stop | `stop_gate.py` | Warn if open tasks remain |
| PreToolUse | `prewrite_gate.py` | Artifact ownership + plan-first rule |
| PreToolUse (Bash) | `mcp_bash_guard.py` | Block Bash-layer mutations of source / protected / workflow-control paths |
| UserPromptSubmit | `prompt_memory.py` | Inject `[harness-context]` block on each prompt (active task + verdict + open ACs + suspect notes) |
| PostToolUse (Bash) | `tool_routing.py` | Emit `[harness-hint]` on known failures (wrong test command, missing script) |
| (task_start) | `environment_snapshot.py` | One-shot probe invoked from `task_start`; writes `ENVIRONMENT_SNAPSHOT.md` into the task dir |

All hooks are fail-safe (C-12): `|| true` tail, `timeout ≤ 10`. A broken hook degrades gracefully; it never blocks the session. Gates signal decisions via stdout JSON (`hookSpecificOutput.permissionDecision`), so blocking survives the `|| true` wrapper while a script crash still exits 0.

## MCP tools

7 tools via `plugin/mcp/harness_server.py`:

| Tool | Purpose |
|------|---------|
| `task_start` | Create/resume task, return context |
| `task_context` | Refresh task state |
| `task_verify` | Sync paths + check verification |
| `task_close` | Gate: all verdicts PASS → close |
| `write_critic_runtime` | QA agent writes verdict |
| `write_handoff` | Developer writes HANDOFF.md |
| `write_doc_sync` | Developer writes DOC_SYNC.md |

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:plan` | 7-phase dual-voice review → PLAN.md + CHECKS.yaml |
| `/harness:develop` | Implement plan with quality audit pipeline |
| `/harness:run` | Full cycle: plan → develop → verify → close + self-improvement |
| `/harness:maintain` | Contract drift, doc cleanup, re-interview |

`/harness:plan` internally dispatches four review sub-skills (`plan-ceo-review`, `plan-design-review`, `plan-eng-review`, `plan-devex-review`) as dual-voice reviewers — they are not invoked directly.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json    # plugin manifest
  .mcp.json                     # MCP server config
  CLAUDE.md                     # runtime rules
  hooks/hooks.json              # hook config
  mcp/harness_server.py         # 7-tool MCP server
  agents/                       # 5 agents: developer, dogfooder, qa-{api,browser,cli}
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

This repo uses harness on itself. After symlinking:

```bash
ln -s "$(pwd)/plugin" ~/.claude/plugins/harness
```

Start a new Claude Code session. The harness SessionStart hooks will activate. Use `/harness:setup` to repair/upgrade if needed. The `MAINTENANCE` marker in a task dir bypasses plan-first for urgent fixes.
