---
date: 2026-05-14
task: TASK__move-emit-codex-config-to-plugin-codex
type: refactor
---

# emit_codex_config.py moved into plugin-codex/install/

After the v1.5 sync-engine reversal (spike-report §3.6), `plugin/runtime-sync/` held a single file: `emit_codex_config.py` — a Codex-install helper that emits the `~/.codex/config.toml` MCP+hook snippet. The "runtime-sync" dir name was leftover from a deleted architecture and made the file's role unclear. Moved it to `plugin-codex/install/emit_codex_config.py` where the role is obvious. Behavior unchanged (AC-006 regression 7/7 green at new path); 4 self-references inside the script updated; `plugin-codex/README.md` and spike-report §3.6 path-references updated. `plugin/runtime-sync/` directory removed.

Superseded later the same day: the helper was folded into repo-root `install.py` and `plugin-codex/install/` was removed so `install.py` is the only install implementation.
