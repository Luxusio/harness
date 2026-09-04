"""Canonical gate-response shape for all harness gate scripts.

Every active gate (stop_gate, prewrite_gate, task_close)
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

There is a second, non-blocking shape:

    {"continue": true, "systemMessage": "<what the gate observed>"}

returned by `proceed()`. A gate can have a real finding and still have no work
for the caller; blocking there spends a turn on nothing, while silence leaves
an unexplained stop. The absence of `decision` is what makes it non-blocking.
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


def proceed(system_message: str) -> dict:
    """Allow the event, but tell the operator what the gate observed.

    Not every gate outcome is block-or-silence. A gate can have a real,
    reportable finding and still have no work for the caller — the case this
    exists for is a Stop while a lens subagent is running: blocking produces a
    turn with nothing to do in it, while staying silent leaves the operator
    with an unexplained stop.

    `continue: true` is the explicit non-blocking Stop-hook acknowledgement;
    `systemMessage` surfaces text without turning it into a directive the model
    must resolve. Both are documented top-level hook output fields — see the
    output schema in `doc/harness/codex-payload-deltas.md`. Carrying no
    `decision` key is what makes this non-blocking.
    """
    return {"continue": True, "systemMessage": system_message}
