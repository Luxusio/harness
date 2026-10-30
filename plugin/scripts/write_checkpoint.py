#!/usr/bin/env python3
"""Write a WBS checkpoint snapshot for WBS resume.

Captures git state, derived WBS.control.json state, PROGRESS.md status, and a next-action
line into doc/harness/checkpoints/<WBS_ID>.md. Overwrites prior
checkpoint for the same WBS (one checkpoint per WBS — latest wins).

The directory doc/harness/checkpoints/ is gitignored (see setup/bootstrap.md).

Purpose: provide a compact resume snapshot. Survives compaction / session
resume so the next session can recover where a WBS left off
mid-implementation.

Invocation:
  python3 write_checkpoint.py --wbs-dir doc/harness/wbs/WBS__xxx/ [--note "..."]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    find_repo_root, now_iso, read_wbs_control,
    wbs_control_status,
)


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


def _next_action(wbs_dir: str, control: dict) -> str:
    status = wbs_control_status(wbs_dir, control)
    if not os.path.isfile(os.path.join(wbs_dir, "PLAN.md")) and control.get("execution_mode") != "micro":
        return "Open plan skill — PLAN.md not yet created."
    if status == "closed":
        return "Task closed."
    return "Resume the current PROGRESS.md step, complete independent review and QA, then run WBS checks and close."


def _progress_summary(wbs_dir: str) -> list[str]:
    path = os.path.join(wbs_dir, "PROGRESS.md")
    try:
        with open(path, encoding="utf-8") as stream:
            lines = [line.rstrip() for line in stream if line.startswith(("phase:", "current_ac:", "partial_ac:"))]
    except OSError:
        return ["(PROGRESS.md absent)"]
    return lines or ["(PROGRESS.md has no compact phase fields)"]


def write_checkpoint(wbs_dir: str, note: str = "") -> str:
    wbs_dir = os.path.abspath(wbs_dir)
    if not os.path.isdir(wbs_dir):
        raise FileNotFoundError(f"WBS directory not found: {wbs_dir}")

    repo_root = find_repo_root(wbs_dir)
    wbs_id = os.path.basename(os.path.normpath(wbs_dir))
    control = read_wbs_control(wbs_dir) or {}
    git_ctx = _git_context(repo_root)
    progress = _progress_summary(wbs_dir)
    next_act = _next_action(wbs_dir, control)
    status = wbs_control_status(wbs_dir, control)

    ck_dir = os.path.join(repo_root, "doc", "harness", "checkpoints")
    os.makedirs(ck_dir, exist_ok=True)
    ck_path = os.path.join(ck_dir, f"{wbs_id}.md")

    lines = [
        f"# Checkpoint — {wbs_id}",
        "",
        f"- written: {now_iso()}",
        f"- branch: {git_ctx['branch']}",
        f"- head: {git_ctx['head']} ({git_ctx['last_subject']})",
        f"- dirty files: {git_ctx['dirty_count']}",
        "",
        "## WBS state",
        "",
        f"- status: {status}",
        f"- execution_mode: {control.get('execution_mode') or 'unknown'}",
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
    p = argparse.ArgumentParser(description="Write a WBS checkpoint snapshot")
    p.add_argument("--wbs-dir", required=True)
    p.add_argument("--note", default="", help="Optional free-form note")
    args = p.parse_args()
    try:
        path = write_checkpoint(args.wbs_dir, args.note)
    except (FileNotFoundError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"checkpoint: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
