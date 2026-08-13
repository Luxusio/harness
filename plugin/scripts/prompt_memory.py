#!/usr/bin/env python3
"""UserPromptSubmit hook — inject compact harness state on every user prompt.

Emits a short ``[harness-context]`` block on stdout when a harness task is
active, so agents don't burn a turn re-reading task artifacts to orient
themselves in fix rounds.

Pending hygiene and suspect-note summaries are intentionally not injected here.
The post-close `hygiene_followup.py` scheduler turns hygiene output into a
separate task so prompt hooks do not dilute the active task's attention scope.

Output is silent outside harness-enabled repos. Total length is hard-capped at
400 chars for the compact prompt context; excess truncates with ``…``. The
``|| true`` wrapper in ``hooks.json`` (C-12 fail-safe) keeps the session healthy
on any crash.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (  # type: ignore
        read_hook_input,
        read_task_control,
        task_control_status,
        receipt_review_verdict,
        receipt_runtime_verdict,
        find_repo_root,
        is_harness_enabled_repo,
        TASK_DIR,
        _log_gate_error,
        resolve_active_task_dir,
        read_current_goal,
        next_goal_task,
    )
except Exception:
    sys.exit(0)

try:
    from runbook_memory import render_prompt_block as _render_runbook_block  # type: ignore
except Exception:
    _render_runbook_block = None


MAX_BLOCK_CHARS = 400
MAX_OUTPUT_CHARS = 400
PREFIX = "[harness-context]"
DOC_GATE = (
    "[harness-doc-gate] Durable decisions require doc/ update before final; "
    "if no doc applies, keep the PLAN durable-doc rationale specific."
)
QA_GATE = (
    "[harness-qa] PENDING: ALL_TOOLS→spawn_agent(QA lenses)→await status-map "
    "completion; use available list_agents only if wait omitted identities "
    "→explicit VERDICT→task_verify; start≠PASS."
)
REVIEW_GATE = (
    "[harness-review] PENDING: spawn+await structural completions for required "
    "reviewers; available list_agents is fallback only; fresh PASS before QA; "
    "start≠PASS."
)
REVIEW_RECORDED_GATE = (
    "[harness-review/qa] RECORDED review PASS only; task_verify must validate "
    "lenses/freshness, then run required QA. Do not respawn solely from this hint."
)
RESTORE_INJECT_CAP = 1400
RESTORE_ARTIFACTS = ("RECEIPTS.jsonl", "BLOCKED.md")


def _build_goal_block(repo_root: str, synced_goal: dict | None = None) -> str:
    """Inject the runtime Goal sync procedure.

    Hooks cannot call agent-only tools such as Codex get_goal, so the hook
    provides the exact MCP sequence the agent should run when native goal
    context is present.
    """
    try:
        goal = synced_goal or read_current_goal(repo_root)
    except Exception:
        goal = synced_goal or {}
    goal_id = str(goal.get("goal_id") or "")
    objective = _sanitize_prompt_text(str(goal.get("objective") or ""))
    tasks = goal.get("tasks") if isinstance(goal.get("tasks"), list) else []
    try:
        next_info = next_goal_task(repo_root) if goal else {"task": None}
    except Exception:
        next_info = {"task": None}
    task = next_info.get("task") if isinstance(next_info, dict) else None
    next_task = task.get("task_id") if isinstance(task, dict) else ""

    status = ""
    if goal_id:
        status = f" active={goal_id} tasks={len(tasks)}"
        if next_task:
            status += f" next={next_task}"
        if objective:
            status += f" objective={objective[:80]}"
    return (
        "[harness-goal] native goal? get_goal->goal_start; "
        "plain mutating request? task_start; no child: task_start->goal_add_task; "
        "next: goal_next_task."
        + status
    )


def _find_active_task_dir(repo_root: str) -> str:
    td = resolve_active_task_dir(repo_root)
    return td if td and os.path.isdir(td) else ""


def _truncate(block: str) -> str:
    if len(block) <= MAX_BLOCK_CHARS:
        return block
    return block[: MAX_BLOCK_CHARS - 2].rstrip() + " …"


def _truncate_output(output: str) -> str:
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    return output[: MAX_OUTPUT_CHARS - 2].rstrip() + " …"


def _build_block(task_dir: str) -> str:
    control = read_task_control(task_dir)
    if not control:
        return ""
    task_id = os.path.basename(task_dir)
    status = task_control_status(task_dir, control)
    verdict = receipt_runtime_verdict(task_dir, control)

    pieces: list = [PREFIX, f"task={task_id}", f"status={status}"]
    pieces.append(f"recorded_verdict={verdict}")

    block = " ".join(pieces)
    return _truncate(block)


def _build_qa_gate(task_dir: str) -> str:
    """Derive the current verdict; task_verify remains authoritative."""
    try:
        control = read_task_control(task_dir)
        return "" if receipt_runtime_verdict(task_dir, control) == "PASS" else QA_GATE
    except Exception:
        return QA_GATE


def _build_review_gate(task_dir: str) -> str:
    """Report the receipt-derived review state without source-path heuristics."""
    try:
        control = read_task_control(task_dir)
        if receipt_runtime_verdict(task_dir, control) == "PASS":
            return ""
        if receipt_review_verdict(task_dir, control) == "PASS":
            return REVIEW_RECORDED_GATE
        return REVIEW_GATE
    except Exception:
        return REVIEW_GATE


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SYSTEM_REMINDER_RE = re.compile(r"</?system-reminder[^>]*>", re.IGNORECASE)


def _sanitize_path(path: str) -> str:
    """Sanitize a path for injection into system-reminder output.

    Removes control chars, newlines, and system-reminder tag fragments.
    """
    # Strip control chars and newlines
    cleaned = _CONTROL_CHAR_RE.sub("", path)
    # Escape system-reminder tags
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return cleaned.strip()


def _sanitize_prompt_text(text: str) -> str:
    cleaned = _CONTROL_CHAR_RE.sub(" ", str(text or ""))
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return " ".join(cleaned.split()).strip()


def _first_meaningful_line(path: str, cap: int = 180) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = _sanitize_prompt_text(line)
                if not line or line.startswith("#"):
                    continue
                return line[:cap]
    except OSError:
        return ""
    return ""


def _build_restore_block(task_dir: str) -> str:
    """Build a capped resume digest for the active task."""
    if not read_task_control(task_dir):
        return ""
    lines = ["<system-reminder>[harness-restore]"]

    artifact_lines: list[str] = []
    for name in RESTORE_ARTIFACTS:
        snippet = _first_meaningful_line(os.path.join(task_dir, name))
        if snippet:
            artifact_lines.append(f"{name}: {snippet}")
    if artifact_lines:
        lines.append("latest artifacts:")
        lines.extend(f"  - {line}" for line in artifact_lines[:3])

    if len(lines) == 1:
        return ""
    lines.append("</system-reminder>")
    block = "\n".join(lines)
    if len(block) > RESTORE_INJECT_CAP:
        block = block[: RESTORE_INJECT_CAP - 22].rstrip() + "\n...truncated\n</system-reminder>"
    return block


def main() -> int:
    data = read_hook_input()
    repo_root = find_repo_root()
    if not is_harness_enabled_repo(repo_root):
        return 0
    task_dir = _find_active_task_dir(repo_root)

    output_parts = [DOC_GATE]
    # Prompt submission is advisory and deliberately performs no Git queries.
    # Authoritative freshness/fingerprint checks remain in task_verify/task_close.
    if task_dir:
        review_gate = _build_review_gate(task_dir)
        if review_gate:
            output_parts.append(review_gate)
        else:
            qa_gate = _build_qa_gate(task_dir)
            if qa_gate:
                output_parts.append(qa_gate)
        block = _build_block(task_dir)
        if block:
            output_parts.append(block)
        restore_block = _build_restore_block(task_dir)
        if restore_block:
            output_parts.append(restore_block)

    # Approved runbooks + pending candidates are repo-local execution memory.
    # They are advisory and capped inside runbook_memory.py.
    if _render_runbook_block is not None:
        runbook_block = _render_runbook_block(repo_root)
        if runbook_block:
            output_parts.append(runbook_block)

    if not task_dir:
        output_parts.append(_build_goal_block(repo_root))

    if output_parts:
        sys.stdout.write(_truncate_output("\n".join(output_parts)))
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "prompt_memory")
        except Exception:
            pass
        sys.exit(0)
