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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (  # type: ignore
    TASK_DIR, find_repo_root, read_hook_input, emit_compact_context,
    log_gate_crash, last_hook_input, resolve_active_task_dir, current_session_id,
)
from _gate_response import block as gate_block  # type: ignore
import background_registry  # type: ignore


def _background_wait_budget() -> float:
    try:
        return max(0.0, min(8.0, float(os.environ.get("HARNESS_BACKGROUND_WAIT_SECS", "6"))))
    except ValueError:
        return 6.0


def _background_stale_secs() -> float:
    try:
        return max(1.0, float(os.environ.get("HARNESS_BACKGROUND_STALE_SECS", "1800")))
    except ValueError:
        return 1800.0


def _background_reason(task_id: str, active: list[dict]) -> str:
    lines = [
        f"Active harness task {task_id} has background subagent work still running.",
        "Stop hook already waited automatically; do not stop until lifecycle hooks mark it complete.",
    ]
    for record in active[:5]:
        agent_type = record.get("agent_type") or "subagent"
        agent_id = record.get("id") or "(unknown)"
        age = 0
        try:
            age = int(max(0, time.time() - float(record.get("updated_ts") or time.time())))
        except Exception:
            pass
        lines.append(f"- {agent_type} {agent_id} active for ~{age}s")
    if len(active) > 5:
        lines.append(f"- ... {len(active) - 5} more active records")
    return "\n".join(lines)


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
                "after running QA, or spawn Agent(subagent_type='harness:stop-judge') "
                "to assess legitimate pause-with-blocker (transitions runtime_verdict "
                "to BLOCKED_ENV via task_blocked)",
                "harness:qa-* or harness:stop-judge")
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
        hook_input = read_hook_input()  # drain stdin and populate session id/cwd cache
        repo_root = find_repo_root()
        active_path = os.path.join(repo_root, TASK_DIR, ".active")
        td = resolve_active_task_dir(repo_root)
        if not td:
            return 0
        task_id = os.path.basename(td.rstrip("/"))[:120]

        # Official Stop input includes stop_hook_active=true when Claude is
        # already continuing due to a Stop hook. Do not emit another
        # background-specific block in that recursive path; fall through to the
        # canonical task close gate and let Claude Code's built-in 8-block cap
        # remain the final guard.
        if not bool(hook_input.get("stop_hook_active")):
            wait_result = background_registry.wait_for_clear(
                repo_root,
                task_id=task_id,
                session_id=current_session_id(),
                timeout_secs=_background_wait_budget(),
                stale_secs=_background_stale_secs(),
            )
            if not wait_result.get("cleared"):
                payload = gate_block(
                    reason=_background_reason(task_id, wait_result.get("active") or []),
                    owner_skill="Claude SubagentStart/SubagentStop hooks",
                    docs="plugin/scripts/background_registry.py",
                )
                json.dump(payload, sys.stdout)
                return 0

        # BLOCKED_ENV runtime_verdict permits a legitimate paused-with-blocker
        # stop ONLY when fresh. The stop-judge agent
        # (plugin/agents/stop-judge.md) records this transition through
        # task_blocked.
        #
        # Staleness check (AC-001 of TASK__stop-gate-stale-blocked-env-fix):
        # if any touched_paths file has mtime > CRITIC__qa.md mtime, the
        # BLOCKED_ENV verdict is historical, not current. Activity continued
        # after the env blocker was recorded — fall through to the block
        # payload so the orchestrator must re-verify (via task_verify ->
        # spawn stop-judge again, or task_close on PASS) before stopping.
        ctx = None
        if td and os.path.isdir(td):
            try:
                ctx = emit_compact_context(td)
                verdict = (ctx or {}).get("runtime_verdict", "")
                if verdict == "BLOCKED_ENV" and not ctx.get("stale", False):
                    return 0  # silent allow — fresh BLOCKED_ENV from stop-judge
            except Exception:
                ctx = None

        next_action = ""
        owner_skill = ""
        if ctx is not None:
            missing = (ctx or {}).get("missing_for_close") or []
            if missing:
                next_action, owner_skill = _next_action_for_missing(missing[0])

        # Cancel-push escape removed. The stop-judge agent is the only
        # legitimate non-PASS escape path — it transitions runtime_verdict to
        # BLOCKED_ENV via task_blocked; older task states may still have a
        # stop-judge CRITIC__qa.md section, so stale handling remains here.
        #
        # If we reach here with verdict == "BLOCKED_ENV" the verdict is stale
        # (touched_paths activity post-dates CRITIC__qa.md). Surface that in
        # the reason so the orchestrator routes to a fresh stop-judge spawn.
        stale = bool(ctx and ctx.get("stale"))
        stale_path = (ctx or {}).get("stale_path", "")
        stale_note = ""
        if stale and (ctx or {}).get("runtime_verdict") == "BLOCKED_ENV":
            stale_note = (
                " Note: the existing BLOCKED_ENV verdict is STALE — activity"
                f" on {stale_path or '<touched path>'} post-dates CRITIC__qa.md."
                " Spawn stop-judge again to re-assess current state, or run"
                " task_verify after QA to transition toward PASS."
            )
        reason = (
            f"Active harness task {task_id} is open. Do not stop — finish the "
            "plan -> develop -> verify -> close loop. Legitimate exits: "
            "(1) run task_verify until runtime_verdict=PASS, then call task_close; "
            "or (2) spawn Agent(subagent_type='harness:stop-judge') to assess "
            "whether the current state is a genuine pause-with-blocker. Stop-judge "
            "reads CHECKS+transcript+work and emits VERDICT_OK_DONE / "
            "VERDICT_OK_BLOCKED / VERDICT_NO_CONTINUE. On VERDICT_OK_BLOCKED it "
            "calls task_blocked to record runtime_verdict=BLOCKED_ENV, write "
            "BLOCKED.md, and clear this session's active marker." + stale_note
        )
        payload = gate_block(
            reason=reason,
            next_action_command=next_action,
            owner_skill=owner_skill,
            docs="plugin/CLAUDE.md § 4a Turn-end rule",
        )
        json.dump(payload, sys.stdout)
        return 0
    except Exception as exc:
        # AC-007: even fail-open should leave a diagnostic crash record so
        # Codex-side payload drift doesn't decay into an invisible dead gate.
        try:
            log_gate_crash(exc, "stop_gate", last_hook_input())
        except Exception:
            pass
        return 0  # fail-open — never trap Claude in a bad gate


if __name__ == "__main__":
    sys.exit(main())
