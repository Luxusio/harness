#!/usr/bin/env python3
"""Check running observability stack status for the harness plugin."""
import json
import subprocess
import sys
import urllib.request
import urllib.error


ENDPOINTS = {
    "grafana": "http://localhost:3100/api/health",
    "prometheus": "http://localhost:9090/-/healthy",
    "loki": "http://localhost:3101/ready",
    "tempo": "http://localhost:3200/ready",
    "otel-collector": "http://localhost:8889/metrics",
}

CONTAINERS = ["otel-collector", "prometheus", "loki", "tempo", "grafana"]


def check_docker_containers():
    """Return dict of container_name -> running bool."""
    running = {}
    try:
        out = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        active = set(out.strip().splitlines())
        for name in CONTAINERS:
            running[name] = any(name in n for n in active)
    except Exception:
        for name in CONTAINERS:
            running[name] = False
    return running


def check_endpoint(url):
    """Return True if endpoint responds with 2xx."""
    try:
        req = urllib.request.urlopen(url, timeout=3)
        return req.status < 300
    except Exception:
        return False


def status():
    result = {
        "stack_running": False,
        "containers": {},
        "endpoints": {},
        "signals": [],
        "summary": "",
    }

    result["containers"] = check_docker_containers()

    for svc, url in ENDPOINTS.items():
        result["endpoints"][svc] = check_endpoint(url)

    running_count = sum(1 for v in result["containers"].values() if v)
    healthy_count = sum(1 for v in result["endpoints"].values() if v)

    result["stack_running"] = running_count >= 3

    if result["stack_running"]:
        result["signals"].append("Observability stack is UP ({}/{} containers running)".format(
            running_count, len(CONTAINERS)
        ))
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

    result["summary"] = "{} containers running, {} endpoints healthy".format(
        running_count, healthy_count
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        status()
    except Exception as e:
        print(json.dumps({"stack_running": False, "error": str(e)}))
    sys.exit(0)
