# Contributing to harness

This file documents local development setup. For end-user install see [README.md](README.md).

## Install / update

```bash
# 1. Clone (first time) or pull (already cloned)
git clone https://github.com/Luxusio/harness
# or, if you already have a clone:
#   cd harness-plugin && git pull

# 2. Install or update (idempotent — first run installs, subsequent runs refresh)
cd harness && ./scripts/install.sh
```

`scripts/install.sh` does three things:

1. `rm -rf ~/.claude/harness-dev` followed by a `tar` pipe into the same path,
   excluding `./.git`. The destination is a runtime mirror, not a working repo,
   so the `.git` directory adds copy time without buying anything. The tar pipe
   batches syscalls through a single stream, much faster than `cp -r` on a tree
   with many small files.
2. Detects whether the `harness` marketplace is already registered.
3. First install → `claude plugin marketplace add` + `claude plugin install
   harness@harness`. Update → `claude plugin marketplace update harness`.

Override the destination with `HARNESS_DEST=/some/other/path ./scripts/install.sh`.

The script uses the `claude plugin` CLI (`marketplace add`, `install`,
`marketplace update`); these map one-to-one to the in-session `/plugin
marketplace add` / `/plugin install` / `/plugin marketplace update` slash
commands.

The destination `~/.claude/harness-dev/` is deliberately distinct from
`~/.claude/plugins/harness/`, which is where Claude Code drops the
*installed* plugin and which must not collide with the source. If you
register the marketplace via the slash command instead of the script,
pass an absolute path (`/home/<your-user>/.claude/harness-dev`) — `/plugin`
is a Claude Code slash command, so shell substitution and tilde expansion
(`$(pwd)`, `~`) are NOT performed.

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
