#!/usr/bin/env python3
"""Project-specific smoke tests.
Reads smoke_command from doc/harness/manifest.yaml
Falls back to project-local scripts/harness/smoke.sh if it exists.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_array, yaml_field, yaml_array,
                  is_doc_path, find_tasks_touching_path,
                  find_tasks_with_verification_targets, manifest_field,
                  is_profile_enabled, TASK_DIR, MANIFEST, now_iso)

import subprocess

print("=== SMOKE TESTS ===")

# Priority 1: project-local override
if os.path.isfile("scripts/harness/smoke.sh") and os.access("scripts/harness/smoke.sh", os.X_OK):
    os.execvp("scripts/harness/smoke.sh", ["scripts/harness/smoke.sh"])

# Priority 2: manifest smoke_command
smoke_cmd = manifest_field("smoke_command")
if smoke_cmd:
    print(f"Running: {smoke_cmd}")
    result = subprocess.run(smoke_cmd, shell=True, capture_output=True, text=True)
    output = result.stdout + result.stderr
    exit_code = result.returncode
    tail_lines = output.strip().splitlines()[-20:] if output.strip() else []
    print(output, end="")
    if exit_code == 0:
        last_line = tail_lines[-1] if tail_lines else ""
        print(f"[EVIDENCE] smoke: PASS — exit 0 — last output: {last_line}")
    else:
        last_3 = " ".join(tail_lines[-3:]) if tail_lines else ""
        print(f"[EVIDENCE] smoke: FAIL — exit {exit_code} — last output: {last_3}")
    sys.exit(exit_code)

print("SKIP: no smoke tests configured")
print("Add smoke_command to doc/harness/manifest.yaml or create scripts/harness/smoke.sh")
print("[EVIDENCE] smoke: FAIL — no smoke tests configured")
sys.exit(1)
