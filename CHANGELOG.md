# Changelog

## Unreleased — v2.3.0 (dual-runtime v1)

Opt-in support for OpenAI Codex CLI alongside Claude Code. Pure-additive: existing `plugin/` is untouched.

### Added

- `plugin-codex/` — Codex runtime tree. Mirrors `.claude-plugin/` with `.codex-plugin/plugin.json`, `hooks.json` (schema-identical), and generated `skills/` / `agents/` ports.
- `plugin/runtime-sync/` (planned per AC-005 in `doc/harness/tasks/TASK__dual-runtime-plugin-claude-codex/PLAN.md`) — sync engine that emits Codex tree from canonical sources.
- `plugin/scripts/_lib.runtime_is_stale` — single-source-of-truth staleness check shared by MCP `task_close` gate and Stop hook. `emit_compact_context` now always returns `stale` / `stale_path` keys.
- `doc/harness/runtime-matrix.md` — per-feature capability matrix (Claude / Codex). Read before adopting on Codex.
- `doc/harness/codex-payload-deltas.md` — Codex hook payload schemas with Claude delta. Empirically grounded in codex source + figma plugin reference.
- `doc/harness/apply-patch-matrix.md` — 13-pattern `apply_patch` vs `Edit` matrix. Binding spec for AC-005 sync engine.
- `doc/harness/codex-troubleshooting.md` — error message strings + remediation commands.
- `README.codex.md` — Codex-specific install + first-run walkthrough.

### Changed

- **CONTRACTS.md § C-17** — Staleness clause added. Stop hook permits `BLOCKED_ENV`-based stops ONLY when no `touched_paths` mtime post-dates `CRITIC__qa.md`. Closes the 2026-05-14 loophole where stale verdicts from earlier in a session permitted stops after subsequent work.
- **`plugin/scripts/stop_gate.py`** — BLOCKED_ENV branch consults `ctx["stale"]`; stale verdicts fall through to block payload with an explanatory `stale_note`.
- **`plugin/mcp/harness_server.py`** — replaced inline `_runtime_is_stale` duplicate with import from `_lib`. Single source of truth.

### Deprecated

- **`CLAUDE_PLUGIN_ROOT` env var** — renamed to `HARNESS_PLUGIN_ROOT` (AC-006). Dual-name fallback in `_lib.plugin_root_env()` reads either during deprecation. Old name will be removed in **v2.5.0**. Update your wrappers, shell rc files, and Docker images.

### Compatibility

- v1 keeps `plugin/` untouched. Claude Code users on `claude plugin update` get no surprise sibling tree — `plugin-codex/` materializes only when `harness.codex_enabled: true` in `marketplace.json` (default `false`).
- Cross-runtime task handoff (start on Claude, resume on Codex) is NOT supported in v1. Single-runtime-per-repo. Use `fcntl.flock` on `TASK_STATE.yaml` is a v2 prerequisite for parallel.

## v2.2.0

See git history.
