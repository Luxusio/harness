# Contributing to harness

This file documents local development setup. For end-user install see [README.md](README.md).

## Install / update

Both first install and subsequent updates use the same sync mechanism:
copy the cloned source into a stable path under `~/.claude/`, then point the
local marketplace at that copy.

```bash
# 1. Clone (first time) or pull (already cloned)
git clone https://github.com/Luxusio/harness harness-plugin
# or, if you already have a clone:
#   cd harness-plugin && git pull

# 2. Sync the source into ~/.claude/harness-dev (works for first install AND updates)
rm -rf ~/.claude/harness-dev
cp -r harness-plugin ~/.claude/harness-dev

# 3a. First install — register the marketplace and install the plugin
/plugin marketplace add /home/<your-user>/.claude/harness-dev
/plugin install harness

# 3b. Subsequent updates — refresh the marketplace
/plugin marketplace update harness
```

Two reasons for the `rm -rf` + `cp -r` pair: `cp -r` alone does not propagate
upstream deletions (orphan files linger), and `cp -r src dest` copies INTO
`dest` if `dest` already exists — nuking first guarantees a clean tree every
time.

`/plugin marketplace add` accepts a literal directory path. Pass an absolute
path (e.g. `/home/<your-user>/.claude/harness-dev`) — `/plugin` is a Claude
Code slash command, so shell substitution and tilde expansion (`$(pwd)`,
`~`) are NOT performed. The destination `~/.claude/harness-dev/` is
deliberately distinct from `~/.claude/plugins/harness/`, which is where
Claude Code drops the *installed* plugin and which must not collide with
the source.

## Validating the install

```bash
claude plugin validate ~/.claude/plugins/harness
```

Confirms the plugin manifest, hooks, skills, and MCP servers are well-formed.

## Uninstall

```bash
# Remove the plugin
/plugin uninstall harness

# Remove the local marketplace registration
/plugin marketplace remove harness
```

## Repo layout

Runtime sources live under `plugin/`. Every change to runtime behavior (paths, hook schemas, agent definitions, skill logic, script APIs) MUST stay internally consistent across `plugin/` — grep for the constant or path before landing the change. See `CLAUDE.md` § "Template sync rule" for details.

## Running the harness loop on the harness repo

This repository dogfoods itself: any repo-mutating change goes through the canonical loop (`plan → develop → verify → close`). Use `/harness:run <slug>` to drive the full cycle, or invoke the individual skills (`/harness:plan`, `/harness:develop`, `/harness:setup`, `/harness:maintain`) directly. CONTRACTS.md is authoritative for what counts as a hard gate vs. a soft warning.
