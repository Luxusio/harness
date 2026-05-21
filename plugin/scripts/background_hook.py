#!/usr/bin/env python3
"""Claude SubagentStart/SubagentStop hook adapter for background_registry."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from _lib import find_repo_root, last_hook_input, log_gate_crash, read_hook_input  # type: ignore
    import background_registry  # type: ignore
except Exception:
    sys.exit(0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Claude subagent lifecycle")
    parser.add_argument("--event", choices=["start", "stop"], default="")
    args = parser.parse_args()
    try:
        payload = read_hook_input()
        repo_root = find_repo_root()
        background_registry.handle_subagent_hook(repo_root, payload, forced_event=args.event)
        background_registry.prune(repo_root)
    except Exception as exc:
        try:
            log_gate_crash(exc, "background_hook", last_hook_input())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
