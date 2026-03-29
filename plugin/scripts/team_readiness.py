#!/usr/bin/env python3
"""Team readiness probe for harness.

Detects native Claude Code team support and OMC team support.
Can be imported or run standalone.

Usage:
    python3 team_readiness.py              # prints JSON result
    python3 team_readiness.py --quiet      # exit 0 if any team ready, 1 if none
"""

import json
import os
import subprocess
import sys


def check_native_ready():
    """Check if native Claude Code teams are available."""
    details = {}

    # Check CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS env var
    teams_env = os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "")
    details["teams_env_set"] = teams_env == "1"

    # Check claude CLI version
    claude_version = ""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            claude_version = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    details["claude_version"] = claude_version
    details["claude_available"] = bool(claude_version)

    # Check tmux availability (optional, for terminal-based teams)
    tmux_available = False
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True, text=True, timeout=3
        )
        tmux_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    details["tmux_available"] = tmux_available

    # Native ready if teams env is set and claude is available
    ready = details["teams_env_set"] and details["claude_available"]
    return ready, details


def check_omc_ready():
    """Check if oh-my-claudecode teams are available."""
    details = {}

    # Check omc command
    omc_available = False
    try:
        result = subprocess.run(
            ["command", "-v", "omc"],
            capture_output=True, text=True, timeout=3,
            shell=True
        )
        omc_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    if not omc_available:
        # Try which as fallback
        try:
            result = subprocess.run(
                ["which", "omc"],
                capture_output=True, text=True, timeout=3
            )
            omc_available = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    details["omc_available"] = omc_available

    # Check .omc/ directory
    omc_dir_exists = os.path.isdir(".omc") or os.path.isdir(
        os.path.join(os.path.expanduser("~"), ".omc")
    )
    details["omc_dir_exists"] = omc_dir_exists

    ready = omc_available
    return ready, details


def probe():
    """Run full readiness probe. Returns dict."""
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


if __name__ == "__main__":
    result = probe()

    if "--quiet" in sys.argv:
        sys.exit(0 if result["any_ready"] else 1)

    print(json.dumps(result, indent=2))
