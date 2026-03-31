#!/usr/bin/env python3
"""Pure policy helper for observability overlay activation.

Determines whether the observability overlay should be activated for a task.
No side effects, no I/O beyond manifest reading. Safe to call from tests.

Usage:
    from observability_policy import evaluate_observability_activation

    result = evaluate_observability_activation(task_dir)
    # result = {"activate": bool, "reason": str}

CLI usage (for debugging):
    python3 observability_policy.py <task_dir>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    yaml_field, yaml_array, manifest_field, manifest_section_field,
    is_tooling_ready, should_activate_observability,
)


def evaluate_observability_activation(task_dir):
    """Evaluate whether observability overlay should be activated for a task.

    Reads manifest for readiness and project kind, then reads TASK_STATE.yaml
    for review_overlays, runtime_fail_count, and request context.

    Returns dict with 'activate' (bool) and 'reason' (str).
    """
    # Read manifest readiness
    manifest_ready = is_tooling_ready("observability_ready")

    # Read project kind from manifest
    project_kind = manifest_field("type") or manifest_section_field("project", "type") or ""

    # Read task state
    state_file = os.path.join(task_dir, "TASK_STATE.yaml") if task_dir else ""
    review_overlays = yaml_array("review_overlays", state_file) if state_file and os.path.isfile(state_file) else []
    runtime_fail_count = yaml_field("runtime_verdict_fail_count", state_file) if state_file and os.path.isfile(state_file) else "0"

    # Read request context (from REQUEST.md if available)
    context_text = ""
    if task_dir:
        request_file = os.path.join(task_dir, "REQUEST.md")
        if os.path.isfile(request_file):
            try:
                with open(request_file, "r", encoding="utf-8") as fh:
                    context_text = fh.read()
            except OSError:
                pass

    try:
        fail_count = int(runtime_fail_count) if runtime_fail_count else 0
    except (ValueError, TypeError):
        fail_count = 0

    activate, reason = should_activate_observability(
        manifest_ready=manifest_ready,
        project_kind=project_kind,
        review_overlays=review_overlays,
        runtime_fail_count=fail_count,
        context_text=context_text,
    )

    return {"activate": activate, "reason": reason}


def main():
    """CLI entry point for debugging."""
    task_dir = sys.argv[1] if len(sys.argv) > 1 else None

    if not task_dir or not os.path.isdir(task_dir):
        print(json.dumps({"activate": False, "reason": "no task directory provided"}))
        sys.exit(0)

    result = evaluate_observability_activation(task_dir)
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
