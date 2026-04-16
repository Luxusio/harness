# harness

Execution harness for AI-assisted repository work. Enforces a **plan → develop → verify → close** loop on every repo-mutating task. No step skipped.

## Install

Claude Code에서 아래 명령어를 실행합니다:

```bash
# 1. marketplace 등록
/plugin marketplace add <harness-repo-url>

# 2. 플러그인 설치
/plugin install harness
```

### 로컬 개발용 (symlink)

```bash
# 리포 클론
git clone <repo-url> harness-plugin
cd harness-plugin

# Claude Code 플러그인 디렉토리에 심링크
ln -s "$(pwd)/plugin" ~/.claude/plugins/harness

# 검증
claude plugin validate ~/.claude/plugins/harness
```

### 삭제

```bash
# marketplace 설치
/plugin uninstall harness

# symlink 설치
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
| **develop** | Implement per-AC, checkpoint progress, run quality audit |
| **verify** | QA agent(s) write CRITIC__runtime.md with runtime_verdict |
| **close** | Gate: PLAN.md + HANDOFF.md exist + runtime_verdict = PASS |

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
| `inject_checkpoint.py` | SessionStart hook — surfaces latest checkpoint | reads `checkpoints/` |
| `retro.py` | Weekly retrospective (git + learnings + health) | `doc/harness/retros/` |

Activated via optional manifest keys: `health_components`, `benchmark_components`, `audit_categories`. Health falls back to `test_command` when no components declared.

## Tiered learning

```
CLAUDE.md                     # Tier 1: key facts, loaded every session
doc/harness/patterns/*.md     # Tier 2: detailed patterns, read when relevant
doc/harness/learnings.jsonl   # Tier 3: raw signals, session-transient
```

`promote_learnings.py` auto-promotes keys with 2+ occurrences from Tier 3 → Tier 2, prunes stale entries (>90 days, keeps eureka/calibration forever), and reports Tier 1 candidates.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| SessionStart | `inject_checkpoint.py` | Resume briefing from latest checkpoint |
| SessionStart | `note_freshness.py` | Flip changed notes current → suspect |
| SessionStart | `contract_lint.py` | Detect CONTRACTS.md drift |
| Stop | `stop_gate.py` | Warn if open tasks remain |
| PreToolUse | `prewrite_gate.py` | Artifact ownership + plan-first rule |

## MCP tools

7 tools via `plugin/mcp/harness2_server.py`:

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
| `/harness:run` | Full cycle: plan → develop → verify → close |
| `/harness:maintain` | Contract drift, doc cleanup, re-interview |

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json    # plugin manifest
  .mcp.json                     # MCP server config
  CLAUDE.md                     # runtime rules
  hooks/hooks.json              # hook config
  mcp/harness2_server.py        # 7-tool MCP server
  agents/                       # agent definitions
  skills/                       # plan, develop, run, setup, maintain
  scripts/                      # _lib.py + 14 scripts
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
