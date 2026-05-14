---
title: stop-gate stale BLOCKED_ENV fix
date: 2026-05-14
task: TASK__stop-gate-stale-blocked-env-fix
kind: bugfix
affects: [plugin/scripts/stop_gate.py, plugin/scripts/_lib.py, plugin/mcp/harness_server.py, CONTRACTS.md]
---

## Problem

`stop_gate.py:94` unconditionally returned 0 (permit stop) when
`runtime_verdict == "BLOCKED_ENV"`, with no staleness check.

Failure mode observed 2026-05-14:
1. Codex CLI auth failed mid-task. stop-judge wrote `BLOCKED_ENV`.
2. User authenticated; develop work resumed.
3. Three ACs landed (`implemented_candidate`) — four files written.
4. Orchestrator paused with a continuation question.
5. Stop hook fired, saw stale `BLOCKED_ENV`, permitted the stop illegitimately.

## Fix

Three coordinated changes:

### 1. `plugin/scripts/_lib.py` — `runtime_is_stale` helper extracted here

Previously duplicated inline in `harness_server.py`. Moved to `_lib.py` as
`runtime_is_stale(task_dir) -> tuple[bool, str]` so both the MCP close gate
and `stop_gate.py` share one implementation.

`emit_compact_context` now always populates `ctx["stale"]` and
`ctx["stale_path"]` via `runtime_is_stale`, giving every caller a consistent
signal without recomputing.

Skip list (`_STALE_CHECK_SKIP_SUFFIXES`, `_STALE_CHECK_SKIP_FRAGMENTS`) prevents
`.pyc` / `__pycache__` churn from falsely staling verdicts.

### 2. `plugin/scripts/stop_gate.py` — BLOCKED_ENV branch now checks staleness

```python
# Before (bug):
if verdict == "BLOCKED_ENV":
    return 0  # silent allow

# After (fix):
if verdict == "BLOCKED_ENV" and not ctx.get("stale", False):
    return 0  # silent allow — fresh BLOCKED_ENV only
```

When stale, falls through to block payload. The `reason` string includes a
`STALE` note naming the offending path so the orchestrator knows to spawn
a fresh `harness:stop-judge` or run `task_verify`.

### 3. `plugin/mcp/harness_server.py` — duplicate removed

`_runtime_is_stale` and `_stale_skip` functions (~60 lines) removed from
`harness_server.py`. The module now imports `runtime_is_stale as _runtime_is_stale`
from `_lib` at line 28.

### 4. `CONTRACTS.md` C-17 — Staleness clause added

Title updated: "**fresh** verified runtime_verdict".
Enforced-by line names `_lib.runtime_is_stale`.
New dedicated paragraph explains: BLOCKED_ENV permits stop ONLY when no
`touched_paths` file has `mtime > mtime(CRITIC__qa.md)`.

## Regression test

`tests/regression/task__stop_gate_stale_blocked/test_ac_001__stale_blocked_env_blocks.py`

Two functions:
- `test_stale_blocked_env_emits_block` — fixture writes BLOCKED_ENV verdict,
  then touches a tracked path AFTER; asserts `decision == "block"` and reason
  contains "STALE"/"stale".
- `test_fresh_blocked_env_permits_stop` — fixture writes BLOCKED_ENV verdict
  with all paths pre-dating CRITIC__qa.md; asserts empty stdout (silent allow).

## Do not regress

- `runtime_is_stale` is the single source of truth in `_lib.py`. Never
  re-inline into MCP server or stop_gate.
- `emit_compact_context` must always return `stale` and `stale_path` keys.
- stop_gate BLOCKED_ENV condition: `verdict == "BLOCKED_ENV" and not ctx.get("stale", False)`.
  Removing the `and not stale` half reintroduces the bug.
- C-17 Staleness clause in CONTRACTS.md must be preserved by `maintain` skill.
