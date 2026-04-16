#!/usr/bin/env python3
"""Write a task checkpoint snapshot for mid-task resume.

Captures git state, TASK_STATE fields, open/failed ACs from CHECKS.yaml, and a
next-action line into doc/harness/checkpoints/<TASK_ID>.md. Overwrites prior
checkpoint for the same task (one checkpoint per task — latest wins).

The directory doc/harness/checkpoints/ is gitignored (see setup/bootstrap.md).

Purpose: fill the gap between timeline.jsonl (append-only event log) and
HANDOFF.md (close-time). Survives compaction / session resume so the next
session can recover where a task left off mid-implementation.

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


def _pluck_acs(checks_path: str) -> tuple[list[dict], int]:
    """Return [(id, status, title)] for non-terminal ACs, plus total count.

    Parser mirrors update_checks.py — regex scan of `- id:` blocks. Only
    surfaces status in {open, implemented_candidate, failed} since passed /
    deferred don't block resume.
    """
    if not os.path.isfile(checks_path):
        return [], 0
    try:
        with open(checks_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return [], 0

    import re

    blocks = re.split(r"(?m)^(?=-\s+id:\s*)", text)
    active = []
    total = 0
    surface = {"open", "implemented_candidate", "failed"}
    for blk in blocks:
        m = re.match(r"^-\s+id:\s*(\S+)", blk)
        if not m:
            continue
        total += 1
        ac_id = m.group(1).strip().strip('"').strip("'")
        sm = re.search(r"^\s+status:\s*(\S+)", blk, re.MULTILINE)
        status = sm.group(1).strip() if sm else "unknown"
        tm = re.search(r'^\s+title:\s*"?(.+?)"?\s*$', blk, re.MULTILINE)
        title = tm.group(1).strip() if tm else ""
        if status in surface:
            active.append({"id": ac_id, "status": status, "title": title})
    return active, total


def _next_action(state: dict, active_acs: list[dict]) -> str:
    status = (state.get("status") or "").lower()
    verdict = (state.get("runtime_verdict") or "pending").upper()
    if status in ("", "created"):
        return "Open plan skill — PLAN.md not yet created."
    if status == "planning":
        return "Resume plan skill — plan_session_state may be open."
    failed = [a for a in active_acs if a["status"] == "failed"]
    if failed:
        return f"Address {len(failed)} failed AC(s): {', '.join(a['id'] for a in failed[:3])}"
    open_acs = [a for a in active_acs if a["status"] == "open"]
    if open_acs:
        return f"{len(open_acs)} AC(s) still open — continue develop lane."
    impl = [a for a in active_acs if a["status"] == "implemented_candidate"]
    if impl:
        return f"{len(impl)} AC(s) at implemented_candidate — run task_verify."
    if verdict != "PASS":
        return "All ACs passed status-wise — run task_verify to gate runtime_verdict."
    return "runtime_verdict PASS — run task_close."


def write_checkpoint(task_dir: str, note: str = "") -> str:
    task_dir = os.path.abspath(task_dir)
    if not os.path.isdir(task_dir):
        raise FileNotFoundError(f"task dir not found: {task_dir}")

    repo_root = find_repo_root(task_dir)
    task_id = os.path.basename(os.path.normpath(task_dir))
    state = read_state(task_dir) or {}
    git_ctx = _git_context(repo_root)
    active_acs, total_acs = _pluck_acs(os.path.join(task_dir, "CHECKS.yaml"))
    next_act = _next_action(state, active_acs)

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
        "## Active ACs",
        "",
    ]
    if not active_acs:
        lines.append(f"(none — {total_acs} AC(s) total, all passed/deferred or CHECKS absent)")
    else:
        lines.append(f"{len(active_acs)} of {total_acs} AC(s) non-terminal:")
        lines.append("")
        for ac in active_acs:
            t = ac["title"]
            t = (t[:80] + "…") if len(t) > 80 else t
            lines.append(f"- **{ac['id']}** [{ac['status']}] {t}")
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
