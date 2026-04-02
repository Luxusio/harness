#!/usr/bin/env python3
"""Unified observability helper.

Subcommands:
  detect   - check whether the repo/environment is ready for observability
  status   - inspect whether the local stack is running
  hint     - print runtime investigation hints
  policy   - evaluate task-local overlay activation policy
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _lib import (
    MANIFEST,
    is_profile_enabled,
    is_tooling_ready,
    manifest_field,
    manifest_path_field,
    should_activate_observability,
    yaml_array,
    yaml_field,
)

ENDPOINTS = {
    "grafana": "http://localhost:3100/api/health",
    "prometheus": "http://localhost:9090/-/healthy",
    "loki": "http://localhost:3101/ready",
    "tempo": "http://localhost:3200/ready",
    "otel-collector": "http://localhost:8889/metrics",
}

CONTAINERS = ["otel-collector", "prometheus", "loki", "tempo", "grafana"]


def detect() -> dict:
    result = {
        "observability_ready": False,
        "docker_available": False,
        "compose_available": False,
        "project_suitable": False,
        "existing_setup": False,
        "reason": "",
        "details": [],
    }

    if shutil.which("docker"):
        result["docker_available"] = True
        result["details"].append("Docker binary found")
    else:
        result["reason"] = "Docker not available"
        result["details"].append("Docker binary not found on PATH")
        return result

    if shutil.which("docker-compose") or shutil.which("docker"):
        result["compose_available"] = True

    manifest = "doc/harness/manifest.yaml"
    if os.path.isfile(manifest):
        project_kind = manifest_path_field("project_meta.shape") or manifest_field("type")
        suitable_kinds = ["web", "api", "fullstack", "web_frontend", "fullstack_web", "service", "worker"]
        kind_lower = (project_kind or "").lower().replace("-", "_").replace(" ", "_")
        if any(kind.replace("-", "_") in kind_lower for kind in suitable_kinds):
            result["project_suitable"] = True
            result["details"].append(f"Project kind suitable for observability ({project_kind})")
        else:
            result["reason"] = "Project type not suitable (library/cli/unknown)"
            result["details"].append(f"Project kind not web/api/fullstack ({project_kind or 'unknown'})")

    for compose_file in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        if not os.path.isfile(compose_file):
            continue
        try:
            with open(compose_file, encoding="utf-8") as handle:
                content = handle.read().lower()
            if any(signal in content for signal in ["grafana", "prometheus", "jaeger", "zipkin", "otel"]):
                result["existing_setup"] = True
                result["details"].append(
                    f"Existing observability setup detected in {compose_file}"
                )
                break
        except Exception:
            pass

    result["observability_ready"] = (
        result["docker_available"] and result["compose_available"] and result["project_suitable"]
    )
    if result["observability_ready"]:
        result["reason"] = "Ready for observability scaffold"
    return result


def check_docker_containers() -> dict[str, bool]:
    running = {}
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        active = set(out.strip().splitlines())
        for name in CONTAINERS:
            running[name] = any(name in item for item in active)
    except Exception:
        for name in CONTAINERS:
            running[name] = False
    return running


def check_endpoint(url: str) -> bool:
    try:
        request = urllib.request.urlopen(url, timeout=3)
        return request.status < 300
    except Exception:
        return False


def status() -> dict:
    result = {
        "stack_running": False,
        "containers": check_docker_containers(),
        "endpoints": {},
        "signals": [],
        "summary": "",
    }

    for service, url in ENDPOINTS.items():
        result["endpoints"][service] = check_endpoint(url)

    running_count = sum(1 for value in result["containers"].values() if value)
    healthy_count = sum(1 for value in result["endpoints"].values() if value)
    result["stack_running"] = running_count >= 3

    if result["stack_running"]:
        result["signals"].append(
            f"Observability stack is UP ({running_count}/{len(CONTAINERS)} containers running)"
        )
        if result["endpoints"].get("grafana"):
            result["signals"].append("Grafana UI: http://localhost:3100")
        if result["endpoints"].get("prometheus"):
            result["signals"].append("Prometheus: http://localhost:9090")
    else:
        result["signals"].append("Observability stack is DOWN or not started")
        result["signals"].append(
            "Start with: docker compose -f docker-compose.yml "
            "-f docker-compose.observability.yml --profile observability up -d"
        )

    result["summary"] = f"{running_count} containers running, {healthy_count} endpoints healthy"
    return result


def is_task_overlay_active(task_dir: str | None) -> bool:
    if not task_dir:
        return False
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False
    overlays = yaml_array("review_overlays", state_file)
    return "observability" in overlays


def hints_for_context(context: str | None) -> list[str]:
    ctx = (context or "").lower()
    hint_map = {
        "error": [
            'Loki query: {job="app"} |= "error" | logfmt',
            'Prometheus: rate(app_http_requests_total{status=~"5.."}[5m])',
            "Check traces for failed requests in Tempo via Grafana Explore",
        ],
        "slow": [
            'Latency p99: histogram_quantile(0.99, rate(app_http_request_duration_seconds_bucket[5m]))',
            "Compare p50 vs p99 to identify tail latency spikes",
            "Trace slow requests: filter by duration > 1s in Tempo",
        ],
        "latency": [
            'histogram_quantile(0.95, rate(app_http_request_duration_seconds_bucket[5m]))',
            "Look for upstream dependency slowdowns in distributed traces",
            "Check DB query spans in Tempo for N+1 patterns",
        ],
        "memory": [
            "process_resident_memory_bytes metric in Prometheus",
            'Loki: {job="app"} |= "OOM" or |= "out of memory"',
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
            'Loki: {job="app"} | logfmt | rate by error type over time',
            "Prometheus: increase(app_errors_total[1h]) to find sporadic spikes",
            "Tempo: search for traces with errors in specific time windows",
        ],
        "p95": [
            'histogram_quantile(0.95, rate(app_http_request_duration_seconds_bucket[5m]))',
            "Compare p50/p95/p99 breakdown for tail latency characterization",
        ],
        "p99": [
            'histogram_quantile(0.99, rate(app_http_request_duration_seconds_bucket[5m]))',
            "Compare p50/p95/p99 breakdown for tail latency characterization",
        ],
    }

    matched: list[str] = []
    for keyword, hints in hint_map.items():
        if keyword in ctx:
            matched.extend(hints)
    if not matched:
        matched = [
            "Grafana UI: http://localhost:3100 (App Overview dashboard)",
            'Logs: Loki — {job="app"}',
            "Metrics: Prometheus — http://localhost:9090",
            "Traces: Tempo via Grafana Explore (datasource: Tempo)",
        ]
    return matched


def hint(context: str | None = None, task_dir: str | None = None, force_task_overlay: bool = False) -> dict:
    enabled = is_profile_enabled("observability_enabled")
    task_overlay = False
    if not enabled and (force_task_overlay or task_dir):
        task_overlay = is_task_overlay_active(task_dir)
        if task_overlay:
            enabled = True
    if not enabled:
        return {"enabled": False, "hints": []}
    return {
        "enabled": True,
        "context": context or "general",
        "hints": hints_for_context(context),
        "grafana_url": "http://localhost:3100",
        "dashboard": "App Overview",
        "manifest_loaded": os.path.isfile(MANIFEST),
        "activation_mode": "task_overlay" if task_overlay else "global_profile",
    }


def evaluate_policy(task_dir: str | None) -> dict:
    if not task_dir or not os.path.isdir(task_dir):
        return {"activate": False, "reason": "no task directory provided"}

    manifest_ready = is_tooling_ready("observability_ready")
    project_kind = manifest_path_field("project_meta.shape") or manifest_field("type") or ""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    review_overlays = yaml_array("review_overlays", state_file) if os.path.isfile(state_file) else []
    runtime_fail_count = yaml_field("runtime_verdict_fail_count", state_file) if os.path.isfile(state_file) else "0"

    context_text = ""
    request_file = os.path.join(task_dir, "REQUEST.md")
    if os.path.isfile(request_file):
        try:
            with open(request_file, "r", encoding="utf-8") as handle:
                context_text = handle.read()
        except OSError:
            pass

    try:
        fail_count = int(runtime_fail_count) if runtime_fail_count else 0
    except (TypeError, ValueError):
        fail_count = 0

    activate, reason = should_activate_observability(
        manifest_ready=manifest_ready,
        project_kind=project_kind,
        review_overlays=review_overlays,
        runtime_fail_count=fail_count,
        context_text=context_text,
    )
    return {"activate": activate, "reason": reason}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness observability helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("detect", help="check observability readiness")
    subparsers.add_parser("status", help="check running stack status")

    hint_parser = subparsers.add_parser("hint", help="print observability hints")
    hint_parser.add_argument("context", nargs="?", default=None)
    hint_parser.add_argument("--task-dir", default=None)
    hint_parser.add_argument("--force-task-overlay", action="store_true")

    policy_parser = subparsers.add_parser("policy", help="evaluate activation policy")
    policy_parser.add_argument("task_dir", nargs="?", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "detect":
        print(json.dumps(detect(), indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if args.command == "hint":
        print(json.dumps(hint(args.context, task_dir=args.task_dir, force_task_overlay=args.force_task_overlay), indent=2))
        return 0
    if args.command == "policy":
        print(json.dumps(evaluate_policy(args.task_dir), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"observability_ready": False, "error": str(exc)}))
        sys.exit(0)
