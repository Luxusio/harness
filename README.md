# harness

Execution harness for AI-assisted repository work. Enforces a **WBS open → plan → develop → QA → close** loop on every repo-mutating task. Internal review and verification gates remain mandatory.

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
WBS open → plan → develop → QA → close
```

| Step | What happens |
|------|-------------|
| **WBS open** | Create or resume one exact task generation and bind it to the current runtime session |
| **plan** | Review the request, write PLAN.md, and declare required lenses in TASK.json |
| **develop** | Implement per-AC; independent review runs as an internal end-of-develop gate |
| **QA** | Run the declared runtime QA lens and record its observed completion |
| **close** | Internally run `wbs(action="verify")`; for Harness source, conditionally refresh stale installed payloads; then publish close authority |

After the same WBS root closes, the coordinator performs self-improvement
before any separately authorized follow-up execution: it may append friction signals to `learnings.jsonl`,
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
| `ac-worker` | Implements one AC's owned files; may split disjoint work one level deep on Claude |
| `test-author` | Writes one AC's tests from PLAN.md intent inside its `Tests:` paths alongside the implementation lane |
| `task-lead` | `harness:batch` lead: retains its `isolation: worktree` bootstrap with a validated untracked marker, waits for durable worker binding, then runs one task lifecycle and commits on its branch |
| `task-lead-resume` | Continues the exact retained worktree/task/run after worker binding; allocates no new checkout |
| `defect-hunter` | Non-attesting evidence-only discovery: LIGHT runs 0, STANDARD 1 selected focus, and DEEP both focuses |
| `code-reviewer` | Always runs fresh after discovery, independently sweeps the full scope, and is the sole `review-code` verdict authority |
| `security-reviewer` | Conditional trust-boundary and exploitability specialist; sole `review-security` authority |
| `dogfooder` | Post-QA power-user pass; finds friction + missing workflows |
| `qa-browser` | Browser-first runtime QA via Chrome DevTools MCP |
| `qa-api` | API runtime QA via curl / httpie |
| `qa-cli` | CLI / library runtime QA |
| `qa-desktop` | Native GUI runtime QA via Device Lab for X11, macOS, Windows, Android and iOS; selected-device capabilities determine actions |
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
unified `RECEIPTS.jsonl`; `wbs(action="verify")` enforces plan-declared lenses and
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
| `verification_target.py` | Persistent copied verification inputs and separate source applicability for authentic review/QA results |
| `verify_runner.py` | Deterministic manifest `verify_commands` runner with optional parallel execution | stdout |
| `req_detector.py` | Detect observable behavior that needs a durable `REQ__*.md` | stdout |
| `wbs.py` | Measure verified outcomes and preserve WBS refinement evidence | `WBS.json` |
| `req_scaffold.py` | Create or update durable REQ scaffolds before observable source work | `doc/<area>/REQ__*.md` |
| `install_verified.py` | Stateless trusted post-QA delivery wrapper; compares canonical payloads from an isolated verified snapshot and refreshes only stale runtimes | stdout / exit status |
| `batch_state.py` | Durable batch records, bounded pool claims, validated bootstrap retention, exact worker/result binding, serialized finish checkpoints, status/recovery, same-worktree resume and explicit retained abandonment; operational state never grants task PASS | `doc/harness/runtime/batches/` |
| `batch_worktrees.py` | Routes managed WBS worktree preparation, binding, completion and recovery through the existing batch owners | Managed pool result |
| `batch_pending.py` | Read-only discovery of retained worktree pools and their next lifecycle action after restart; never deletes resources | Pending-pool advisory |
| `work_pack.py` | Validates task-owned isolated work packs and integration evidence before completion | Library routing result |
| `goal_migration.py` | Explicitly previews/imports retired Goal records and pack bindings, preserving historical identities and evidence | WBS migrate report |
| `parallel_dispatch.py` | Proposes ready AC lanes from dependencies, path ownership and observed host capacity; the coordinator reserves actual slots | stdout (JSON) |
| `batch_submodules.py` | Shared S1 selection, private named-branch preparation, durable local object pins, exact checkout reconciliation and module-removal proof; used by the existing batch owners | Embedded batch manifest and private/main Git stores |
| `batch_preflight.py` | `harness:batch` intake/preflight: read-only repo-shape report — control-root shape, submodules, ignored nested repos, a post-checkout hook whose text mentions `submodule`, per-scope class, explicit selected-module validation and whole-gitlink ownership, cleanliness of the main checkout plus every populated submodule and nested repo, whether `.claude/worktrees/` is gitignored at the path git sees (a `.claude` symlink leaving the repository passes), and scope overlap between requests; exits 0 only when those checks allow the requested set | stdout (JSON) |
| `batch_finish.py` | Integration helper (wrapped by `batch_state.py finish` for managed pools) for one closed lead: checks (returned commit is the branch tip, no merge commit, lead worktree clean, main checkout clean and on a branch), rebases in the lead worktree, fast-forwards the main checkout, harvests through `batch_harvest.py`, unlocks only a locked worktree, then plain `git worktree remove` and `git branch -d`; selected state-managed S1 delegates its sole guarded single-force removal to `batch_harvest.py`; `--resume` for a lead resolved by hand in the integration task; status `integrated`/`kept`/`conflict`/`ff-refused` as exit 0/3/4/5 (`error`, exit 1, on an unexpected failure) | stdout (JSON) |
| `batch_harvest.py` | `harness:batch` coordinator step: copies an integrated (rebased and fast-forwarded) lead worktree's gitignored task evidence and learnings into the main checkout before `git worktree remove`; refuses unregistered, not-yet-integrated, or detached-HEAD (mid-rebase) worktrees, links, non-regular files, and archive collisions. Also owns guarded S1 disposal after durable archive, identity/content and preserved-object proof; ordinary removal stays no-force | `doc/harness/archive/batch/`, `doc/harness/learnings.jsonl` |
| `install_smoke.py` | Drives an installed runtime tree once — imports every registered hook module and checks a bound subagent produces a receipt row; run by `install.py` after each sync and on the default run's SYNCHRONIZED skip path | stdout / exit status |
| `runbook_memory.py` | Capture approved runbooks and pending setup-command candidates | `doc/harness/runbooks.yaml` |
| `review-log` | Append one bounded formal-review final from stdin to the task-local content-addressed detail store | task `REVIEWS.jsonl` |
| `review-read` | Return exactly one stored formal-review final by lowercase SHA-256 digest | stdout |
| `subagent_lifecycle.py` | Receipt-backed Claude lifecycle handling, active-work queries, and trusted stop-only inference | task `RECEIPTS.jsonl` |
| `background_hook.py` | SubagentStart/SubagentStop adapter for direct unified-receipt publication | task `RECEIPTS.jsonl` |
| `_gate_response.py` | Shared hook deny/allow response helper | — |
| `verification_gap_check.py` | Resume-time warning for missing verification evidence | — |
| `drift_warn.py` | SessionStart drift detector: compares source against the scripts dir it is executing from, so a session loading a different tree is visible (silent in non-dev / non-harness repos) | — |
| `hook_tree_health.py` | Reports when the registered hook tree lacks the `SubagentStart`/`SubagentStop` receipt subsystem, so `wbs(action="open")` can warn that receipts cannot be recorded | — |

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
any separately authorized follow-up execution, so each completed run contributes to subsequent work.
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
| PostToolUse (Bash/native Goal/WBS) | `hook_post_tool_use.py` | Route Bash failures and native `create_goal`; bind successful WBS open/context results to the exact Codex session |
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

One callable tool, `wbs`, via `plugin/mcp/harness_server.py`:

| Action | Purpose |
|--------|---------|
| `open` / `context` | Create/resume one WBS execution or refresh its context |
| `plan` / `check` / `next` | Maintain rolling outcomes, run a declared assertion, or select ready outcomes |
| `dispatch` | Bind an isolated work pack and park its parent |
| `verify` / `close` / `block` | Compute the ordered receipt verdict, close on PASS, or record a blocker |
| `migrate` | Preview retired Goal data; convert only with explicit `apply=true` |

Retired Goal/task tool aliases are unavailable. Native Goals provide their
objective and continuation to the same WBS. All execution actions accept an
optional `workspace` naming a registered linked worktree; Claude and Codex
preserve that checkout's execution and receipt ownership. Migration runs at the
control root without a workspace argument.

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:run` | Codex public entry for any repository-mutating workflow |
| `/harness:batch` | Claude: run a bounded worktree-lead pool from one session, persist/resume exact ownership, refill after serial rebase/fast-forward integration, then review and verify |

Normal usage is `/harness:setup` once per repository. On Codex, `$harness:run`
is implicitly selected for plain repository mutation and may also be invoked
explicitly; it loads the internal canonical workflow. Native `/goal` remains
the native objective and continuation context; the run skill opens that same
objective's WBS and continues dependency-ready outcomes. Claude Code uses the
same WBS control plane. `plan`, `develop`, and the four plan-review sub-skills
remain internal. Retired Goal records and old pack bindings require explicit
`wbs(action="migrate")` preview followed by `apply=true`; there is no live child
queue or compatibility execution path.

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
  scripts/                      # stdlib helpers, including batch state/preflight/finish/harvest
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
