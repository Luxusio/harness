# harness on Codex CLI — install + first run

This guide gets a Codex CLI user from `git clone` to first PASS verdict on a harness task. Time to hello world target: ≤ 5 minutes on a clean Codex 1.1+ install.

If you're a Claude Code user, see the root [`README.md`](README.md) — your install path is `/plugin marketplace add`. The instructions below DO NOT apply to Claude Code.

## Prerequisites

- `codex-cli` ≥ minimum pinned version. Check: `codex --version`. Minimum is recorded in [`plugin-codex/.codex-version`](plugin-codex/.codex-version) (written by AC-008). If you see "Codex 1.x.y required, found 1.a.b" during setup, run `codex upgrade` first.
- Authenticated: `codex login` (or `OPENAI_API_KEY` exported). Test: `echo "ping" | codex exec "say pong"` should return `pong`.
- `python3` available on PATH. The harness server and hook scripts are stdlib-only.

## Install — 3 commands

```bash
# 1. Clone (or fetch if already cloned)
git clone https://github.com/Luxusio/harness.git ~/.harness
cd ~/.harness

# 2. Install into Codex (copies plugin payload to ~/.codex/harness)
python3 install.py --codex-only

# 3. Verify the install
codex exec "list harness mcp tools" < /dev/null
# Expected output mentions task_start, task_verify, task_close, etc.
```

If step 2 fails, re-run with `python3 install.py --codex-only --force`. The installer copies `plugin/` plus the Codex plugin manifest tree into `~/.codex/harness/`, writes `.agents/plugins/marketplace.json`, writes plugin-local `hooks.json`, then writes only plugin enablement and MCP registration into `~/.codex/config.toml`.

## Capability caveats — read before opening a task

This is partial parity. **What's NOT in v1 on Codex:**

- `qa-browser` (browser MCP integration deferred to v2)
- `AskUserQuestion` — Codex has no native equivalent in v1

See [`doc/harness/runtime-matrix.md`](doc/harness/runtime-matrix.md) for the full row-by-row support table.

**What DOES work on Codex:**
- Public loop: `task_start → plan → develop → QA → close` (independent review and `task_verify` are internal close gates)
- Shared MCP server (same `harness_server.py` as Claude)
- Shared Python scripts via `HARNESS_PLUGIN_ROOT` env
- `setup`, plain repo-mutating request routing, native `/goal` orchestration, Goal child-task queues, capability-routed `plan` and `develop`, `qa-cli`, and `qa-api`
- Hooks (prompt/context/safety only; Codex does not use Stop hooks for loop control)

## First-run walkthrough

```bash
cd <your project>

# Plain request: the agent recognizes repo mutation and opens/resumes a task.
codex exec 'fix the flaky test in tests/auth/' < /dev/null

# Or start an explicit Goal. Codex reads native goal context, then syncs it through harness goal_start.
codex exec '/goal fix the flaky test in tests/auth/' < /dev/null

# Watch it walk task start -> plan -> develop -> QA -> close.
# Verify with:
codex exec 'show the active harness goal and next child task' < /dev/null
```

Formal code review deterministically selects ephemeral LIGHT (no hunters),
STANDARD (one selected correctness or contract/test hunter), or DEEP (both
hunters), then always runs one fresh full-sweep authoritative verifier. Depth
is not authoritative lifecycle state or a dedicated task, receipt, or
review-detail field, though selected depth and evidence may appear in stored
non-authoritative formal-review narrative. Conditional security review remains
separate.
Rebase-LIGHT additionally requires exact endpoints, conflict-free one-to-one
patch equivalence, affirmative semantic non-overlap, `HEAD` at the new tip, and
a clean, accounted-for index/worktree. Discovery is capped at two cycles (four
hunter calls maximum at DEEP); exhausted recovery proceeds to one fresh formal
reviewer without another hunter. Receipts stay compact. To retrieve only
the exact formal review named by a receipt's `DETAIL_SHA256`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HARNESS_PLUGIN_ROOT/scripts/review-read" \
  --task-dir doc/harness/tasks/TASK__slug <64-lowercase-hex>
```

An old receipt can legitimately return not found because prior runtimes did not
persist the detail appendix; no fresh task run or migration is required.

If a skill returns "tool not found", check that the MCP server registered: `codex mcp test harness`. If hooks don't fire (e.g. `prewrite_gate.py` doesn't intercept a write to `PLAN.md`), refresh the plugin-local hook install with `python3 install.py --codex-only --force`.

After updating Harness, start a new Codex session before relying on review
storage or lifecycle changes. The already-running MCP process does not
hot-reload replaced plugin files.

## Where to look when things break

- **Codex CLI auth (401 Unauthorized)** — run `codex login` again; `OPENAI_API_KEY` env var is also honored.
- **`codex plugin marketplace add` fails** — `~/.codex/config.toml` already has `[mcp_servers.harness]`; setup refuses to overwrite. Resolve manually.
- **Hooks don't fire** — plugin-local `~/.codex/harness/plugins/harness/hooks.json` or the plugin cache is stale. Re-run `python3 install.py --codex-only --force`.
- **Skill returns "tool not found"** — MCP server didn't register. Run `codex mcp test harness`. Check `command =` path in your `[mcp_servers.harness]` block.
- **Skill output references `mcp__harness__task_start`** — old prompt text; sync engine should have rewritten it. Run `Skill(harness:setup) --regenerate-codex-skills` to re-emit.
- Full troubleshooting: [`doc/harness/codex-troubleshooting.md`](doc/harness/codex-troubleshooting.md).

## Opting out

`plugin-codex/` is opt-in via the `harness.codex_enabled` manifest flag (AC-010). Existing Claude Code installs get no surprise. If you regret the install:

```bash
codex plugin marketplace remove harness
# OR manually remove [plugins."harness@harness"] and [mcp_servers.harness] from ~/.codex/config.toml
```

The Codex runtime copy lives under `~/.codex/harness/`. `plugin-codex/` files in your repo can be deleted after install; Codex executes MCP and hooks from `~/.codex/harness/plugin`. Claude Code is unaffected.
