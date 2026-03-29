#!/usr/bin/env python3
"""Detect observability feasibility for the harness plugin."""
import json
import os
import shutil
import sys

def detect():
    result = {
        "observability_ready": False,
        "docker_available": False,
        "compose_available": False,
        "project_suitable": False,
        "existing_setup": False,
        "reason": "",
        "details": []
    }

    # Check Docker
    if shutil.which("docker"):
        result["docker_available"] = True
        result["details"].append("Docker binary found")
    else:
        result["reason"] = "Docker not available"
        result["details"].append("Docker binary not found on PATH")
        print(json.dumps(result, indent=2))
        return

    # Check docker compose
    compose = shutil.which("docker-compose") or shutil.which("docker")
    if compose:
        result["compose_available"] = True

    # Check project suitability (web/api/fullstack → suitable)
    manifest = ".claude/harness/manifest.yaml"
    if os.path.isfile(manifest):
        with open(manifest) as f:
            content = f.read()
        suitable_kinds = ["web", "api", "fullstack", "web_frontend", "fullstack_web"]
        for kind in suitable_kinds:
            if "shape: {}".format(kind) in content or "kind: {}".format(kind) in content:
                result["project_suitable"] = True
                result["details"].append("Project kind suitable for observability")
                break
        if not result["project_suitable"]:
            result["reason"] = "Project type not suitable (library/cli/unknown)"
            result["details"].append("Project kind not web/api/fullstack")

    # Check for existing observability setup
    compose_files = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
    for cf in compose_files:
        if os.path.isfile(cf):
            try:
                with open(cf) as f:
                    content = f.read().lower()
                if any(s in content for s in ["grafana", "prometheus", "jaeger", "zipkin", "otel"]):
                    result["existing_setup"] = True
                    result["details"].append("Existing observability setup detected in {}".format(cf))
                    break
            except Exception:
                pass

    # Final readiness
    result["observability_ready"] = result["docker_available"] and result["compose_available"] and result["project_suitable"]
    if result["observability_ready"]:
        result["reason"] = "Ready for observability scaffold"

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        detect()
    except Exception as e:
        print(json.dumps({"observability_ready": False, "error": str(e)}))
    sys.exit(0)
