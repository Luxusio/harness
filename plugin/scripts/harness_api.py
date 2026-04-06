#!/usr/bin/env python3
"""Importable helpers for hot harness control-plane paths.

The CLI remains the stable public entrypoint, but MCP latency-sensitive reads can
reuse the same logic in-process to avoid spawning a fresh Python interpreter for
simple task-context fetches.
"""

from __future__ import annotations

import os

from _lib import emit_compact_context, reconcile_agent_run_counts, sync_team_status
from failure_memory import write_failure_case_snapshot


def get_task_context(
    task_dir: str,
    *,
    team_worker: str | None = None,
    agent_name: str | None = None,
) -> dict:
    """Return the compact machine-readable task context.

    This intentionally mirrors the side effects of ``hctl context`` so MCP and
    CLI callers stay in lockstep:
    - refresh the failure-case sidecar when possible
    - refresh artifact-derived team status when possible
    - build the compact context payload with the same personalization options
    """
    if not task_dir or not os.path.isdir(task_dir):
        raise FileNotFoundError(f"task dir not found: {task_dir}")

    try:
        write_failure_case_snapshot(task_dir)
    except Exception:
        pass

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

    reconciliation = {"reconciled": [], "skipped": []}
    try:
        reconciliation = reconcile_agent_run_counts(task_dir, apply=True)
    except Exception:
        reconciliation = {"reconciled": [], "skipped": []}

    context = emit_compact_context(
        task_dir,
        raw_agent_name=agent_name,
        explicit_worker=team_worker,
    )
    if reconciliation.get("reconciled"):
        context["agent_run_reconciliation"] = reconciliation
    return context
