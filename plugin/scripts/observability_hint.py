#!/usr/bin/env python3
"""Provide observability hints during runtime investigation for the harness plugin."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import MANIFEST


def load_state():
    state_path = ".claude/harness/state.json"
    if not os.path.isfile(state_path):
        return {}
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception:
        return {}


def is_observability_enabled():
    """Check if observability is enabled in harness state."""
    state = load_state()
    return state.get("observability_enabled", False)


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


def run(context=None):
    if not is_observability_enabled():
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
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    ctx = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        run(ctx)
    except Exception as e:
        print(json.dumps({"enabled": False, "error": str(e)}))
    sys.exit(0)
