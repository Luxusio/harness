#!/usr/bin/env python3
"""Provide observability hints during runtime investigation for the harness plugin.

Supports two modes:
  1. Global profile mode: checks manifest profiles.observability_enabled (original behavior)
  2. Task overlay mode: activated when --task-overlay flag is passed and the task's
     review_overlays contains 'observability', even if the global profile is off.

This allows task-local observability activation without changing repo-wide defaults.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import MANIFEST, is_profile_enabled, yaml_array


def is_observability_enabled():
    """Check if observability is enabled in manifest profiles."""
    return is_profile_enabled("observability_enabled")


def is_task_overlay_active(task_dir):
    """Check if observability overlay is active for a specific task.

    Returns True if the task's review_overlays contains 'observability'.
    This allows task-local activation even when the global profile is off.
    """
    if not task_dir:
        return False
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False
    overlays = yaml_array("review_overlays", state_file)
    return "observability" in overlays


def hints_for_context(context):
    """Return hints relevant to the given context keyword."""
    ctx = (context or "").lower()

    hint_map = {
        "error": [
            "Loki query: {job=\"app\"} |= \"error\" | logfmt",
            "Prometheus: rate(app_http_requests_total{status=~\"5..\"}[5m])",
            "Check traces for failed requests in Tempo via Grafana Explore",
        ],
        "slow": [
            "Latency p99: histogram_quantile(0.99, rate(app_http_request_duration_seconds_bucket[5m]))",
            "Compare p50 vs p99 to identify tail latency spikes",
            "Trace slow requests: filter by duration > 1s in Tempo",
        ],
        "latency": [
            "histogram_quantile(0.95, rate(app_http_request_duration_seconds_bucket[5m]))",
            "Look for upstream dependency slowdowns in distributed traces",
            "Check DB query spans in Tempo for N+1 patterns",
        ],
        "memory": [
            "process_resident_memory_bytes metric in Prometheus",
            "Loki: {job=\"app\"} |= \"OOM\" or |= \"out of memory\"",
            "Check container memory in Docker stats: docker stats --no-stream",
        ],
        "pool": [
            "Look for connection_pool_exhausted in app logs via Loki",
            "Prometheus: app_db_pool_size vs app_db_pool_checked_out",
            "Trace: find requests waiting on pool acquisition",
        ],
        "deploy": [
            "Compare request rate before/after deploy in Grafana",
            "Loki: filter by deployment timestamp to isolate new errors",
            "Use Tempo to compare trace shapes between old and new version",
        ],
        "intermittent": [
            "Loki: {job=\"app\"} | logfmt | rate by error type over time",
            "Prometheus: increase(app_errors_total[1h]) to find sporadic spikes",
            "Tempo: search for traces with errors in specific time windows",
        ],
        "p95": [
            "histogram_quantile(0.95, rate(app_http_request_duration_seconds_bucket[5m]))",
            "Compare p50/p95/p99 breakdown for tail latency characterization",
        ],
        "p99": [
            "histogram_quantile(0.99, rate(app_http_request_duration_seconds_bucket[5m]))",
            "Compare p50/p95/p99 breakdown for tail latency characterization",
        ],
    }

    matched = []
    for keyword, hints in hint_map.items():
        if keyword in ctx:
            matched.extend(hints)

    if not matched:
        matched = [
            "Grafana UI: http://localhost:3100 (App Overview dashboard)",
            "Logs: Loki — {job=\"app\"}",
            "Metrics: Prometheus — http://localhost:9090",
            "Traces: Tempo via Grafana Explore (datasource: Tempo)",
        ]

    return matched


def run(context=None, task_dir=None, force_task_overlay=False):
    # Determine if enabled: global profile OR task overlay
    enabled = is_observability_enabled()
    task_overlay = False

    if not enabled and (force_task_overlay or task_dir):
        task_overlay = is_task_overlay_active(task_dir)
        if task_overlay:
            enabled = True

    if not enabled:
        # Silent when disabled
        print(json.dumps({"enabled": False, "hints": []}))
        return

    hints = hints_for_context(context)

    result = {
        "enabled": True,
        "context": context or "general",
        "hints": hints,
        "grafana_url": "http://localhost:3100",
        "dashboard": "App Overview",
        "manifest_loaded": os.path.isfile(MANIFEST),
        "activation_mode": "task_overlay" if task_overlay else "global_profile",
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Observability hints")
    parser.add_argument("context", nargs="?", default=None, help="Context keyword")
    parser.add_argument("--task-dir", default=None, help="Task directory for overlay check")
    parser.add_argument("--force-task-overlay", action="store_true",
                        help="Force task overlay mode even if global profile is off")
    args = parser.parse_args()

    try:
        run(context=args.context, task_dir=args.task_dir,
            force_task_overlay=args.force_task_overlay)
    except Exception as e:
        print(json.dumps({"enabled": False, "error": str(e)}))
    sys.exit(0)
