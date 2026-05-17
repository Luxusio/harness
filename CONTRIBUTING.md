# Contributing to harness

This file documents local development setup. For end-user install see [README.md](README.md).

## Install / update

```bash
# 1. Clone (first time) or pull (already cloned)
git clone https://github.com/Luxusio/harness
# or, if you already have a clone:
#   cd harness-plugin && git pull

# 2. Install or update (idempotent — first run installs, subsequent runs refresh)
cd harness && python3 install.py --claude-only --force
```

`install.py --claude-only --force` does four things:

1. Copies the runtime plugin payload from `plugin/` into
   `~/.claude/harness-dev/plugin`, excluding transient caches, and copies the
   single canonical root marketplace manifest into
   `~/.claude/harness-dev/.claude-plugin/marketplace.json`.
2. Detects whether the `harness` marketplace is already registered.
3. If the registered source is stale, removes it, then registers
   `~/.claude/harness-dev` as the marketplace source. The marketplace manifest
   points at `./plugin`.
4. First install → `claude plugin install harness@harness`; every run refreshes
   the MCP server path to `~/.claude/harness-dev/plugin/mcp/harness_server.py`.

Override the destination with
`HARNESS_DEST=/some/other/path python3 install.py --claude-only --force`.

The script uses the `claude plugin` CLI (`marketplace add`, `install`,
`marketplace update`); these map one-to-one to the in-session `/plugin
marketplace add` / `/plugin install` / `/plugin marketplace update` slash
commands.

The destination `~/.claude/harness-dev/` is deliberately distinct from
`~/.claude/plugins/harness/`, which is where Claude Code drops the
*installed* plugin and which must not collide with the source. If you
register a local checkout via slash command instead of `install.py`, either
register the repo root (it contains `.claude-plugin/marketplace.json` pointing
at `./plugin`) or register the installed mirror root
(`/home/<user>/.claude/harness-dev`). `/plugin` is a Claude Code slash command,
so shell substitution and tilde expansion (`$(pwd)`, `~`) are NOT performed.

## Validating the install

```bash
claude plugin validate ~/.claude/plugins/harness
```

Confirms the plugin manifest, hooks, skills, and MCP servers are well-formed.

## Uninstall

```bash
claude plugin uninstall harness
claude plugin marketplace remove harness
```

The slash-command equivalents (`/plugin uninstall harness`,
`/plugin marketplace remove harness`) work too.

## Repo layout

Runtime sources live under `plugin/`. Every change to runtime behavior (paths, hook schemas, agent definitions, skill logic, script APIs) MUST stay internally consistent across `plugin/` — grep for the constant or path before landing the change. See `CLAUDE.md` § "Template sync rule" for details.

## Running the harness loop on the harness repo

This repository dogfoods itself: any repo-mutating change goes through the canonical loop (`plan → develop → verify → close`). Use `/harness:run <slug>` to drive the full cycle, or invoke the individual skills (`/harness:plan`, `/harness:develop`, `/harness:setup`, `/harness:maintain`) directly. CONTRACTS.md is authoritative for what counts as a hard gate vs. a soft warning.
