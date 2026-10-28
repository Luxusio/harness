#!/usr/bin/env python3
"""SessionStart hook: surface pending worktree recovery without completion tracking."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    if os.environ.get("HARNESS_DISABLE_VERIFY_GAP") == "1":
        return 0
    try:
        from _lib import find_repo_root, is_harness_enabled_repo
        repo_root = find_repo_root()
        if not repo_root or not is_harness_enabled_repo(repo_root):
            return 0
    except Exception:
        return 0
    try:
        from batch_pending import render_prompt_block
        recovery = render_prompt_block(repo_root)
    except Exception:
        recovery = "[harness-worktree-recovery] discovery unavailable; inspect MCP context."
    if recovery:
        print(recovery)
    return 0


if __name__ == "__main__":
    sys.exit(main())
