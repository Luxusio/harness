#!/usr/bin/env python3
"""Capability probe — detect delegation availability and team readiness.

Default CLI behavior remains unchanged:
  python3 capability_probe.py
    -> prints `delegation_capability: <status>`

Additional team readiness mode:
  python3 capability_probe.py team
  python3 capability_probe.py team --quiet
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import manifest_section_field, native_agent_teams_runtime_probe, omc_runtime_probe, set_task_state_field


def probe_delegation_capability():
    """Probe whether subagent delegation is available.

    Returns: "available" | "unavailable" | "unknown"
    """
    manifest_mode = manifest_section_field("capabilities", "delegation_mode")
    if manifest_mode in ("available", "unavailable"):
        return manifest_mode

    agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")
    if agent_name and ":" in agent_name:
        return "available"

    ci_signals = [
        "CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS",
        "GITLAB_CI", "JENKINS_URL", "CIRCLECI", "BUILDKITE",
    ]
    for signal in ci_signals:
        if os.environ.get(signal):
            return "unavailable"

    return "unknown"


def update_task_capability(task_dir, capability_status=None):
    """Update capability_delegation field in TASK_STATE.yaml."""
    if capability_status is None:
        capability_status = probe_delegation_capability()

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return capability_status

    try:
        set_task_state_field(task_dir, "capability_delegation", capability_status)
    except OSError:
        pass

    return capability_status


def check_native_ready():
    """Check if native Claude Code teams are available in the current session."""
    details = native_agent_teams_runtime_probe()

    tmux_available = False
    try:
        result = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=3)
        tmux_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    details["tmux_available"] = tmux_available

    ready = bool(details.get("ready"))
    return ready, details


def check_omc_ready():
    """Check if oh-my-claudecode teams are available in the current session."""
    details = omc_runtime_probe()
    ready = bool(details.get("ready"))
    return ready, details


def probe_team_readiness():
    native_ready, native_details = check_native_ready()
    omc_ready, omc_details = check_omc_ready()
    return {
        "native_ready": native_ready,
        "omc_ready": omc_ready,
        "any_ready": native_ready or omc_ready,
        "details": {
            "native": native_details,
            "omc": omc_details,
        },
    }


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    if argv and argv[0] == "team":
        quiet = "--quiet" in argv[1:]
        result = probe_team_readiness()
        if quiet:
            return 0 if result["any_ready"] else 1
        print(json.dumps(result, indent=2))
        return 0

    result = probe_delegation_capability()
    print(f"delegation_capability: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
