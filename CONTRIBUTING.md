# Contributing to harness

This file documents local development setup. For end-user install see [README.md](README.md).

## Local development install

Clone the repo and register it as a local marketplace, then install:

```bash
# 1. Clone
git clone https://github.com/Luxusio/harness harness-plugin
cd harness-plugin

# 2. Register the local checkout as a marketplace (run inside Claude Code from the repo root)
/plugin marketplace add ./

# 3. Install
/plugin install harness
```

`/plugin marketplace add` accepts a directory path (relative or absolute) — `./` works when Claude Code's cwd is the cloned repo. The cloned directory becomes a local marketplace, and `/plugin install harness` then resolves the plugin from it. No symlinks, no manual copies into `~/.claude/plugins/`. Note: `/plugin` is a Claude Code slash command, so shell substitution (e.g. `"$(pwd)"`) is NOT expanded — pass the path literally.

## Validating the install

```bash
claude plugin validate ~/.claude/plugins/harness
```

Confirms the plugin manifest, hooks, skills, and MCP servers are well-formed.

## Updating during development

After pulling new commits or making local changes:

```bash
# Pick up the new revision from the local marketplace
/plugin marketplace update harness

# Or reinstall from scratch
/plugin uninstall harness
/plugin install harness
```

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
