#!/usr/bin/env python3
"""Write a task checkpoint snapshot for mid-task resume.

Captures git state, TASK_STATE fields, PROGRESS.md status, and a next-action
line into doc/harness/checkpoints/<TASK_ID>.md. Overwrites prior
checkpoint for the same task (one checkpoint per task — latest wins).

The directory doc/harness/checkpoints/ is gitignored (see setup/bootstrap.md).

Purpose: provide a compact resume snapshot. Survives compaction / session
resume so the next session can recover where a task left off
mid-implementation.

Invocation:
  python3 write_checkpoint.py --task-dir doc/harness/tasks/TASK__xxx/ [--note "..."]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root, now_iso, read_state


def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=5
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _git_context(repo_root: str) -> dict:
    return {
        "branch": _git(["branch", "--show-current"], repo_root) or "unknown",
        "head": _git(["rev-parse", "--short", "HEAD"], repo_root) or "unknown",
        "last_subject": _git(["log", "-1", "--format=%s"], repo_root) or "",
        "dirty_count": len(
            [
                ln
                for ln in _git(["status", "--porcelain"], repo_root).splitlines()
                if ln.strip()
            ]
        ),
    }


def _next_action(state: dict) -> str:
    status = (state.get("status") or "").lower()
    verdict = (state.get("runtime_verdict") or "pending").upper()
    if status in ("", "created"):
        return "Open plan skill — PLAN.md not yet created."
    if status == "planning":
        return "Resume plan skill — plan_session_state may be open."
    if verdict != "PASS":
        return "Resume the current PROGRESS.md step, then run ordered review and QA verification."
    return "runtime_verdict PASS — run task_close."


def _progress_summary(task_dir: str) -> list[str]:
    path = os.path.join(task_dir, "PROGRESS.md")
    try:
        with open(path, encoding="utf-8") as stream:
            lines = [line.rstrip() for line in stream if line.startswith(("phase:", "current_ac:", "partial_ac:"))]
    except OSError:
        return ["(PROGRESS.md absent)"]
    return lines or ["(PROGRESS.md has no compact phase fields)"]


def write_checkpoint(task_dir: str, note: str = "") -> str:
    task_dir = os.path.abspath(task_dir)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"task dir not found: {task_dir}")

    repo_root = find_repo_root(task_dir)
    task_id = os.path.basename(os.path.normpath(task_dir))
    state = read_state(task_dir) or {}
    git_ctx = _git_context(repo_root)
    progress = _progress_summary(task_dir)
    next_act = _next_action(state)

    ck_dir = os.path.join(repo_root, "doc", "harness", "checkpoints")
    os.makedirs(ck_dir, exist_ok=True)
    ck_path = os.path.join(ck_dir, f"{task_id}.md")

    lines = [
        f"# Checkpoint — {task_id}",
        "",
        f"- written: {now_iso()}",
        f"- branch: {git_ctx['branch']}",
        f"- head: {git_ctx['head']} ({git_ctx['last_subject']})",
        f"- dirty files: {git_ctx['dirty_count']}",
        "",
        "## Task state",
        "",
        f"- status: {state.get('status') or 'unknown'}",
        f"- runtime_verdict: {state.get('runtime_verdict') or 'pending'}",
        f"- plan_session_state: {state.get('plan_session_state') or 'unknown'}",
        f"- touched_paths: {len(state.get('touched_paths') or [])}",
        "",
        "## Progress",
        "",
    ]
    lines.extend(f"- {item}" for item in progress)
    lines.extend(["", "## Next action", "", next_act, ""])
    if note:
        lines.extend(["## Note", "", note, ""])

    import tempfile
    fd, tmp = tempfile.mkstemp(dir=ck_dir, prefix=".ckpt.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, ck_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return ck_path


def main() -> int:
    p = argparse.ArgumentParser(description="Write a task checkpoint snapshot")
    p.add_argument("--task-dir", required=True)
    p.add_argument("--note", default="", help="Optional free-form note")
    args = p.parse_args()
    try:
        path = write_checkpoint(args.task_dir, args.note)
    except (FileNotFoundError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"checkpoint: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
