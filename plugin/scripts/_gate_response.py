"""Canonical gate-response shape for all harness gate scripts.

Every gate (stop_gate, prewrite_gate, mcp_bash_guard, task_close)
emits the same JSON shape so the orchestrator can resolve a block in one step
instead of grepping for the helper script. The retro from 2026-05-12 surfaced
that error messages saying "blocked" without an actionable next step force the
agent into discovery loops.

Shape:
    {
        "decision": "block" | "deny",
        "reason": "<one sentence why>",
        "next_action_command": "<exact CLI or MCP call to resolve>",
        "owner_skill": "<which skill or subagent owns the next step>",
        "docs": "<doc path>"
    }

`decision` field name is preserved across consumers — stop_gate.py uses "block"
(Stop-hook contract), prewrite_gate.py uses "deny" (PreToolUse contract). Both
shapes carry the new informational keys without breaking legacy readers.
"""

from __future__ import annotations


def gate_response(
    decision: str,
    *,
    reason: str,
    next_action_command: str = "",
    owner_skill: str = "",
    docs: str = "",
) -> dict:
    """Return the canonical gate-response dict.

    decision: "block" or "deny" — keep the consumer's expected token.
    reason: one sentence describing why the gate fired.
    next_action_command: the exact CLI or MCP call that resolves the block.
        When empty, callers should still set a sensible default; an empty
        string signals "no canonical next action known".
    owner_skill: which skill / subagent owns the resolution. Helps the
        orchestrator route to the right place ("plan-skill", "harness:developer",
        "harness:qa-browser", "write_plan MCP", etc.).
    docs: optional path to authoritative doc (CONTRACTS.md, plugin/CLAUDE.md,
        agent definition, etc.). Helps future agents pull context without
        re-deriving from grep.

    Backward compat: legacy callers that only read `decision` and `reason`
    continue to work — the new keys are additive.
    """
    return {
        "decision": decision,
        "reason": reason,
        "next_action_command": next_action_command,
        "owner_skill": owner_skill,
        "docs": docs,
    }


def block(reason: str, *, next_action_command: str = "", owner_skill: str = "",
          docs: str = "") -> dict:
    """Shortcut for Stop-hook style "block" decisions."""
    return gate_response("block", reason=reason,
                         next_action_command=next_action_command,
                         owner_skill=owner_skill, docs=docs)


def deny(reason: str, *, next_action_command: str = "", owner_skill: str = "",
         docs: str = "") -> dict:
    """Shortcut for PreToolUse style "deny" decisions."""
    return gate_response("deny", reason=reason,
                         next_action_command=next_action_command,
                         owner_skill=owner_skill, docs=docs)
