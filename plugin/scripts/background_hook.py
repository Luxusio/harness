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


def _receipt_was_expected(diagnostics: dict, payload: dict) -> bool:
    """Was a completion receipt actually owed for this stop?

    Not every SubagentStop belongs to a lifecycle-tracked lens agent. Other
    agent classes stop without ever writing a subagent transcript and without a
    `started` receipt, so they owe no completion — logging them as misses buried
    the real failures under ~25 noise entries during the 2026-08-25 diagnosis.

    A receipt is owed when a matching `started` receipt exists for this run, or
    when the payload names an agent type (a lens agent whose start should have
    been recorded). Absent both, silence is correct. Errs toward logging: an
    unknown shape is reported, not swallowed.
    """
    if diagnostics.get("expected_receipt"):
        return True
    # Use the lifecycle's alias-normalizing accessor, not the raw key: the
    # runtime may supply agentType / subagent_type / a nested dict. Reading
    # `agent_type` directly would silence a genuine lens miss on a build that
    # renames the field — the precise failure this breadcrumb exists to catch.
    try:
        return bool(subagent_lifecycle._agent_type(payload))
    except Exception:
        return bool(str(payload.get("agent_type") or "").strip())


def _log_binding_miss(repo_root: str, payload: dict, event: str, reason: str = "") -> None:
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
        # Record which payload fields were present, not their values. An empty
        # result has several causes (unresolved binding, missing transcript
        # path, missing final text, failed provenance) and the bare message
        # cannot tell them apart — that ambiguity is what made the 2026-08-25
        # outage expensive to diagnose. Keys only: transcripts and assistant
        # text must not be copied into learnings.jsonl.
        present = sorted(
            key for key in (
                "session_id", "agent_id", "agent_type",
                "agent_transcript_path", "last_assistant_message",
            ) if payload.get(key)
        )
        # Whether the runtime's transcript path resolves at hook time, and its
        # last two components for shape comparison. Home-directory prefixes are
        # deliberately not recorded.
        raw_path = str(payload.get("agent_transcript_path") or "")
        transcript_tail = "/".join(raw_path.split(os.sep)[-2:]) if raw_path else ""
        transcript_exists = bool(raw_path) and os.path.exists(raw_path)
        _log_gate_error(
            RuntimeError(
                "subagent lifecycle produced no receipt "
                f"(event={event}, "
                f"session_id={str(payload.get('session_id') or '')!r}, "
                f"provenance_reason={reason or 'n/a'}, "
                f"transcript_exists={transcript_exists}, "
                f"transcript_tail={transcript_tail!r}, "
                f"payload_present={present}, "
                f"payload_keys={sorted(str(key) for key in payload)[:20]})"
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
        diagnostics: dict = {}
        result = subagent_lifecycle.handle_subagent_hook(
            repo_root, payload, forced_event=args.event, diagnostics=diagnostics
        )
        if not result and _receipt_was_expected(diagnostics, payload):
            _log_binding_miss(
                repo_root, payload, args.event,
                str(diagnostics.get("provenance_reason") or ""),
            )
    except Exception as exc:
        try:
            log_gate_crash(exc, "background_hook", last_hook_input())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
