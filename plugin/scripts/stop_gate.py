#!/usr/bin/env python3
"""Stop hook — block Claude from stopping while an active harness task is open.

Signals via stdout JSON ({"decision":"block","reason":..., next_action_command:...})
which is the authoritative Stop-hook contract; exit codes are masked by the
`|| true` wrapper in plugin/hooks/hooks.json (see _lib.py:32-36).

Per the 2026-05-12 retro (gate-friction), the reason names the exact next action
— derived from emit_compact_context's missing_for_close — so the orchestrator
can resolve the block without grepping for the helper. That claim was prose-only
until 2026-09-03: the reason was a fixed paragraph and the derived state reached
the caller only through the `next_action_command` field. It now carries the
missing items and the mapped action.

Keep `reason` close to the one sentence `_gate_response.block` specifies. Rules
about which evidence counts belong to the surface that adjudicates evidence
(`task_verify`, the lens agent definitions); `task_blocked`'s side effects
belong to that tool's description. Duplicating them here is charged to every
turn-end, twice, since the payload surfaces as both hook feedback and a
blocking error.
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
    is_harness_enabled_repo,
    attestation_block_instruction,
    TRUST_BOUNDARY,
)
from _gate_response import block as gate_block  # type: ignore
import subagent_lifecycle  # type: ignore


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
                "once after substantive QA; close on PASS, use a concrete direct "
                "task_blocked call for a genuine external blocker or observed lens "
                f"BLOCKED_ENV, or for qualified missing attestation {attestation_block_instruction()}",
                "harness:qa-* or harness-goal")
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
        return "harness:qa-* or harness-goal"
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
            try:
                active_background = subagent_lifecycle.active_records(
                    repo_root,
                    task_id=task_id,
                    session_id=current_session_id(),
                    stale_secs=_background_stale_secs(),
                )
            except Exception:
                json.dump(gate_block(
                    reason=(
                        f"Harness lifecycle evidence for {task_id} is malformed or unsafe; "
                        "Stop is blocked. Start a fresh task run to reset RECEIPTS.jsonl."
                    ),
                    owner_skill="harness:run",
                    docs="doc/harness/patterns/ADR__consolidated-task-artifacts.md",
                ), sys.stdout)
                return 0
            if active_background:
                return 0
        else:
            try:
                wait_result = subagent_lifecycle.wait_for_clear(
                    repo_root,
                    task_id=task_id,
                    session_id=current_session_id(),
                    timeout_secs=_background_wait_budget(),
                    stale_secs=_background_stale_secs(),
                )
            except Exception:
                json.dump(gate_block(
                    reason=(
                        f"Harness lifecycle evidence for {task_id} is malformed or unsafe; "
                        "Stop is blocked. Start a fresh task run to reset RECEIPTS.jsonl."
                    ),
                    owner_skill="harness:run",
                    docs="doc/harness/patterns/ADR__consolidated-task-artifacts.md",
                ), sys.stdout)
                return 0
            if not wait_result.get("cleared"):
                payload = gate_block(
                    reason=_background_reason(task_id, wait_result.get("active") or []),
                    owner_skill="Claude SubagentStart/SubagentStop hooks",
                    docs="plugin/scripts/subagent_lifecycle.py",
                )
                json.dump(payload, sys.stdout)
                return 0

        # Only the durable task_blocked publication permits a paused-with-blocker
        # stop. A lens-level BLOCKED_ENV receipt still requires task_blocked;
        # runtime_verdict alone is not terminal task state.
        ctx = None
        if td and os.path.isdir(td):
            try:
                ctx = emit_compact_context(td)
                if (ctx or {}).get("status") == "blocked":
                    return 0  # silent allow after task_blocked
            except Exception:
                ctx = None

        next_action = ""
        owner_skill = ""
        missing: list = []
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

        # Cancel-push escape removed. The only legitimate non-PASS exit is a
        # durable task_blocked publication for a qualified blocker.
        #
        # The reason states the current gap and the exits. Evidence-eligibility
        # rules — which records count, review-before-QA ordering, precedence of
        # an actual FAIL — belong to the surface that adjudicates them, the
        # `task_verify` response and the lens agent definitions; `task_blocked`'s
        # side effects belong to that tool's description. Restating all of it
        # here spent ~250 words on every turn-end block, and the payload reaches
        # the model twice (hook feedback and blocking error), while
        # `_gate_response.block` specifies `reason` as one sentence why.
        #
        # The fixed attestation pair is likewise not pinned on unconditionally:
        # C-17 scopes verbatim delivery to the missing-attestation branch, and
        # `_next_action_for_missing` already embeds it in exactly that branch,
        # so it still arrives — via `next_action` below and via the
        # `next_action_command` field — whenever it actually applies.
        missing_summary = ", ".join(str(item) for item in missing[:3])
        if missing and len(missing) > 3:
            missing_summary += f", +{len(missing) - 3} more"

        reason = (
            f"Active harness task {task_id} is open"
            + (f" — missing: {missing_summary}." if missing_summary else ".")
            + " Do not stop; finish task start -> plan -> develop -> QA -> close"
            " (review and task_verify are internal close gates). Exits: after"
            " substantive QA call task_verify once and call task_close only on"
            " runtime_verdict=PASS, or call task_blocked directly for a genuine"
            " external blocker or an observed lens BLOCKED_ENV."
        )
        # Retained deliberately: this is the one part of the old paragraph that
        # changes what may *authorize* an exit rather than restating how the
        # exits work. Skipped only when the inlined next_action already carries
        # the whole boundary — keyed on the constant, never on a fragment of it.
        # An earlier revision keyed this on the substring "structurally
        # delivered" and suppressed the gate's copy in a branch that used the
        # phrase while omitting two elements; a proxy is wrong exactly when the
        # variants disagree, which is why `_lib` owns one canonical text.
        #
        # This was a spelled-out literal until 2026-09-04, because
        # test_direct_blocker_flow_preserves_structural_result_trust_boundary
        # scanned the raw source of every trust surface and a reference is
        # invisible to that scan. That scan now separates prose surfaces (which
        # need their own text) from runtime surfaces (which must compose), so
        # the duplicate no longer buys anything and is gone. `_lib` holds the
        # only literal in runtime code.
        if TRUST_BOUNDARY not in next_action:
            reason += f" {TRUST_BOUNDARY}"
        if next_action:
            reason += f" Next: {next_action}"
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
