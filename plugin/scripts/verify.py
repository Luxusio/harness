#!/usr/bin/env python3
"""Main verification entry point — runs smoke + healthcheck in sequence.
Lives in plugin, referenced via ${CLAUDE_PLUGIN_ROOT}/scripts/verify.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_array, yaml_field, yaml_array,
                  is_doc_path, find_tasks_touching_path,
                  find_tasks_with_verification_targets, manifest_field,
                  is_profile_enabled, TASK_DIR, MANIFEST, now_iso)

import subprocess
from datetime import datetime, timezone

script_dir = os.path.dirname(os.path.abspath(__file__))

failures = 0
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

print("=== HARNESS VERIFY ===")
print(f"[EVIDENCE] verify: started at {timestamp}")

print("--- Running smoke tests ---")
smoke_result = subprocess.run(
    ["python3", os.path.join(script_dir, "smoke.py")],
    capture_output=True, text=True
)
smoke_output = smoke_result.stdout + smoke_result.stderr
smoke_exit = smoke_result.returncode
if smoke_exit == 0:
    print("smoke: PASS")
    last_line = smoke_output.strip().splitlines()[-1] if smoke_output.strip() else ""
    print(f"[EVIDENCE] smoke: PASS — {last_line}")
else:
    print("smoke: FAIL")
    last_lines = " ".join(smoke_output.strip().splitlines()[-3:]) if smoke_output.strip() else ""
    print(f"[EVIDENCE] smoke: FAIL — exit {smoke_exit} — {last_lines}")
    failures += 1

print("--- Running health checks ---")
hc_result = subprocess.run(
    ["python3", os.path.join(script_dir, "healthcheck.py")],
    capture_output=True, text=True
)
hc_output = hc_result.stdout + hc_result.stderr
hc_exit = hc_result.returncode
if hc_exit == 0:
    print("healthcheck: PASS")
    last_line = hc_output.strip().splitlines()[-1] if hc_output.strip() else ""
    print(f"[EVIDENCE] healthcheck: PASS — {last_line}")
else:
    print("healthcheck: FAIL")
    last_lines = " ".join(hc_output.strip().splitlines()[-3:]) if hc_output.strip() else ""
    print(f"[EVIDENCE] healthcheck: FAIL — exit {hc_exit} — {last_lines}")
    failures += 1

print("")

# --- Observability status check ---
if os.path.isfile(MANIFEST):
    obs_enabled = False
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            for line in fh:
                if "observability_enabled:" in line and "true" in line:
                    obs_enabled = True
                    break
    except OSError:
        pass
    if obs_enabled:
        print("--- Running observability status check ---")
        obs_result = subprocess.run(
            ["python3", os.path.join(script_dir, "observability_status.py")],
            capture_output=True, text=True
        )
        obs_output = obs_result.stdout + obs_result.stderr
        obs_exit = obs_result.returncode
        if obs_exit == 0:
            print("observability: PASS")
            last_line = obs_output.strip().splitlines()[-1] if obs_output.strip() else ""
            print(f"[EVIDENCE] observability: PASS — {last_line}")
        else:
            print("observability: FAIL")
            last_lines = " ".join(obs_output.strip().splitlines()[-3:]) if obs_output.strip() else ""
            print(f"[EVIDENCE] observability: FAIL — exit {obs_exit} — {last_lines}")
            failures += 1

end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
if failures > 0:
    print(f"RESULT: {failures} check(s) failed")
    print(f"[EVIDENCE] verify: FAIL — {failures} check(s) failed at {end_timestamp}")
    sys.exit(1)
else:
    print("RESULT: all checks passed")
    print(f"[EVIDENCE] verify: PASS — all checks passed at {end_timestamp}")
    sys.exit(0)
