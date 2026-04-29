#!/usr/bin/env python3
"""Stop hook — block Claude from stopping while an active harness task is open.

Signals via stdout JSON ({"decision":"block","reason":...}) which is the
authoritative Stop-hook contract; exit codes are masked by the `|| true`
wrapper in plugin/hooks/hooks.json (see _lib.py:32-36).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import TASK_DIR, find_repo_root, read_hook_input


def _active_task_id(active_path):
    try:
        with open(active_path, "r", encoding="utf-8") as f:
            first = (f.read().strip().splitlines() or [""])[0]
    except Exception:
        return "(unknown)"
    if not first:
        return "(unknown)"
    # .active may hold either a bare task_id or the full task_dir path
    # (plugin/mcp/harness_server.py:264 writes the path). Collapse to basename.
    return os.path.basename(first.rstrip("/"))[:120]


def main():
    try:
        read_hook_input()  # drain stdin so the hook pipe closes cleanly
        repo_root = find_repo_root()
        active_path = os.path.join(repo_root, TASK_DIR, ".active")
        if not os.path.isfile(active_path):
            return 0
        task_id = _active_task_id(active_path)
        reason = (
            f"Active harness task {task_id} is open. Do not stop — finish the "
            "plan -> develop -> verify -> close loop. Legitimate exits: "
            "(1) run task_verify until runtime_verdict=PASS, then call task_close; "
            "or (2) call the AskUserQuestion tool to ask the user whether to "
            "cancel the task — invoke the tool, do not just emit a free-text "
            "question, so the user gets a clean choice."
        )
        json.dump({"decision": "block", "reason": reason}, sys.stdout)
        return 0
    except Exception:
        return 0  # fail-open — never trap Claude in a bad gate


if __name__ == "__main__":
    sys.exit(main())
