# harness on Codex CLI

This is the Codex-runtime tree for the harness plugin. **Opt-in.** It does NOT materialize automatically on existing Claude Code installs — set `harness.codex_enabled: true` in your `.claude-plugin/marketplace.json` or invoke `Skill(setup) --include-codex` to enable.

Architecture lives in [`doc/harness/spike-report.md`](../doc/harness/spike-report.md) §3.6 — **MCP-only sharing**:
- Shared across runtimes: MCP server, hook payload schemas, `plugin/scripts/` (gate scripts, helpers), contract artifacts (PLAN.md / CHECKS.yaml / HANDOFF.md / DOC_SYNC.md / CRITIC__qa.md), `plugin/runtime-sync/emit_codex_config.py` (bridges the shared substrate to a Codex install).
- Independent per runtime: SKILL.md trees, agent definitions. Hand-authored on each side, both consuming the same shared substrate.

The earlier v1.5 trajectory (canonical YAML → dual-emitted SKILL.md) was reversed after the spike measured 60% weighted-mean mechanical-portability with 100% restructure cost on control-flow primitives (Agent fan-out, Skill chain, AskUserQuestion). The negative ROI is documented in §3.6 for future contributors.

## Quickstart

- **Install + first-run**: see [`README.codex.md`](../README.codex.md) at repo root.
- **Capability matrix** (Codex vs. Claude): see [`doc/harness/runtime-matrix.md`](../doc/harness/runtime-matrix.md).
- **Troubleshooting** (Codex version pin, hook trust, config.toml merge): see [`doc/harness/codex-troubleshooting.md`](../doc/harness/codex-troubleshooting.md).

## What lives here

- `.codex-plugin/plugin.json` — Codex plugin manifest (mirror of Claude's `.claude-plugin/plugin.json`).
- `.codex-version` — minimum Codex CLI version pin (0.130.0). Setup refuses registration if installed Codex is older.
- `config.toml.example` — annotated snippet showing the `~/.codex/config.toml` block that Codex needs for harness to be discoverable. Setup's additive-merge appends a copy to your real config with a timestamped backup.
- `skills/` — 9 hand-authored Codex SKILL.md variants of the harness user-facing skills. Each file carries a `# HAND-PORTED — source: plugin/skills/<name>/SKILL.md` header banner and a Codex runtime notes block in the body. Each ends with a `## Codex port measurement` table tracking the empirical port profile.
- `agents/` — 7 agent definitions as **methodology references**. On Claude these spawn via `Agent(subagent_type=...)`; on Codex 0.130.0 there is no Agent primitive in this scope, so the harness orchestrator reads them inline and executes the role's methodology in its own conversation context.
- `hooks.json` — Codex hook config. Schema is byte-identical to Claude's `plugin/hooks/hooks.json`; the script bodies referenced here are SHARED via `plugin/scripts/`. Codex requires per-hook trust state (`trusted_hash` in `~/.codex/config.toml [hooks.state]`); the setup skill writes this for you.

## Skills (9 ported)

| Skill | Source L | Codex L | as-is % | Notes |
|-------|----------|---------|---------|-------|
| setup | — | — | 71 | v1.5 spike port |
| run | 171 | 176 | 56 | v1.5 spike port |
| plan | — | 292 | 45 | v1.5 spike port; dual-voice degrades to single-voice |
| develop | 500 | 511 | 48 | Agent fan-out → sequential; sub-files fall back to plugin/skills/develop/<sub>.md |
| maintain | 123 | 156 | 72 | Highest as-is — no control-flow primitives in source |
| plan-ceo-review | 1293 | 1335 | 52 | 14 AskUQ → prose; single-voice degraded adversarial |
| plan-eng-review | 846 | 912 | 55 | 9 AskUQ → prose; rubrics sub-file falls back to Claude tree |
| plan-design-review | 853 | 910 | — | Browser MCP refs degrade to ASCII wireframes + `open file://...` |
| plan-devex-review | 1022 | 1105 | 52 | dx-hall-of-fame.md sub-file falls back to Claude tree |

## Agents (7 ported as methodology references)

- `stop-judge.md` — assesses legitimate-stop vs premature-stop on in-progress tasks. The orchestrator on Codex runs this methodology inline when the Stop hook directs it to.
- `qa-cli.md` — CLI / library QA lens.
- `qa-api.md` — API endpoint QA lens.
- `qa-browser.md` — browser QA lens. Methodology preserved; runtime path deferred until Codex Playwright MCP lands (v2).
- `qa-desktop.md` — native GUI / x11 QA lens.
- `dogfooder.md` — post-PASS user-facing-experience pass.
- `developer.md` — HANDOFF / DOC_SYNC / change-doc writer role.

## What's deferred to v2

- **Parallel sub-agent fanout** — Codex 0.130.0 has no `Agent(subagent_type=...)` primitive in skill scope. Develop Phase 3.0 (per-AC parallel), Phase 4.5-4.8 (parallel quality audit), Phase 7 (multi-lens QA in one batch), Phase 7.7 (dogfooder spawn) all collapse to sequential inline on Codex. Will revisit when Codex `multi_agent` ergonomics make subagent spawn cheap.
- **Browser MCP verification** — qa-browser methodology is ported, but runtime calls to `mcp__chrome-devtools__*` have no Codex equivalent yet. Wire Codex Playwright MCP in v2.
- **Dual-voice plan-* reviews** — plan-skill's Voice A / Voice B fan-out for the 4 plan-* review lenses degrades to single-voice on Codex (no Agent primitive). v2 fix is multi_agent-based; until then, users wanting dual-voice fidelity should run `claude $/harness:plan-<lens>-review` against the Claude side.
- **AskUserQuestion** — every call site in the 9 ported skills converted to conversational prose with numbered/lettered options. Functional but UX-wise less discoverable than the structured tool. v2 may introduce a Codex helper that renders prose asks with a consistent shape.
- **Prewrite gate role-detection on Codex** — when the Codex orchestrator runs subagent-role methodology inline (e.g. qa-cli writes CRITIC__qa.md, developer writes HANDOFF.md), the prewrite gate's role-detection currently keys off the Claude subagent-name surface. Until v2 lands runtime-aware role detection in `plugin/scripts/prewrite_gate.py`, the orchestrator uses `HARNESS_SKIP_PREWRITE=1` (documented bypass per CLAUDE.md env-var table; logs `type=gate-bypass` to `learnings.jsonl`).
- **Stop hook + qa_delegation_gate prose** — both gate scripts emit Claude-specific prose ("spawn `Agent(subagent_type='harness:stop-judge')`") that does not translate to Codex's flow. v2 fix is runtime detection in the gate scripts.

## For Claude users

`plugin/` continues to work unchanged. This tree is additive — no behavior change to the Claude side.
