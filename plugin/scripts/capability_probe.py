#!/usr/bin/env python3
"""Capability probe — detect delegation availability.

Determines whether subagent delegation is available in the current environment.
Used by harness to decide workflow_mode (compliant vs degraded_capability).

Detection priority:
  1. manifest capabilities.delegation_mode (explicit override)
  2. CLAUDE_AGENT_NAME presence (agent delegation signal)
  3. Environment signals (known CI/CD, container environments)
  4. Default: unknown

Usage:
    from capability_probe import probe_delegation_capability
    status = probe_delegation_capability()  # "available" | "unavailable" | "unknown"
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import MANIFEST, manifest_section_field


def probe_delegation_capability():
    """Probe whether subagent delegation is available.

    Returns: "available" | "unavailable" | "unknown"
    """
    # 1. Explicit manifest override
    manifest_mode = manifest_section_field("capabilities", "delegation_mode")
    if manifest_mode in ("available", "unavailable"):
        return manifest_mode

    # 2. Agent name presence — if we're running as a named agent, delegation likely works
    agent_name = os.environ.get("CLAUDE_AGENT_NAME", "")
    if agent_name and ":" in agent_name:
        # Running inside a named agent context — delegation infrastructure exists
        return "available"

    # 3. Known environment signals
    # CI/CD environments typically don't support interactive delegation
    ci_signals = [
        "CI", "CONTINUOUS_INTEGRATION", "GITHUB_ACTIONS",
        "GITLAB_CI", "JENKINS_URL", "CIRCLECI", "BUILDKITE",
    ]
    for sig in ci_signals:
        if os.environ.get(sig):
            return "unavailable"

    # 4. Default
    return "unknown"


def update_task_capability(task_dir, capability_status=None):
    """Update capability_delegation field in TASK_STATE.yaml.

    If capability_status is None, probes automatically.
    Returns the status string.
    """
    import re
    if capability_status is None:
        capability_status = probe_delegation_capability()

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return capability_status

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            content = f.read()

        if re.search(r"^capability_delegation:", content, re.MULTILINE):
            content = re.sub(
                r"^capability_delegation:.*",
                f"capability_delegation: {capability_status}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content = content.rstrip("\n") + f"\ncapability_delegation: {capability_status}\n"

        with open(state_file, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass

    return capability_status


if __name__ == "__main__":
    result = probe_delegation_capability()
    print(f"delegation_capability: {result}")
