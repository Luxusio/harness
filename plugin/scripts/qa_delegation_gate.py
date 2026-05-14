#!/usr/bin/env python3
"""PreToolUse hook — surface a WARN when the main session attempts to call a
browser MCP tool (`mcp__chrome-devtools__*`) inline. Implements CONTRACTS.md
C-18 "Verification delegation".

v1 behaviour:
  - Detect any `mcp__chrome-devtools__*` tool invocation by tool_name prefix.
  - Emit a ``permissionDecision: "deny"`` envelope whose
    ``permissionDecisionReason`` redirects the model to spawn
    ``harness:qa-browser``.
  - Silent-log to ``doc/harness/learnings.jsonl`` (``type=qa-delegation-warn``).
  - Escape hatch: ``HARNESS_SKIP_QA_DELEGATION=1`` one-shot allow + log
    ``gate-bypass`` (mirrors the existing prewrite / mcp-bash bypass pattern).

Scope (narrowed 2026-05-14):
  - Bash test runners (pytest, vitest, npm/pnpm/yarn/bun test, jest, mocha,
    cargo/go/mvn/gradle test, rspec, phpunit, rake) are NOT blocked. The
    previous Bash regex matcher fired too aggressively on legitimate inline
    use; user feedback narrowed C-18 to the MCP surface only.
  - curl / wget / httpie / psql / mysql / alembic — ad-hoc HTTP and DB probes
    have too many legitimate inline uses to block.
  - Subagent detection — Claude Code's PreToolUse payload does not expose a
    reliable main-vs-subagent flag today, so the gate fires for any caller.
    qa-browser's own chrome-devtools calls hit the same gate; use the bypass
    env var when needed. A future v2 will refine.

Fail-open by design. Any unexpected exception is swallowed; the gate must
never block the main session because of its own bug.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        read_hook_input,
        emit_permission_decision,
        log_gate_bypass,
        log_gate_crash,
        last_hook_input,
        find_repo_root,
    )
except Exception:
    sys.exit(0)


GATE_NAME = "qa_delegation_gate"
_BLOCKED_TOOL_PREFIX = "mcp__chrome-devtools__"


def _log_warn(tool_name: str) -> None:
    """Append one structured row to doc/harness/learnings.jsonl. Silent-fail."""
    try:
        from datetime import datetime, timezone
        import json
        repo = find_repo_root() or os.getcwd()
        log_path = os.path.join(repo, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "qa-delegation-warn",
            "source": GATE_NAME,
            "key": tool_name,
            "matched_pattern": _BLOCKED_TOOL_PREFIX,
            "tool_name": tool_name,
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    payload = read_hook_input()
    tool_name = payload.get("tool_name") or ""
    if not isinstance(tool_name, str) or not tool_name.startswith(_BLOCKED_TOOL_PREFIX):
        return 0

    if os.environ.get("HARNESS_SKIP_QA_DELEGATION") == "1":
        try:
            log_gate_bypass(GATE_NAME, tool_name)
        except Exception:
            pass
        return 0

    _log_warn(tool_name)

    verb = tool_name[len(_BLOCKED_TOOL_PREFIX):] or "(unknown)"
    reason = (
        f"Browser MCP tool '{tool_name}' detected in main session "
        f"(verb: '{verb}'). Browser-driving MCP calls must be delegated to "
        f"the qa-browser subagent (C-18 Verification delegation). Inline use "
        f"bloats main context with snapshots / screenshots / evaluate payloads."
    )
    next_action = (
        "Spawn Agent(subagent_type='harness:qa-browser', "
        "prompt='Run the browser verification and call write_critic_qa with verdict + transcript.'). "
        "Bypass: HARNESS_SKIP_QA_DELEGATION=1 <retry>"
    )
    emit_permission_decision(
        "deny",
        reason=reason,
        next_action_command=next_action,
        owner_skill="qa-browser",
        docs="plugin/CLAUDE.md § 8c Verification delegation; CONTRACTS.md C-18",
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # AC-007: payload-aware crash log (gate-crash).
        try:
            log_gate_crash(exc, "qa_delegation_gate", last_hook_input())
        except Exception:
            pass
        sys.exit(0)
