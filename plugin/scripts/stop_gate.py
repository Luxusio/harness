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
    TASK_DIR, find_repo_root, find_harness_root, harness_root_resolution,
    read_hook_input, emit_compact_context,
    log_gate_crash, last_hook_input, resolve_active_task_dir, current_session_id,
    is_harness_enabled_repo, write_goal_payload_probe,
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
    if "review-security" in item:
        return ("Spawn Agent(subagent_type='harness:security-reviewer', ...); await an explicit PASS for the current diff",
                "harness:security-reviewer")
    if "review" in item:
        return ("Spawn Agent(subagent_type='harness:code-reviewer', ...); await an explicit PASS for the current diff",
                "harness:code-reviewer")
    if "qa-browser" in item:
        return ("Spawn Agent(subagent_type='harness:qa-browser', ...); the hook "
                "records lifecycle receipts; await an explicit PASS",
                "harness:qa-browser")
    if "runtime_verdict" in item or "pass" in item:
        return ("mcp__plugin_harness_harness__task_verify { task_id: '<task_id>' } "
                "after running QA, or spawn Agent(subagent_type='harness:stop-judge') "
                "to assess legitimate pause-with-blocker (transitions runtime_verdict "
                "to BLOCKED_ENV via task_blocked)",
                "harness:qa-* or harness:stop-judge")
    return "", ""


def _owner_for_context_next_action(next_action: str) -> str:
    action = (next_action or "").lower()
    if not action:
        return ""
    if "plan.md" in action or "plan skill" in action:
        return "plan-skill"
    if "review subagent" in action or "read-only review" in action:
        return "harness:code-reviewer or harness:security-reviewer"
    if (
        "spawn a subagent" in action
        or "qa subagent" in action
        or "subagent" in action and "receipt" in action
    ):
        return "harness:qa-*"
    if "task_verify" in action or "runtime verdict" in action:
        return "harness:qa-* or harness:stop-judge"
    if "qa-browser" in action:
        return "harness:qa-browser"
    if "critic-document" in action:
        return "harness:critic-document"
    if "commit-backed learnings" in action or "self-healing candidates" in action:
        return "harness:developer"
    if "task_close" in action:
        return "harness-goal"
    return ""


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
        payload_cwd = str(hook_input.get("cwd") or "").strip()
        hook_cwd = os.path.realpath(payload_cwd or os.getcwd())
        if payload_cwd:
            harness_root, harness_error = harness_root_resolution(hook_cwd)
            repo_root = harness_root or find_repo_root(hook_cwd)
        else:
            candidate_root = find_repo_root()
            repo_root = find_harness_root(candidate_root) or candidate_root
            harness_error = ""
        if harness_error:
            json.dump(gate_block(
                reason=f"Harness workspace configuration is invalid: {harness_error}",
                owner_skill="harness:setup",
                docs="plugin/skills/setup/repo-census.md",
            ), sys.stdout)
            return 0
        if not is_harness_enabled_repo(repo_root):
            return 0
        write_goal_payload_probe(repo_root, hook_input, source="Stop")
        active_path = os.path.join(repo_root, TASK_DIR, ".active")
        td = resolve_active_task_dir(repo_root)
        if not td:
            return 0
        task_id = os.path.basename(td.rstrip("/"))[:120]

        # Official Stop input includes stop_hook_active=true when Claude is
        # already continuing due to a Stop hook. If background work is still
        # active in that recursive path, return success silently: the first
        # Stop hook already forced the continuation, and re-blocking here loops
        # until Claude Code's consecutive hook cap fires.
        if bool(hook_input.get("stop_hook_active")):
            active_background = background_registry.active_records(
                repo_root,
                task_id=task_id,
                session_id=current_session_id(),
                stale_secs=_background_stale_secs(),
            )
            if active_background:
                return 0
        else:
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
        # stop. The stop-judge agent records this transition through
        # task_blocked.
        ctx = None
        if td and os.path.isdir(td):
            try:
                ctx = emit_compact_context(td)
                verdict = (ctx or {}).get("runtime_verdict", "")
                if verdict == "BLOCKED_ENV":
                    return 0  # silent allow after task_blocked
            except Exception:
                ctx = None

        next_action = ""
        owner_skill = ""
        if ctx is not None:
            next_action = (ctx or {}).get("next_action") or ""
            if next_action:
                owner_skill = _owner_for_context_next_action(next_action)
            missing = (ctx or {}).get("missing_for_close") or []
            if missing:
                mapped_action, mapped_owner = _next_action_for_missing(missing[0])
                if not next_action:
                    next_action = mapped_action
                    owner_skill = mapped_owner
                elif not owner_skill:
                    owner_skill = mapped_owner

        # Cancel-push escape removed. The stop-judge agent is the only
        # legitimate non-PASS escape path — it transitions runtime_verdict to
        # BLOCKED_ENV via task_blocked.
        reason = (
            f"Active harness task {task_id} is open. Do not stop — finish the "
            "plan -> develop -> review -> QA -> verify -> close loop. Legitimate exits: "
            "(1) run task_verify until runtime_verdict=PASS, then call task_close; "
            "or (2) spawn Agent(subagent_type='harness:stop-judge') to assess "
            "whether the current state is a genuine pause-with-blocker. Stop-judge "
            "reads PLAN+transcript+work and emits VERDICT_OK_DONE / "
            "VERDICT_OK_BLOCKED / VERDICT_NO_CONTINUE. On VERDICT_OK_BLOCKED it "
            "calls task_blocked to record runtime_verdict=BLOCKED_ENV, write "
            "BLOCKED.md, and clear this session's active marker."
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
