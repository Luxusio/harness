# harness

Execution harness for AI-assisted repository work. Enforces a **WBS open → plan → develop → QA → close** loop on every repo-mutating WBS. Internal review and verification gates remain mandatory.

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

Every repo-mutating WBS follows this public sequence:

```
WBS open → plan → develop → QA → close
```

| Step | What happens |
|------|-------------|
| **WBS open** | Create or resume one exact WBS generation and bind it to the current runtime session |
| **plan** | Review the request, write PLAN.md, and declare required lenses in WBS.control.json |
| **develop** | Implement per-AC; independent review runs as an internal end-of-develop gate |
| **QA** | Run the declared runtime QA lens and record its observed completion |
| **close** | Internally run `wbs(action="verify")`; for Harness source, conditionally refresh stale installed payloads; then publish close authority |

After the same WBS root closes, the coordinator performs self-improvement
before any separately authorized follow-up execution: it may append friction signals to `learnings.jsonl`,
then reports recurring keys from distinct verified WBS runs as Tier 2
candidates. The candidate-reporting step changes neither the raw ledger nor
pattern files; durable pattern changes require a separately reviewed Harness
WBS.

## WBS.control.json (4 fields)

```json
{
  "run_id": "<canonical lowercase UUIDv7>",
  "execution_mode": "standard",
  "required_lenses": ["review-code", "qa-cli"],
  "closed": false
}
```

`wbs_id` is derived from the canonical directory. The public owner publishes
`closed: true` on successful close. `BLOCKED.md` represents an unfinished
external blocker. Existing supported controls require explicit migration
preview/apply; historical receipt bytes are preserved and ignored by active calls.

## Acceptance criteria

Stable AC IDs and success conditions live in `PLAN.md`. Existing WBS assertions
own measured outcomes. Await actual independent code review and conditional
security review PASS before actual QA. Fix actual failures and publish concrete
environmental blockers. Machine verify/close measure current WBS checks and do
not attest review or QA. No recording, naming convention or replacement ledger
is required. See [the active requirement](doc/harness/REQ__receipt-free-execution-and-completion.md).

## Agents

All under `plugin/agents/`. Narrow tool surface — each agent gets only what its role demands.

| Agent | Role |
|-------|------|
| `developer` | Implements PLAN.md per AC |
| `ac-worker` | Implements one AC's owned files; may split disjoint work one level deep on Claude |
| `test-author` | Writes one AC's tests from PLAN.md intent inside its `Tests:` paths alongside the implementation lane |
| `wbs-lead` | `harness:batch` lead: retains its `isolation: worktree` bootstrap with a validated untracked marker, waits for durable worker binding, then runs one WBS lifecycle and commits on its branch |
| `wbs-lead-resume` | Continues the exact retained worktree/WBS/run after worker binding; allocates no new checkout |
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
lifecycle state and has no dedicated WBS, receipt, or review-detail field;
selected depth and evidence may appear in stored non-authoritative formal-review
narrative. Rebase-LIGHT requires exact old/new endpoints,
conflict-free one-to-one patch equivalence, affirmative semantic non-overlap,
`HEAD` at the new tip, and a clean, fully accounted-for index and worktree.
Discovery is capped at two cycles (DEEP: four hunter calls maximum); test-only
retries use contract/test only, narrative corrections use deterministic checks,
and an exhausted budget proceeds to one fresh formal reviewer.

QA/UX agents return actual findings in their final response. Await their results;
coordinator summaries and copied text do not substitute for independent results.
QA agents never hold `Edit`/`Write` on source files. Dogfooder remains a
non-gating backlog pass after QA/UX.

WBS checks measure declared outcomes and source applicability. Work packs also
validate ownership, Git ancestry and integration evidence. Post-review/QA scope
drift remains developer-owned; rerun substantive work only when it is failed,
unrun or stale. Missing recording never warrants reruns or parking.

## Quality scripts

All under `plugin/scripts/`. Stdlib only.

| Script | Purpose | Output |
|--------|---------|--------|
| `health.py` | Weighted composite 0-10 score | stdout |
| `promote_learnings.py` | Current-run-validated Tier 2 candidate reporting; no durable writes | stdout |
| `write_checkpoint.py` | Mid-WBS resume snapshot | `doc/harness/checkpoints/` |
| `retro.py` | Weekly retrospective (git + verified closes + learnings) | stdout; `--save` writes `doc/harness/retros/` |
| `qa_codifier.py` | Parses QA transcripts → regression tests under `tests/regression/` | — |
| `golden_replay.py` | Record/replay runtime smoke runs for deterministic regression | `doc/harness/replays/` |
| `contract_lint.py` | CONTRACTS.md managed-block lint and skill weight checks | — |
| `mutation_probe.py` | Mutates the changed lines of a diff and reports the mutations no test noticed; every survivor is re-run against the full suite first | stdout |
| `runtime_services.py` | Start/status/log helper for manifest-declared runtime services | `doc/harness/runtime/` |
| `codex_hook_registration.py` | Bind native sessions to the exact WBS/run and source workspace | Session focus |
| `setup_finalize.py` | Canonical setup validation, manifest version migration, operational ignores, and legacy version-file cleanup | `.gitignore`, `doc/harness/manifest.yaml` |
| `project_format_check.py` | Read-only SessionStart reminder for older Harness project file formats and missing operational ignores | stdout |
| `verification_target.py` | Persistent copied verification inputs and separate source applicability for authentic review/QA results |
| `verify_runner.py` | Deterministic manifest `verify_commands` runner with optional parallel execution | stdout |
| `req_detector.py` | Detect observable behavior that needs a durable `REQ__*.md` | stdout |
| `wbs.py` | Measure verified outcomes and preserve WBS refinement evidence | `WBS.json` |
| `req_scaffold.py` | Create or update durable REQ scaffolds before observable source work | `doc/<area>/REQ__*.md` |
| `install_verified.py` | Stateless trusted post-QA delivery wrapper; compares canonical payloads from an isolated verified snapshot and refreshes only stale runtimes | stdout / exit status |
| `batch_state.py` | Durable batch records, bounded pool claims, validated bootstrap retention, exact worker/result binding, serialized finish checkpoints, status/recovery, same-worktree resume and explicit retained abandonment; operational state never grants WBS PASS | `doc/harness/runtime/batches/` |
| `batch_worktrees.py` | Routes managed WBS worktree preparation, binding, completion and recovery through the existing batch owners | Managed pool result |
| `batch_pending.py` | Read-only discovery of retained worktree pools and their next lifecycle action after restart; never deletes resources | Pending-pool advisory |
| `work_pack.py` | Validates WBS-owned isolated work packs and integration evidence before completion | Library routing result |
| `wbs_namespace_migration.py` | Explicitly imports retired Task controls and registered execution bindings into the WBS namespace, preserving run/check/history identities and exact source archives; normal calls never use a legacy fallback | WBS migrate preview/apply report |
| `goal_migration.py` | Explicitly previews/imports retired Goal records and pack bindings, preserving historical identities and evidence | WBS migrate report |
| `parallel_dispatch.py` | Proposes ready AC lanes from dependencies, path ownership and observed host capacity; the coordinator reserves actual slots | stdout (JSON) |
| `batch_submodules.py` | Shared S1 selection, private named-branch preparation, durable local object pins, exact checkout reconciliation and module-removal proof; used by the existing batch owners | Embedded batch manifest and private/main Git stores |
| `batch_preflight.py` | `harness:batch` intake/preflight: read-only repo-shape report — control-root shape, submodules, ignored nested repos, a post-checkout hook whose text mentions `submodule`, per-scope class, explicit selected-module validation and whole-gitlink ownership, cleanliness of the main checkout plus every populated submodule and nested repo, whether `.claude/worktrees/` is gitignored at the path git sees (a `.claude` symlink leaving the repository passes), and scope overlap between requests; exits 0 only when those checks allow the requested set | stdout (JSON) |
| `batch_finish.py` | Integration helper (wrapped by `batch_state.py finish` for managed pools) for one closed lead: checks (returned commit is the branch tip, no merge commit, lead worktree clean, main checkout clean and on a branch), rebases in the lead worktree, fast-forwards the main checkout, harvests through `batch_harvest.py`, unlocks only a locked worktree, then plain `git worktree remove` and `git branch -d`; selected state-managed S1 delegates its sole guarded single-force removal to `batch_harvest.py`; `--resume` for a lead resolved by hand in the integration WBS; status `integrated`/`kept`/`conflict`/`ff-refused` as exit 0/3/4/5 (`error`, exit 1, on an unexpected failure) | stdout (JSON) |
| `batch_harvest.py` | `harness:batch` coordinator step: copies an integrated (rebased and fast-forwarded) lead worktree's gitignored WBS evidence and learnings into the main checkout before `git worktree remove`; refuses unregistered, not-yet-integrated, or detached-HEAD (mid-rebase) worktrees, links, non-regular files, and archive collisions. Also owns guarded S1 disposal after durable archive, identity/content and preserved-object proof; ordinary removal stays no-force | `doc/harness/archive/batch/`, `doc/harness/learnings.jsonl` |
| `install_smoke.py` | Drives an installed runtime tree once — imports registered hook modules and checks the receipt-free public WBS lifecycle; run by `install.py` after each sync and on the default run's SYNCHRONIZED skip path | stdout / exit status |
| `runbook_memory.py` | Capture approved runbooks and pending setup-command candidates | `doc/harness/runbooks.yaml` |
| `document_destination.py` | Select local or configured external business-document policy without network calls | Project common YAML |
| `personal_feedback.py` | Inspect a private improvement inbox, check access, or send stdin once with independent authentication | User-home settings; explicit native comment |
| `_gate_response.py` | Shared hook deny/allow response helper | — |
| `verification_gap_check.py` | Resume-time warning for missing verification evidence | — |
| `drift_warn.py` | SessionStart drift detector: compares source against the scripts dir it is executing from, so a session loading a different tree is visible (silent in non-dev / non-harness repos) | — |

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
repeated across 2+ verified WBS runs for a separately reviewed Tier 2
change. That reporting step performs no durable writes; other self-improvement
steps may append signals or save a due retro. The pass completes before
any separately authorized follow-up execution, so each completed run contributes to subsequent work.
`qa_codifier.py` separately turns validated QA failures into regression tests.

## Hooks

| Hook | Script | Purpose |
|------|--------|---------|
| PreToolUse (direct writes) | `prewrite_gate.py` | Artifact ownership + plan-first rule |
| PreToolUse (selected mutation/lifecycle tools) | `hook_pre_tool_use.py` | Codex wrapper for direct-write gates |
| UserPromptSubmit | `prompt_memory.py` | Inject stored `[harness-context]` state without Git |
| UserPromptSubmit | `hook_user_prompt_submit.py` | Codex wrapper that injects `$harness:run` routing plus prompt memory |
| PostToolUse (Bash) | `tool_routing.py` | Emit `[harness-hint]` on known failures (wrong test command, missing script) |
| PostToolUse (Bash/native Goal/WBS) | `hook_post_tool_use.py` | Route Bash failures and native `create_goal`; bind successful WBS open/context results to the exact Codex session |
| SessionStart | `hook_session_start.py` | Codex plugin wrapper for startup context |
| Explicit note maintenance | `note_freshness.py --paths ...` | Mark selected durable notes suspect without automatic Git scanning |

Codex MCP servers are loaded for the lifetime of the Codex session. After a
Harness runtime update, start a new Codex session before relying on runtime
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
| `verify` / `close` / `block` | Measure current WBS checks, close on PASS, or record a real blocker |
| `migrate` | Preview retired Goal data; convert only with explicit `apply=true` |

Retired Goal/task tool aliases are unavailable. Native Goals provide their
objective and continuation to the same WBS. All execution actions accept an
optional `workspace` naming a registered linked worktree; Claude and Codex
preserve that checkout's execution ownership. Migration runs at the
control root without a workspace argument.

## Skills

Business knowledge can be owned by the independent
[Atlassian wiki plugin](wiki-atlassian/README.md). It shares an
automatically synchronized home corpus across projects and provides document
search, native edits, comments and history through the `atlassian` MCP.
Harness selects its documentation policy from the plugin's injected connection environment;
without an external connection, existing local documentation behavior remains.
WBS, source assets and runtime instructions stay repository-owned.

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness in target project |
| `/harness:report-friction` | Produce a copyable report of a real Harness blockage for its improvement developer |
| `/harness:run` | Codex public entry for any repository-mutating workflow |
| `/harness:batch` | Claude: run a bounded worktree-lead pool from one session, persist/resume exact ownership, refill after serial rebase/fast-forward integration, then review and verify |

Normal usage is `/harness:setup` once per repository. On Codex, `$harness:run`
is implicitly selected for plain repository mutation and may also be invoked
explicitly; it loads the internal canonical workflow. Native `/goal` remains
the native objective and continuation context; the run skill opens that same
objective's WBS and continues dependency-ready outcomes. Claude Code uses the
same WBS control plane. `plan`, `develop`, and the four plan-review sub-skills
remain internal. Invoke `/harness:report-friction` on Claude Code or
`$harness:report-friction` on Codex to capture a blocked or inefficient workflow.
It reports existing context and essential evidence without changing the work;
copy its output to the Harness improvement developer.

### Personal improvement inbox

The optional personal Harness inbox is separate from project knowledge. Keep its
address in `~/.config/harness/feedback.yaml`, outside project Git:

```yaml
backend: atlassian
page: "https://YOUR-SITE.atlassian.net/wiki/spaces/TEAM/pages/123456"
```

Keep its own authentication in `~/.config/harness/mise.local.toml`:

```toml
[env]
ATLASSIAN_BASE_URL = "https://YOUR-SITE.atlassian.net"
ATLASSIAN_EMAIL = "YOUR_EMAIL"
ATLASSIAN_API_TOKEN = "YOUR_TOKEN"
```

Use owner-only files (`chmod 600`), an owned directory (`chmod 700
~/.config/harness`), and trust only that file after reviewing it:
`mise trust ~/.config/harness/mise.local.toml`. Private parent directories must
not be writable by other users. These examples are placeholders, not an enabled
connection. Do not commit real destination URLs, reports or credentials.

Both project and inbox use the standard variable names in separate evaluations.
The sender removes inherited project variables and requires all three values to
originate from the exact home mise file. Missing values never borrow global or
project credentials. The private origin pin must match the configured page.
No additional MCP is registered, and the project wiki connection is unchanged.

Run `python -B <plugin-root>/scripts/personal_feedback.py status` to inspect the
destination without loading credentials or making requests. `check` verifies
account and page access without writing. `send` reads a redacted plain UTF-8
report from stdin and appends a native footer comment; ask `report-friction` to
send it when desired. Configuring the destination or invoking ordinary reporting
does not send anything. The helper does not write settings, report files, queues
or caches, and never prints tokens or report bodies.

Accepted page links are HTTPS `/wiki/spaces/KEY/pages/ID[/title]`,
`/spaces/KEY/pages/ID[/title]`, and `/wiki/pages/viewpage.action?pageId=ID`
(or `/pages/viewpage.action?pageId=ID`). Remove tracking queries and fragments;
short/share links need their full page URL. YAML accepts only `backend` and
`page`. Status is still usable without PyYAML when settings are absent; configured
settings require PyYAML in the selected Python, and authentication requires mise.
The private-file path checks require a POSIX host. This sender supports Confluence
Cloud V2 with compatible personal-token Basic auth, not Jira or Data Center.

Success is reported only after exact native comment readback, including its page,
body, status, initial version and authenticated author. Timeout, missing ID or
readback mismatch after POST returns `unknown`: inspect the page and known comment
ID before another submission. No automatic retry occurs. CLI exit codes are
0 for status/check/send success, 1 for unavailable or missing requested setup,
2 for invalid arguments, and 3 for an uncertain write. Local limits are 64 KiB
per settings file, 128 KiB per report and 1 MiB per HTTP/mise result. These are
resource bounds. Replies, attachments and page replacement are outside this
narrow sender. Saving a report does not notify or automatically start a Harness
developer; request that the developer read the inbox.
Retired Goal records and old pack bindings require explicit
`wbs(action="migrate")` preview followed by `apply=true`; there is no live child
queue or compatibility execution path.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json    # plugin manifest
  .mcp.json                     # MCP server config
  CLAUDE.md                     # runtime rules
  hooks/hooks.json              # hook config
  mcp/harness_server.py         # single-tool WBS MCP server
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

This repo uses harness on itself. After installing locally (see [CONTRIBUTING.md](CONTRIBUTING.md)), start a new Claude Code session — the harness SessionStart hooks activate automatically. Use `/harness:setup` to repair or upgrade if needed. The `MAINTENANCE` marker in a WBS dir bypasses plan-first for urgent fixes.
