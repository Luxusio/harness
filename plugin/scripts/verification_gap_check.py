#!/usr/bin/env python3
"""SessionStart hook — surface verification gaps for the active task.

Detects the case where:
  - an active harness task exists
  - manifest declares qa.browser_qa_supported: true
  - the task's touched_paths include frontend files
  - CRITIC__qa.md exists but has no qa-browser section

On match, prints a one-line `[verification-gap]` message so the orchestrator
sees the gap on session resume (the 2026-05-12 retro showed that compaction
summaries lost "browser QA pending" state, leading to premature close
attempts).

Fail-safe: any exception → silent exit 0. Hook wrapper has `|| true` anyway.

Kill switch: ``HARNESS_DISABLE_VERIFY_GAP=1`` → silent exit 0.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    if os.environ.get("HARNESS_DISABLE_VERIFY_GAP") == "1":
        return 0
    try:
        from _lib import (  # type: ignore
            TASK_DIR, find_repo_root,
            _read_nested_manifest_field, _frontend_touched, _has_qa_browser_section,
            read_state, resolve_active_task_dir, is_harness_enabled_repo,
        )
    except Exception:
        return 0

    try:
        repo_root = find_repo_root()
    except Exception:
        return 0
    if not repo_root:
        return 0
    if not is_harness_enabled_repo(repo_root):
        return 0

    task_dir = resolve_active_task_dir(repo_root)
    if not os.path.isdir(task_dir):
        return 0

    try:
        browser_supported = (_read_nested_manifest_field(
            repo_root, "qa", "browser_qa_supported") or "").lower() == "true"
    except Exception:
        return 0
    if not browser_supported:
        return 0

    try:
        st = read_state(task_dir) or {}
    except Exception:
        return 0
    touched = st.get("touched_paths") or []
    if not _frontend_touched(touched):
        return 0

    critic_path = os.path.join(task_dir, "CRITIC__qa.md")
    if not os.path.isfile(critic_path):
        return 0  # no CRITIC yet — runtime_verdict gate already covers this

    if _has_qa_browser_section(task_dir):
        return 0

    task_id = os.path.basename(task_dir.rstrip("/"))
    print(
        f"[verification-gap] active task {task_id}: browser QA required "
        f"(manifest.qa.browser_qa_supported=true + frontend diff) but no "
        f"qa-browser section in CRITIC__qa.md. Spawn qa-browser before close."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
