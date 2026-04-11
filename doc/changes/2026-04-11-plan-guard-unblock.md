# plan guard unblock — allow write_artifact.py plan through mcp_bash_guard
date: 2026-04-11
task: TASK__plan-workflow-sub-h-guard-and-gate-fixes

## Summary

`mcp_bash_guard.py` now permits `python3 plugin-legacy/scripts/write_artifact.py plan ...`
through the Bash tool without requiring the `HARNESS_SKIP_MCP_GUARD=1` escape hatch.
The `harness:plan` skill can invoke the CLI directly, closing the degraded-path workaround
that was left open after sub-f (autoplan parity).

## Problem

Before this fix, `mcp_bash_guard.py` matched any invocation of `write_artifact.py` against
`MANAGED_SCRIPT_PATTERNS` and — finding no registered subcommand for `plan` — emitted a
block message of the form:

```
BLOCKED: write_artifact.py is a managed harness script.
Use the appropriate MCP tool instead of calling it directly via Bash.
```

The `harness:plan` skill could not call `write_artifact.py plan` through the Bash tool.
The sub-f workaround was `HARNESS_SKIP_MCP_GUARD=1`, which bypasses the guard entirely.
That bypass was acceptable as a temporary measure but violated the spirit of the CLI-only
invariant: agents were forced to use the Write tool directly instead of going through the
CLI.

## Fix

Commit `ddb8790` adds 23 lines to `plugin-legacy/scripts/mcp_bash_guard.py`:

1. **`allowed_subcommands` set** — declared inside `MANAGED_SCRIPT_PATTERNS["write_artifact.py"]`,
   initially containing `"plan"`. Adding future subcommands here is the only change needed
   to unblock them.
2. **`_subcommand_in_allowlist(cmd_parts, pattern_entry)` helper** — extracts the first
   non-flag token after the script name and tests membership in `allowed_subcommands`.
3. **`main()` allowlist check** — before emitting the block message, consults the helper.
   If the subcommand is on the allowlist, the guard exits 0 and the Bash call proceeds.

The prewrite gate (`prewrite_gate.py`) and PLAN_SESSION.json session-token enforcement are
untouched; owner-identity checks still run downstream regardless of the guard decision.

## Impact

`harness:plan` skill invocations that call:

```bash
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan ...
python3 plugin-legacy/scripts/write_artifact.py plan --artifact plan-meta ...
python3 plugin-legacy/scripts/write_artifact.py plan --artifact audit ...
python3 plugin-legacy/scripts/write_artifact.py plan --artifact checks ...
```

now pass through `mcp_bash_guard.py` cleanly. The `Write via CLI only` invariant is now
actually enforceable for the plan subcommand — agents have no reason to fall back to the
Write tool for PLAN.md, PLAN.meta.json, AUDIT_TRAIL.md, or CHECKS.yaml.

## Prewrite gate still enforces

Unblocking the guard does **not** weaken owner-identity protection. The prewrite gate reads
`PLAN_SESSION.json` and validates that the calling agent holds the plan session token.
Unauthorized writes are still rejected at the gate level.

## Escape hatch preserved

`HARNESS_SKIP_MCP_GUARD=1` remains functional. It bypasses all guard logic and can be used
in testing or emergency scenarios. It is no longer needed for normal plan skill operation.

## Deferred

1. **`task_completed_gate.py` cache version gap** — `plugin-legacy/scripts/task_completed_gate.py`
   line 612 uses `target` (correct). The installed cache at
   `~/.claude/plugins/cache/harness/harness/2.1.0/` has a stale copy at line 604 that uses
   `task_dir`, causing a `NameError` at runtime. The source is already fixed; a plugin
   version bump will refresh the cache and close this gap.

2. **Cross-model dual voice (sub-g)** — Voice B remains on the Agent tool. No `codex exec`,
   `gemini`, or `omc ask` calls have been introduced. Deferred to a separate sub-task.

## References

- Commit: `ddb8790` — fix: allow write_artifact.py plan through mcp_bash_guard
- Task artifacts (gitignored):
  - `doc/harness/tasks/TASK__plan-workflow-sub-h-guard-and-gate-fixes/PLAN.md`
  - `doc/harness/tasks/TASK__plan-workflow-sub-h-guard-and-gate-fixes/HANDOFF.md`
  - `doc/harness/tasks/TASK__plan-workflow-sub-h-guard-and-gate-fixes/CRITIC__runtime.md`
- Prior change doc: `doc/changes/2026-04-11-plan-autoplan-parity.md` (sub-f, introduced the bypass)
