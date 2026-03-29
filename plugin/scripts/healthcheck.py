#!/usr/bin/env python3
"""Service health probes.
Reads healthcheck_command from .claude/harness/manifest.yaml
Falls back to project-local scripts/harness/healthcheck.sh if it exists.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_array, yaml_field, yaml_array,
                  is_doc_path, find_tasks_touching_path,
                  find_tasks_with_verification_targets, manifest_field,
                  is_profile_enabled, TASK_DIR, MANIFEST, now_iso)

import subprocess
import re
import time

print("=== HEALTH CHECKS ===")

# Priority 1: project-local override
if os.path.isfile("scripts/harness/healthcheck.sh") and os.access("scripts/harness/healthcheck.sh", os.X_OK):
    os.execvp("scripts/harness/healthcheck.sh", ["scripts/harness/healthcheck.sh"])

# Priority 2: manifest healthcheck_command
hc_cmd = manifest_field("healthcheck_command")
if hc_cmd:
    print(f"Running: {hc_cmd}")
    start_ms = int(time.time() * 1000)
    result = subprocess.run(hc_cmd, shell=True, capture_output=True, text=True)
    end_ms = int(time.time() * 1000)
    elapsed = f"{end_ms - start_ms}ms"
    output = result.stdout + result.stderr
    exit_code = result.returncode
    print(output, end="")
    # Extract endpoint URL from command (regex for http(s)://...)
    m = re.search(r'https?://[^ ]+', hc_cmd)
    endpoint = m.group(0) if m else "custom"
    if exit_code == 0:
        print(f"[EVIDENCE] healthcheck: PASS {endpoint} exit=0 time={elapsed}")
    else:
        last_line = output.strip().splitlines()[-1] if output.strip() else ""
        print(f"[EVIDENCE] healthcheck: FAIL {endpoint} exit={exit_code} time={elapsed} — {last_line}")
    sys.exit(exit_code)

print("SKIP: no health checks configured")
print("[EVIDENCE] healthcheck: PASS — skipped (none configured)")
sys.exit(0)
