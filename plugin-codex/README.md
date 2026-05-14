# harness on Codex CLI

This is the Codex-runtime tree for the harness plugin. **Opt-in.** It does NOT materialize automatically on existing Claude Code installs — set `harness.codex_enabled: true` in your `.claude-plugin/marketplace.json` or invoke `Skill(setup) --include-codex` to enable.

Quickstart, install commands, and capability matrix live one level up:

- **Install + first-run**: see [`README.codex.md`](../README.codex.md) at repo root.
- **Capability matrix** (what works on Codex vs. Claude): see [`doc/harness/runtime-matrix.md`](../doc/harness/runtime-matrix.md).
- **Troubleshooting** (Codex version pin, hook trust, config.toml merge): see [`doc/harness/codex-troubleshooting.md`](../doc/harness/codex-troubleshooting.md).

## What lives here

- `.codex-plugin/plugin.json` — Codex plugin manifest (mirror of Claude's `.claude-plugin/plugin.json`).
- `.codex-version` — minimum Codex CLI version pin. Setup refuses registration if installed Codex is older. (Written by AC-008.)
- `config.toml.example` — annotated snippet showing the `~/.codex/config.toml` block that Codex needs for harness to be discoverable. Setup's additive-merge appends a copy to your real config with a timestamped backup.
- `skills/` — generated SKILL.md ports (output of AC-003 spike → AC-005 sync engine). Each file carries a `# GENERATED — do not edit; source: shared/skills/<name>/SKILL.md` header banner. Edits land in source and re-emit, not here.
- `agents/` — generated agent prompt ports. Same generated-file discipline.
- `hooks.json` — Codex hook config. Schema is byte-identical to Claude's `plugin/hooks/hooks.json`; the script bodies referenced here are SHARED via `plugin/scripts/`. The wrinkle is that Codex requires per-hook trust state (computed `trusted_hash` in `~/.codex/config.toml [hooks.state]`); the setup skill writes this for you.

## What does NOT work on Codex in v1

See `doc/harness/runtime-matrix.md` for the full list. Headline gaps:

- **Dual-voice plan-* review skills** (4014 lines of `plan-ceo-review` / `plan-eng-review` / `plan-design-review` / `plan-devex-review`) — Claude-only. Codex variant deferred to v2.
- **qa-browser** — Codex CAN register Playwright/chrome-devtools MCP servers, but the agent prompt and 14 hard-coded `mcp__chrome-devtools__*` tool names are Claude-coupled. v2.
- **Subprocess fan-out** for parallel qa-*/dogfooder agents — Codex executor runs sequential in v1.
- **AskUserQuestion** — 195 sites in harness skills; Codex has no native equivalent in v1.

For Claude users: `plugin/` continues to work unchanged. This tree is purely additive.

## When this tree is generated

After AC-004 in PLAN.md (canonical-form decision based on AC-003 3-skill spike), the sync engine at `plugin/runtime-sync/` emits content here. v1 skill ports cover `setup`, `run`, `plan`. Other skills get ported in subsequent cycles as needed.
