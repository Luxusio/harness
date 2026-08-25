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
        resolve_active_task_dir,
        _log_gate_error,
    )
    import subagent_lifecycle  # type: ignore
except Exception:
    sys.exit(0)


def _log_binding_miss(repo_root: str, payload: dict, event: str) -> None:
    """Leave a breadcrumb when a subagent ran but produced no receipt.

    An empty lifecycle result means the session/task binding did not resolve,
    so no receipt was written. Without this signal that failure is completely
    invisible: the subagent completes normally, receipts stay empty, and
    task_close blocks with no indication of why. Only logged when an active
    task exists, i.e. when a receipt was actually expected.

    Best-effort: never raises into the hook.
    """
    try:
        if not resolve_active_task_dir(repo_root):
            return
        _log_gate_error(
            RuntimeError(
                "subagent lifecycle produced no receipt: session/task binding "
                f"did not resolve (event={event}, "
                f"session_id={str(payload.get('session_id') or '')!r})"
            ),
            "background_hook:binding-miss",
        )
    except Exception:
        pass


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
        result = subagent_lifecycle.handle_subagent_hook(
            repo_root, payload, forced_event=args.event
        )
        if not result:
            _log_binding_miss(repo_root, payload, args.event)
    except Exception as exc:
        try:
            log_gate_crash(exc, "background_hook", last_hook_input())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
