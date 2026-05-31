# REQ - Session Start Hooks No Op Outside Harness

## Intent
Harness SessionStart/PreToolUse/PostToolUse/Stop/Subagent hooks must no-op silently when invoked from a repository that has not completed harness setup. "Not set up" means doc/harness/manifest.yaml is absent. Hooks are installed globally and fire from any project Claude Code or Codex opens, so this no-op contract is what keeps non-harness repos free of runtime files. This REQ captures the expected normal behavior surfaced by the 2026-05-31 stale-install pollution bug (root cause documented in OBS__design-planning-harness-friction.md Implementation-track section; resolving commit 0c5dd7b 2026-05-27).

## Observable Behavior
- In a directory whose nearest ancestor with .git lacks doc/harness/manifest.yaml, every harness hook script returns exit 0, prints nothing to stdout, and creates no files anywhere under that directory.
- The covered hook scripts are: prewrite_gate.py, mcp_bash_guard.py, qa_delegation_gate.py, stop_gate.py, background_hook.py, prompt_memory.py, tool_routing.py, note_freshness.py, hygiene_scan.py, verification_gap_check.py, drift_warn.py.
- Specifically, none of doc/harness/learnings.jsonl, doc/harness/runtime/background.json, doc/harness/runtime/background.json.lock, doc/harness/.hygiene-*, doc/harness/timeline.jsonl, doc/harness/checkpoints/, or doc/harness/tasks/ appear after firing any of those hooks against a non-harness-enabled repo.
- Detection happens through plugin/scripts/_lib.py::is_harness_enabled_repo, which checks for the manifest.yaml file path. Each hook script must call this guard before any write.

## Acceptance Signals
- In a directory whose nearest ancestor with .git lacks doc/harness/manifest.yaml, every harness hook script returns exit 0, prints nothing to stdout, and creates no files anywhere under that directory.
- The covered hook scripts are: prewrite_gate.py, mcp_bash_guard.py, qa_delegation_gate.py, stop_gate.py, background_hook.py, prompt_memory.py, tool_routing.py, note_freshness.py, hygiene_scan.py, verification_gap_check.py, drift_warn.py.
- Specifically, none of doc/harness/learnings.jsonl, doc/harness/runtime/background.json, doc/harness/runtime/background.json.lock, doc/harness/.hygiene-*, doc/harness/timeline.jsonl, doc/harness/checkpoints/, or doc/harness/tasks/ appear after firing any of those hooks against a non-harness-enabled repo.
- Detection happens through plugin/scripts/_lib.py::is_harness_enabled_repo, which checks for the manifest.yaml file path. Each hook script must call this guard before any write.

## Verification Cues
- tests/test_non_harness_hooks_noop.py covers prewrite_gate, qa_delegation_gate, mcp_bash_guard, tool_routing, stop_gate, note_freshness — all subprocess-invoke each hook with a tmp non-harness repo and assert stdout=='' and that no doc/harness/ tree is created.
- A new test (this task) adds the same property to drift_warn.py via tests/test_drift_warn.py::test_drift_warn_noop_outside_harness_enabled_repo.
- Manual: open a fresh terminal in a directory with .git but no doc/harness/manifest.yaml; start a Claude Code session; confirm ls -la doc/ shows no harness/ subdirectory after several tool calls and Stop events.
- Drift surveillance: plugin/scripts/drift_warn.py compares source SHAs to installed SHAs for plugin/scripts/*.py in dev-of-harness repos; if the installed copy lags, it prints a one-line reminder so the no-op fix propagates promptly.

## Non-Goals
- This REQ does not commit to no-op behavior for non-harness invocations of MCP write tools (those live behind an MCP server and require an active session that registered the tool).
- It does not prevent the user from explicitly setting up harness in any repository by creating doc/harness/manifest.yaml.
- It does not promise zero environment writes outside the repository tree (for example, ~/.claude state); only that the repository under work is untouched.

## Source
- created: 2026-05-31
- source: C-100 (CONTRACTS.local.md): bug report -> REQ doc for expected normal behavior. Bug: 2026-05-31 stale-install pollution; resolving commit 0c5dd7b 2026-05-27. Captured ad-hoc via req_scaffold.py CLI (MCP runtime caches TOOLS from session start; loosened MCP path activates next session).
