#!/usr/bin/env python3
"""UserPromptSubmit hook — inject compact harness state on every user prompt.

Emits a short ``[harness-context]`` block on stdout when a harness task is
active, so agents don't burn a turn re-reading task artifacts to orient
themselves in fix rounds.

Suspect-note summaries are intentionally not injected here, so prompt hooks do
not dilute the active task's attention scope.

Output is silent outside harness-enabled repos. Total length is hard-capped at
400 chars for the compact prompt context; excess truncates with ``…``.
Pending worktree recovery has its own separate bounded block. The
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
        find_repo_root,
        is_harness_enabled_repo,
        TASK_DIR,
        _log_gate_error,
        resolve_active_task_dir,
        write_session_hint,
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
REVIEW_GATE = (
    "[harness-review/qa] Await independent review PASS before actual QA; "
    "then verify current WBS checks and close."
)
RESTORE_INJECT_CAP = 1400
RESTORE_ARTIFACTS = ("BLOCKED.md",)


def _build_goal_block(repo_root: str) -> str:
    """Native objective and ordinary work share the same WBS."""
    return (
        "[harness-goal] native goal? get_goal->wbs(open); "
        "plain mutating request? wbs(open); plan/check/verify/close on the same root; "
        "next: wbs(next), same outcome tree."
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

    pieces: list = [PREFIX, f"task={task_id}", f"status={status}"]

    block = " ".join(pieces)
    return _truncate(block)


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

    # This hook is one of the few places that receives the real runtime session
    # id. Record it so task_start can bind the active marker to this session
    # instead of the unreadable "default" marker. Advisory and fail-safe: a
    # failure here must never block prompt submission.
    try:
        write_session_hint(repo_root, data.get("session_id"))
    except Exception:
        pass

    task_dir = _find_active_task_dir(repo_root)

    output_parts = [DOC_GATE]
    # Prompt submission is advisory and deliberately performs no Git queries.
    # Authoritative freshness/fingerprint checks remain in task_verify/task_close.
    if task_dir:
        output_parts.append(REVIEW_GATE)
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

    # Recovery is independently capped so normal context cannot truncate it.
    try:
        from batch_pending import render_prompt_block
        recovery = render_prompt_block(repo_root)
    except Exception:
        recovery = "[harness-worktree-recovery] discovery unavailable; inspect MCP context."
    if output_parts or recovery:
        compact = _truncate_output("\n".join(output_parts))
        sys.stdout.write("\n".join(part for part in (compact, recovery) if part))
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
