#!/usr/bin/env python3
"""PreToolUse hook: block direct Bash execution of harness-managed CLI tools.

The control plane is exposed as MCP tools. This guard blocks Bash commands that
invoke managed harness scripts directly, so the model does not need to assemble
fragile shell strings for task lifecycle or protected artifact operations.

Escape hatch: HARNESS_SKIP_MCP_GUARD=1
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_hook_input  # noqa: E402

MANAGED_SCRIPT_PATTERNS = {
    "hctl.py": {
        "subcommand_tools": {
            "start": "mcp__plugin_harness_harness__task_start",
            "context": "mcp__plugin_harness_harness__task_context",
            "update": "mcp__plugin_harness_harness__task_update_from_git_diff",
            "verify": "mcp__plugin_harness_harness__task_verify",
            "close": "mcp__plugin_harness_harness__task_close",
            "artifact": "mcp__plugin_harness_harness__write_*",
        },
        "default_tool": "mcp__plugin_harness_harness__task_context",
    },
    "verify.py": {
        "default_tool": "mcp__plugin_harness_harness__verify_run",
    },
    "write_artifact.py": {
        "subcommand_tools": {
            "critic-runtime": "mcp__plugin_harness_harness__write_critic_runtime",
            "critic-plan": "mcp__plugin_harness_harness__write_critic_plan",
            "critic-document": "mcp__plugin_harness_harness__write_critic_document",
            "handoff": "mcp__plugin_harness_harness__write_handoff",
            "doc-sync": "mcp__plugin_harness_harness__write_doc_sync",
        },
        "default_tool": "mcp__plugin_harness_harness__write_critic_plan",
    },
    "calibration_miner.py": {
        "default_tool": "mcp__plugin_harness_harness__calibration_mine",
    },
    "observability.py": {
        "subcommand_tools": {
            "detect": "mcp__plugin_harness_harness__observability_detect",
            "status": "mcp__plugin_harness_harness__observability_status",
            "hint": "mcp__plugin_harness_harness__observability_hint",
            "policy": "mcp__plugin_harness_harness__observability_policy",
        },
        "default_tool": "mcp__plugin_harness_harness__observability_status",
    },
}

SCRIPT_REGEXES = {
    name: re.compile(rf"(^|[\s'\"&;|()])(?:[^\s'\"&;|()]+/)?{re.escape(name)}(?=$|[\s'\"&;|()])")
    for name in MANAGED_SCRIPT_PATTERNS
}


def _extract_command() -> str:
    hook_data = read_hook_input()
    if not hook_data:
        return ""
    try:
        data = json.loads(hook_data)
    except (json.JSONDecodeError, TypeError):
        return ""
    tool_name = data.get("tool_name", data.get("tool", ""))
    if tool_name != "Bash":
        return ""
    tool_input = data.get("tool_input", data.get("input", {}))
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command", "")
    return command if isinstance(command, str) else ""


def _find_managed_script(command: str) -> str | None:
    for script_name, pattern in SCRIPT_REGEXES.items():
        if pattern.search(command):
            return script_name
    return None


def _infer_tool(script_name: str, command: str) -> str:
    meta = MANAGED_SCRIPT_PATTERNS.get(script_name, {})
    subcommand_tools = meta.get("subcommand_tools", {})
    default_tool = meta.get("default_tool", "mcp__plugin_harness_harness__task_context")
    if not subcommand_tools:
        return default_tool

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for index, token in enumerate(tokens):
        token_basename = os.path.basename(token)
        if token_basename == script_name:
            for candidate in tokens[index + 1 :]:
                if candidate.startswith("-"):
                    continue
                return subcommand_tools.get(candidate, default_tool)
    return default_tool


def _message(script_name: str, tool_name: str, command: str) -> str:
    return (
        "BLOCKED: harness-managed control-plane CLI calls must use MCP tools, not Bash.\n"
        f"Detected direct invocation of {script_name}.\n"
        f"Use {tool_name} instead.\n"
        f"Command: {command}"
    )


def main() -> int:
    if os.environ.get("HARNESS_SKIP_MCP_GUARD"):
        return 0

    command = _extract_command()
    if not command:
        return 0

    script_name = _find_managed_script(command)
    if not script_name:
        return 0

    tool_name = _infer_tool(script_name, command)
    print(_message(script_name, tool_name, command), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
