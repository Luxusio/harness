#!/usr/bin/env python3
"""Claude SubagentStart/SubagentStop adapter for receipt-backed lifecycle."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from _lib import (  # type: ignore
        find_repo_root,
        find_harness_root,
        harness_root_resolution,
        is_harness_enabled_repo,
        last_hook_input,
        log_gate_crash,
        read_hook_input,
    )
    import subagent_lifecycle  # type: ignore
except Exception:
    sys.exit(0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Claude subagent lifecycle")
    parser.add_argument("--event", choices=["start", "stop"], default="")
    args = parser.parse_args()
    try:
        payload = read_hook_input()
        payload_cwd = str(payload.get("cwd") or "").strip()
        hook_cwd = os.path.realpath(payload_cwd or os.getcwd())
        if payload_cwd:
            harness_root, _harness_error = harness_root_resolution(hook_cwd)
            if _harness_error:
                return 0
            repo_root = harness_root or find_repo_root(hook_cwd)
        else:
            candidate_root = find_repo_root()
            harness_root, _harness_error = harness_root_resolution(candidate_root)
            if _harness_error:
                return 0
            repo_root = harness_root or candidate_root
        if not is_harness_enabled_repo(repo_root):
            return 0
        subagent_lifecycle.handle_subagent_hook(repo_root, payload, forced_event=args.event)
    except Exception as exc:
        try:
            log_gate_crash(exc, "background_hook", last_hook_input())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
