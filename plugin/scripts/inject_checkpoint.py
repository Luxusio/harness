#!/usr/bin/env python3
"""SessionStart hook — surface latest checkpoint for context recovery.

Scans doc/harness/checkpoints/ for the most recently modified .md file
and prints a one-paragraph resume briefing. Runs under a 5s timeout so
parsing is minimal — just read the file and extract key lines.

Exit 0 always (hook must not block startup).

Invocation (via hooks.json):
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/inject_checkpoint.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

DIR = "doc/harness/checkpoints"


def main() -> int:
    repo_root = find_repo_root()
    ck_dir = os.path.join(repo_root, DIR)
    if not os.path.isdir(ck_dir):
        return 0

    files = [
        os.path.join(ck_dir, f)
        for f in os.listdir(ck_dir)
        if f.endswith(".md")
    ]
    if not files:
        return 0

    latest = max(files, key=os.path.getmtime)
    try:
        with open(latest, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return 0

    task_id = os.path.splitext(os.path.basename(latest))[0]

    def _extract(heading: str) -> str:
        m = re.search(
            rf"^## {re.escape(heading)}\s*\n(.*?)(?=\n## |\Z)",
            content, re.MULTILINE | re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    status_block = _extract("Task state")
    next_action = _extract("Next action")

    status_line = ""
    for ln in status_block.splitlines():
        if "status:" in ln:
            status_line = ln.strip().lstrip("- ")
            break
    verdict_line = ""
    for ln in status_block.splitlines():
        if "runtime_verdict:" in ln:
            verdict_line = ln.strip().lstrip("- ")
            break

    written_m = re.search(r"^- written: (.+)$", content, re.MULTILINE)
    written = written_m.group(1).strip() if written_m else "unknown"

    branch_m = re.search(r"^- branch: (.+)$", content, re.MULTILINE)
    branch = branch_m.group(1).strip() if branch_m else ""

    active_acs = _extract("Active ACs")
    ac_count = len(re.findall(r"^\- \*\*AC-", active_acs, re.MULTILINE))

    parts = [f"[checkpoint] {task_id}"]
    if branch:
        parts.append(f"branch={branch}")
    if status_line:
        parts.append(status_line)
    if verdict_line:
        parts.append(verdict_line)
    if ac_count:
        parts.append(f"active_acs={ac_count}")
    parts.append(f"at={written}")

    print(" | ".join(parts))
    if next_action:
        print(f"  next: {next_action}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
