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

# 2. Add to your Codex config (additive merge with backup)
codex plugin marketplace add ~/.harness/plugin-codex   # OR manual merge per step 3

# 3. Verify the install
codex exec "list harness mcp tools" < /dev/null
# Expected output mentions task_start, task_verify, task_close, etc.
```

If step 2 fails or you prefer manual setup: copy the `[mcp_servers.harness]` and `[hooks]` blocks from [`plugin-codex/config.toml.example`](plugin-codex/config.toml.example) into `~/.codex/config.toml`, replacing `HARNESS_PLUGIN_ROOT` placeholders with the absolute path to your `~/.harness/plugin/` directory. Trust state (`[hooks.state.<key>]`) must also be added — see `codex-troubleshooting.md` if hooks don't fire.

## Capability caveats — read before opening a task

This is partial parity. **What's NOT in v1 on Codex:**

- Dual-voice plan-* review skills (Claude-only by structural necessity)
- `qa-browser` (browser MCP integration deferred to v2)
- Subprocess fan-out for parallel agents (sequential executor in v1)
- `AskUserQuestion` — Codex has no native equivalent in v1

See [`doc/harness/runtime-matrix.md`](doc/harness/runtime-matrix.md) for the full row-by-row support table.

**What DOES work on Codex:**
- Core loop: `plan → develop → verify → close`
- Shared MCP server (same `harness_server.py` as Claude)
- Shared Python scripts via `HARNESS_PLUGIN_ROOT` env
- `setup`, `maintain`, `run` (sequential), `plan` (degraded), `develop` (sequential), `qa-cli`, `qa-api` (after AC-003 ports land in your install)
- Hooks (same event names, same `hooks.json` schema; trust state activation required)

## First-run walkthrough

```bash
cd <your project>

# Start a task. Codex uses $skill or /skills invocation.
codex exec '$harness:run "fix the flaky test in tests/auth/"' < /dev/null

# Watch it walk plan -> develop -> verify -> close.
# Verify with:
codex exec '$harness:run --status' < /dev/null
```

If a skill returns "tool not found", check that the MCP server registered: `codex mcp test harness`. If hooks don't fire (e.g. `prewrite_gate.py` doesn't intercept a write to `PLAN.md`), check trust state — see troubleshooting.

## Where to look when things break

- **Codex CLI auth (401 Unauthorized)** — run `codex login` again; `OPENAI_API_KEY` env var is also honored.
- **`codex plugin marketplace add` fails** — `~/.codex/config.toml` already has `[mcp_servers.harness]`; setup refuses to overwrite. Resolve manually.
- **Hooks don't fire** — trust state missing in `~/.codex/config.toml [hooks.state.<key>]`. See `doc/harness/codex-troubleshooting.md` section "Hook trust state".
- **Skill returns "tool not found"** — MCP server didn't register. Run `codex mcp test harness`. Check `command =` path in your `[mcp_servers.harness]` block.
- **Skill output references `mcp__harness__task_start`** — old prompt text; sync engine should have rewritten it. Run `Skill(harness:setup) --regenerate-codex-skills` to re-emit.
- Full troubleshooting: [`doc/harness/codex-troubleshooting.md`](doc/harness/codex-troubleshooting.md).

## Opting out

`plugin-codex/` is opt-in via the `harness.codex_enabled` manifest flag (AC-010). Existing Claude Code installs get no surprise. If you regret the install:

```bash
codex plugin marketplace remove harness
# OR manually remove the [mcp_servers.harness] and [hooks] / [hooks.state.harness*] blocks from ~/.codex/config.toml
```

`plugin-codex/` files in your repo can be deleted; nothing in `plugin/` references them. Claude Code is unaffected.
