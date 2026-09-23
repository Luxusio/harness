# harness

Execution harness for AI-assisted repository work. Enforces a **task_start → plan → develop → QA → close** loop on every repo-mutating task. Internal review and verification gates remain mandatory.

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

Every repo-mutating task follows this public sequence:

```
task_start → plan → develop → QA → close
```

| Step | What happens |
|------|-------------|
| **task_start** | Create or resume one exact task generation and bind it to the current runtime session |
| **plan** | Review the request, write PLAN.md, and declare required lenses in TASK.json |
| **develop** | Implement per-AC; independent review runs as an internal end-of-develop gate |
| **QA** | Run the declared runtime QA lens and record its observed completion |
| **close** | Internally run `task_verify`; for Harness source, conditionally refresh stale installed payloads; then publish close authority |

After every child close, the Goal executor performs self-improvement before
selecting the next child: it may append friction signals to `learnings.jsonl`,
then reports recurring keys from distinct verified task runs as Tier 2
candidates. The candidate-reporting step changes neither the raw ledger nor
pattern files; durable pattern changes require a separately reviewed Harness
task.

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
Independent review and QA completion is recorded in one compact,
authoritative `RECEIPTS.jsonl`; Harness does not create or reconcile a second
acceptance ledger. Current formal-review completions also write their exact
text to a non-authoritative `REVIEWS.jsonl` appendix, selected one item at a
time by the receipt's `DETAIL_SHA256`. The appendix may be absent only for
legacy receipts; operators never need to read it for lifecycle decisions.

## Agents

All under `plugin/agents/`. Narrow tool surface — each agent gets only what its role demands.

| Agent | Role |
|-------|------|
| `developer` | Implements PLAN.md per AC |
| `defect-hunter` | Non-attesting evidence-only discovery: LIGHT runs 0, STANDARD 1 selected focus, and DEEP both focuses |
| `code-reviewer` | Always runs fresh after discovery, independently sweeps the full scope, and is the sole `review-code` verdict authority |
| `security-reviewer` | Conditional trust-boundary and exploitability specialist; sole `review-security` authority |
| `dogfooder` | Post-QA power-user pass; finds friction + missing workflows |
| `qa-browser` | Browser-first runtime QA via Chrome DevTools MCP |
| `qa-api` | API runtime QA via curl / httpie |
| `qa-cli` | CLI / library runtime QA |
| `qa-desktop` | Native GUI runtime QA via X11 tooling |
| `ux-browser` / `ux-api` / `ux-cli` / `ux-desktop` | Surface-specific UX review; judges whether the implemented experience is shippable |

Review discovery is deterministic and ephemeral: LIGHT runs no hunters,
STANDARD runs one selected correctness or contract/test hunter, and DEEP runs
both. Every tier still runs one fresh full-scope formal code reviewer; the
conditional security review remains separate. Depth is not authoritative
lifecycle state and has no dedicated task, receipt, or review-detail field;
selected depth and evidence may appear in stored non-authoritative formal-review
narrative. Rebase-LIGHT requires exact old/new endpoints,
conflict-free one-to-one patch equivalence, affirmative semantic non-overlap,
`HEAD` at the new tip, and a clean, fully accounted-for index and worktree.
Discovery is capped at two cycles (DEEP: four hunter calls maximum); test-only
retries use contract/test only, narrative corrections use deterministic checks,
and an exhausted budget proceeds to one fresh formal reviewer.

QA/UX agents return findings in their final response. Lifecycle hooks own the
unified `RECEIPTS.jsonl`; `task_verify` enforces plan-declared lenses and
review-before-QA ordering. See
[the Codex lifecycle ADR](doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md)
for acquisition/identity/completion and
[the artifact ADR](doc/harness/patterns/ADR__consolidated-task-artifacts.md)
for storage/schema/gate semantics.
QA agents never hold `Edit`/`Write` on source files.
Dogfooder remains a non-gating backlog pass after QA/UX.

To inspect the exact text behind one review receipt without loading review
history, pass its lowercase digest explicitly:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HARNESS_PLUGIN_ROOT/scripts/review-read" \
  --task-dir doc/harness/tasks/TASK__slug <64-lowercase-hex>
```

Old receipts may have no stored detail; that is reported as not found and does
not change their validity.

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
| `promote_learnings.py` | Current-run-validated Tier 2 candidate reporting; no durable writes | stdout |
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/` |
| `retro.py` | Weekly retrospective (git + receipt-verified closes + learnings) | stdout; `--save` writes `doc/harness/retros/` |
| `qa_codifier.py` | Parses QA transcripts → regression tests under `tests/regression/` | — |
| `golden_replay.py` | Record/replay runtime smoke runs for deterministic regression | `doc/harness/replays/` |
| `contract_lint.py` | CONTRACTS.md managed-block lint and skill weight checks | — |
| `mutation_probe.py` | Mutates the changed lines of a diff and reports the mutations no test noticed; every survivor is re-run against the full suite first | stdout |
| `runtime_services.py` | Start/status/log helper for manifest-declared runtime services | `doc/harness/runtime/` |
| `setup_finalize.py` | Canonical setup validation, manifest version migration, operational ignores, and legacy version-file cleanup | `.gitignore`, `doc/harness/manifest.yaml` |
| `project_format_check.py` | Read-only SessionStart reminder for older Harness project file formats and missing operational ignores | stdout |
| `verify_runner.py` | Deterministic manifest `verify_commands` runner with optional parallel execution | stdout |
| `req_detector.py` | Detect observable behavior that needs a durable `REQ__*.md` | stdout |
| `req_scaffold.py` | Create or update durable REQ scaffolds before observable source work | `doc/<area>/REQ__*.md` |
| `install_verified.py` | Stateless trusted post-QA delivery wrapper; compares canonical payloads from an isolated verified snapshot and refreshes only stale runtimes | stdout / exit status |
| `install_smoke.py` | Drives an installed runtime tree once — imports every registered hook module and checks a bound subagent produces a receipt row; run by `install.py` after each sync and on the `--if-stale` skip path | stdout / exit status |
| `runbook_memory.py` | Capture approved runbooks and pending setup-command candidates | `doc/harness/runbooks.yaml` |
| `review-log` | Append one bounded formal-review final from stdin to the task-local content-addressed detail store | task `REVIEWS.jsonl` |
| `review-read` | Return exactly one stored formal-review final by lowercase SHA-256 digest | stdout |
| `subagent_lifecycle.py` | Receipt-backed Claude lifecycle handling, active-work queries, and trusted stop-only inference | task `RECEIPTS.jsonl` |
| `background_hook.py` | SubagentStart/SubagentStop adapter for direct unified-receipt publication | task `RECEIPTS.jsonl` |
| `_gate_response.py` | Shared hook deny/allow response helper | — |
| `verification_gap_check.py` | Resume-time warning for missing verification evidence | — |
| `drift_warn.py` | SessionStart drift detector: compares source against the scripts dir it is executing from, so a session loading a different tree is visible (silent in non-dev / non-harness repos) | — |
| `hook_tree_health.py` | Reports when the registered hook tree lacks the `SubagentStart`/`SubagentStop` receipt subsystem, so `task_start` can warn that receipts cannot be recorded | — |

`health.py` is activated through the optional `health_components` manifest key.
It prints its result; durable follow-up belongs in REQ/GUIDE/ADR/POLICY,
skills, patterns, or tests.

## Tiered learning

```
CLAUDE.md                     # Tier 1: key facts, loaded every session
doc/harness/patterns/*.md     # Tier 2: detailed patterns, read when relevant
doc/harness/learnings.jsonl   # Tier 3: append-only raw signal ledger
```

Within the post-close self-improvement pass, candidate reporting identifies keys
repeated across 2+ receipt-verified task runs for a separately reviewed Tier 2
change. That reporting step performs no durable writes; other self-improvement
steps may append signals or save a due retro. The pass completes before
`goal_next_task`, so every child contributes to subsequent work.
`qa_codifier.py` separately turns validated QA failures into regression tests.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| SubagentStart | `background_hook.py` | Register active Claude subagent work for the receipt lifecycle |
| SubagentStop | `background_hook.py` | Mark Claude work complete; infer the correlated start when this runtime emitted no start event |
| PreToolUse (direct writes) | `prewrite_gate.py` | Artifact ownership + plan-first rule |
| PreToolUse (selected mutation/lifecycle tools) | `hook_pre_tool_use.py` | Codex wrapper for direct-write gates and spawn registration recovery |
| UserPromptSubmit | `prompt_memory.py` | Inject stored `[harness-context]` state without Git |
| UserPromptSubmit | `hook_user_prompt_submit.py` | Codex wrapper that injects `$harness:run` routing plus prompt memory |
| PostToolUse (Bash) | `tool_routing.py` | Emit `[harness-hint]` on known failures (wrong test command, missing script) |
| PostToolUse (Bash/Goal/Harness task) | `hook_post_tool_use.py` | Route Bash failures and native `create_goal`; bind successful `task_start`/`task_context` results to the exact Codex session |
| SessionStart | `hook_session_start.py` | Codex plugin wrapper for startup context |
| Explicit note maintenance | `note_freshness.py --paths ...` | Mark selected durable notes suspect without automatic Git scanning |
| Codex task PostToolUse/spawn PreToolUse + MCP background | `codex_hook_registration.py`, `codex_lifecycle_watcher.py` | Bind the exact task/run returned to each root session, register or repair its rollout checkpoint before spawn, then replay real subagent starts and completions from MCP-hosted daemon threads without a detached process |

Codex MCP servers are loaded for the lifetime of the Codex session. After a
Harness runtime update, start a new Codex session before relying on watcher
changes; replacing the installed files does not hot-reload an existing MCP
process.

All hooks are fail-safe (C-12): `|| true` tail, `timeout ≤ 10`. A broken hook degrades gracefully; it never blocks the session. Gates signal decisions via stdout JSON (`hookSpecificOutput.permissionDecision`), so blocking survives the `|| true` wrapper while a script crash still exits 0.

Harness does not install a Bash/shell PreToolUse mutation guard. Direct
Write/Edit operations remain gated; Bash failures may still receive nonblocking
PostToolUse routing hints.

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
  agents/                       # developer, defect-hunter, code/security reviewers, dogfooder, QA/UX lenses
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
```

## Lineage

Harness was assembled, not invented from nothing. Three prior agent-harness
projects supplied its parts, each stripped of host-specific infrastructure and
rewritten against harness contracts:

- **gstack** — the planning pipeline, the four plan review voices, the always-on
  adversarial review posture, hypothesis-driven debugging, and the health /
  retro / checkpoint / learnings scripts.
- **oh-my-claudecode** — read-only reviewer and security-reviewer role
  boundaries, review/QA separation, and the parallel agent fanout convention.
- **Ponytail** — the minimum-sufficient implementation ladder and the minimality
  review lens.

The `defect-hunter` role came from comparing published AI code-review prompts
after harness's own review started behaving like a yes-man.

These are behavioral references, not vendored dependencies. What was **refused**
matters as much as what was taken — see
[doc/harness/PROVENANCE.md](doc/harness/PROVENANCE.md) for the per-flow-part
attribution, the pinned upstream revisions, and the full refusal list.

## Self-dogfooding

This repo uses harness on itself. After installing locally (see [CONTRIBUTING.md](CONTRIBUTING.md)), start a new Claude Code session — the harness SessionStart hooks activate automatically. Use `/harness:setup` to repair or upgrade if needed. The `MAINTENANCE` marker in a task dir bypasses plan-first for urgent fixes.
