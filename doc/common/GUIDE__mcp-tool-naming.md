---
type: GUIDE
area: common
title: Harness MCP tool naming across runtimes
freshness: current
invalidated_by_paths:
  - plugin/.mcp.json
  - tests/test_mcp_tool_name_contracts.py
updated: 2026-06-02
---

# Harness MCP tool naming across runtimes

The harness MCP server exposes the same tools under different names depending on
how it is loaded. Getting this wrong looks like an agent bug ("No such tool
available") but is usually a runtime-naming mismatch. Read this before
"fixing" a tool-grant.

## The contract

| Runtime | Tool name shape | Example | Where |
|---------|-----------------|---------|-------|
| Claude plugin (marketplace) | `mcp__plugin_<plugin>_<server>__<tool>` | `mcp__plugin_harness_harness__write_critic_qa` | `plugin/` agent grants + skill prose |
| Codex | bare name, no prefix | `write_critic_qa` | `plugin-codex/skills/` |
| Legacy / banned in `plugin/` | `mcp__harness__<tool>` | `mcp__harness__write_critic_qa` | must not appear under `plugin/` |

- The Claude plugin tree (`plugin/`) uses `mcp__plugin_harness_harness__<tool>`
  exclusively. The plugin is named `harness` and its MCP server (in
  `plugin/.mcp.json`) is named `harness`, so Claude Code's marketplace loader
  namespaces every tool as `mcp__plugin_harness_harness__<tool>`.
- `mcp__harness__<tool>` is the legacy bare-server prefix. It is banned under
  `plugin/`. Do not add it to agent `tools:` grants or skill prose.
- The Codex tree (`plugin-codex/skills/`) uses bare tool names with no `mcp__`
  prefix at all.

## Why a dev session sees "No such tool available"

A development session can load the MCP server straight from a project
`.mcp.json` (server name `harness`) instead of through the marketplace loader.
In that mode the runtime exposes the tools as `mcp__harness__<tool>` (bare server
name), and the main session calls them under that name.

But the `plugin/` agents grant the marketplace name
`mcp__plugin_harness_harness__<tool>`. When the main session spawns a `harness:qa-*`
or `harness:ux-*` agent in this dev mode, the granted name does not match the
exposed `mcp__harness__` name, so the agent reports "No such tool available" and
cannot persist its own `CRITIC__qa.md` / `CRITIC__ux.md`.

This is expected dev-session behavior, not a product bug. In the shipped
marketplace install the tools are exposed as `mcp__plugin_harness_harness__` and
the agent grants resolve correctly.

## What to do (and not do)

- In a dev session where a spawned QA/UX agent cannot call its writer, the
  orchestrator relays the verdict: it calls `write_critic_qa` / `write_critic_ux`
  itself using the agent's returned findings. This keeps the gate honest while
  staying inside the contract.
- Do NOT "fix" the dev artifact by adding `mcp__harness__` to `plugin/` agent
  grants. That violates the contract above and breaks the guard test, and it does
  nothing for the marketplace install (which already works).
- If you genuinely need spawned agents to call their writers directly in dev,
  the correct lever is the dev MCP exposure (how this session registers the
  server), not the `plugin/` agent grants.

## Enforcement

`tests/test_mcp_tool_name_contracts.py`:

- `test_claude_plugin_docs_do_not_use_legacy_harness_mcp_prefix` — asserts
  `mcp__harness__` never appears under `plugin/`.
- `test_claude_plugin_prefixed_tool_names_exist_in_mcp_server` — asserts every
  `mcp__plugin_harness_harness__<tool>` named under `plugin/` is a real server
  tool.
- `test_codex_skills_use_bare_tool_names_not_claude_prefixes` — asserts the
  Codex tree uses bare names and neither `mcp__` prefix.

If you change `plugin/.mcp.json`'s server name, the marketplace prefix changes
with it; update agent grants and these tests together.
