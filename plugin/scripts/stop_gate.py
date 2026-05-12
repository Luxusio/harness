#!/usr/bin/env python3
"""Stop hook — block Claude from stopping while an active harness task is open.

Signals via stdout JSON ({"decision":"block","reason":..., next_action_command:...})
which is the authoritative Stop-hook contract; exit codes are masked by the
`|| true` wrapper in plugin/hooks/hooks.json (see _lib.py:32-36).

Per the 2026-05-12 retro (gate-friction), the reason text now also names the
exact next action — derived from emit_compact_context's missing_for_close —
so the orchestrator can resolve the block without grepping for the helper.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import TASK_DIR, find_repo_root, read_hook_input, emit_compact_context  # type: ignore
from _gate_response import block as gate_block  # type: ignore


def _active_task_id(active_path):
    try:
        with open(active_path, "r", encoding="utf-8") as f:
            first = (f.read().strip().splitlines() or [""])[0]
    except Exception:
        return "(unknown)"
    if not first:
        return "(unknown)"
    return os.path.basename(first.rstrip("/"))[:120]


def _next_action_for_missing(missing_item: str) -> tuple[str, str]:
    """Map a missing_for_close item to (next_action_command, owner_skill).

    Returns "" / "" when the missing item is not recognized.
    """
    if not missing_item:
        return "", ""
    item = missing_item.lower()
    if "plan.md" in item:
        return ("Skill('harness:plan', '<task_id>')",
                "plan-skill")
    if "handoff.md" in item:
        return ("Spawn Agent(subagent_type='harness:developer', ...) to call "
                "mcp__plugin_harness_harness__write_handoff",
                "harness:developer")
    if "qa-browser" in item:
        return ("Spawn Agent(subagent_type='harness:qa-browser', ...) and call "
                "mcp__plugin_harness_harness__write_critic_qa with lens='browser'",
                "harness:qa-browser")
    if "runtime_verdict" in item or "pass" in item:
        return ("mcp__plugin_harness_harness__task_verify { task_id: '<task_id>' } "
                "after running QA",
                "harness:qa-*")
    return "", ""


def _resolve_active_task_dir(repo_root: str, active_path: str) -> str | None:
    try:
        with open(active_path, "r", encoding="utf-8") as f:
            first = (f.read().strip().splitlines() or [""])[0]
    except Exception:
        return None
    if not first:
        return None
    if os.path.isabs(first):
        return first
    return os.path.join(repo_root, TASK_DIR, first.rstrip("/"))


def main():
    try:
        read_hook_input()  # drain stdin so the hook pipe closes cleanly
        repo_root = find_repo_root()
        active_path = os.path.join(repo_root, TASK_DIR, ".active")
        if not os.path.isfile(active_path):
            return 0
        task_id = _active_task_id(active_path)
        td = _resolve_active_task_dir(repo_root, active_path)

        next_action = ""
        owner_skill = ""
        if td and os.path.isdir(td):
            try:
                ctx = emit_compact_context(td)
                missing = (ctx or {}).get("missing_for_close") or []
                if missing:
                    next_action, owner_skill = _next_action_for_missing(missing[0])
            except Exception:
                pass

        reason = (
            f"Active harness task {task_id} is open. Do not stop — finish the "
            "plan -> develop -> verify -> close loop. Legitimate exits: "
            "(1) run task_verify until runtime_verdict=PASS, then call task_close; "
            "or (2) call the AskUserQuestion tool to ask the user whether to "
            "cancel the task — invoke the tool, do not just emit a free-text "
            "question, so the user gets a clean choice."
        )
        payload = gate_block(
            reason=reason,
            next_action_command=next_action,
            owner_skill=owner_skill,
            docs="plugin/CLAUDE.md § Canonical Loop",
        )
        json.dump(payload, sys.stdout)
        return 0
    except Exception:
        return 0  # fail-open — never trap Claude in a bad gate


if __name__ == "__main__":
    sys.exit(main())
