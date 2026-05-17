---
date: 2026-05-14
task: TASK__unified-install-py-codex-claude-parallel
type: feature
---

# install.py — unified parallel install for Codex + Claude

Single entry point at the repo root replaces the previous two-step install UX. `python3 install.py` auto-detects which CLIs are present and runs the install steps for each runtime in parallel via `ThreadPoolExecutor`. Codex side: syncs the runtime payload into `~/.codex/harness/`, registers that installed copy, then merges MCP/hooks/trust state into `~/.codex/config.toml` with timestamped `.bak`. Claude side: `claude plugin marketplace add` + `claude plugin install harness@harness` (hooks auto-resolved from plugin.json) + `claude mcp add harness ...` with env vars. Flags: `--codex-only`, `--claude-only`, `--dry-run`, `--force`, `--config-path`. Stdlib-only (no third-party deps). The Codex config emitter now lives inside `install.py` so there is only one install implementation. Regression tests in `tests/regression/task__unified_install/test_install_py.py` lock the CLI surface and core behaviors.
