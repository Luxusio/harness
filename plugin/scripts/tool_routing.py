#!/usr/bin/env python3
"""PostToolUse hook: provide routing hints when tools fail."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, manifest_field, is_tooling_ready,
                  is_profile_enabled, exit_if_unmanaged_repo)

ROUTING_STATE_FILE = "doc/harness/.routing-state.json"

def read_manifest():
    """Read manifest for known commands and tooling."""
    manifest_path = "doc/harness/manifest.yaml"
    data = {}
    if not os.path.isfile(manifest_path):
        return data
    try:
        with open(manifest_path) as f:
            content = f.read()
        # Extract known commands
        for field in ["dev_command", "test_command", "build_command", "smoke_command", "healthcheck_command"]:
            for line in content.split("\n"):
                if "{}:".format(field) in line:
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val and not val.startswith("{{"):
                        data[field] = val
        # Extract tooling flags using _lib helpers
        data["ast_grep_enabled"] = is_profile_enabled("ast_grep_enabled")
        data["symbol_lane_enabled"] = is_profile_enabled("symbol_lane_enabled")
        data["ast_grep_ready"] = is_tooling_ready("ast_grep_ready")
        data["lsp_ready"] = is_tooling_ready("lsp_ready") or is_tooling_ready("cclsp_ready")
    except Exception:
        pass
    return data

def load_routing_state():
    """Load routing state (failure counters)."""
    try:
        if os.path.isfile(ROUTING_STATE_FILE):
            with open(ROUTING_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"grep_failures": 0, "command_failures": 0, "path_failures": 0}

def save_routing_state(state):
    """Save routing state."""
    try:
        os.makedirs(os.path.dirname(ROUTING_STATE_FILE), exist_ok=True)
        with open(ROUTING_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass

def get_hint(hook_input, manifest, state):
    """Generate a routing hint based on tool failure context."""
    if not hook_input:
        return None

    try:
        data = json.loads(hook_input)
    except (json.JSONDecodeError, TypeError):
        return None

    tool_name = data.get("tool_name", data.get("tool", ""))
    exit_code = data.get("exit_code", data.get("exitCode", 0))
    command = data.get("command", data.get("input", ""))
    error = data.get("error", data.get("stderr", ""))

    # Only process failures
    if not exit_code and not error:
        return None

    hint = None

    # 1. Wrong dev/build/test command
    if isinstance(command, str):
        wrong_commands = {
            "npm start": "dev_command",
            "npm run start": "dev_command",
            "yarn start": "dev_command",
            "npm run build": "build_command",
            "yarn build": "build_command",
            "npm test": "test_command",
            "yarn test": "test_command",
            "pytest": "test_command",
            "python -m pytest": "test_command",
        }
        for wrong_cmd, field in wrong_commands.items():
            if wrong_cmd in command and field in manifest:
                state["command_failures"] = state.get("command_failures", 0) + 1
                hint = "Command failed. Manifest knows: {} = '{}'".format(field, manifest[field])
                break

    # 2. Broad grep repeated
    if isinstance(command, str) and ("grep" in command or "rg " in command):
        if exit_code:
            state["grep_failures"] = state.get("grep_failures", 0) + 1
            if state["grep_failures"] >= 3:
                suggestions = []
                if manifest.get("symbol_lane_enabled") or manifest.get("lsp_ready"):
                    suggestions.append("Try symbol lane (lsp_find_references, lsp_goto_definition)")
                if manifest.get("ast_grep_enabled") or manifest.get("ast_grep_ready"):
                    suggestions.append("Try structural search (ast-grep)")
                if suggestions:
                    hint = "Repeated grep failures. " + ". ".join(suggestions)
                    state["grep_failures"] = 0  # Reset after hint

    # 3. Non-existent script path
    if isinstance(command, str) and ("scripts/" in command or "script/" in command):
        if "No such file" in str(error) or "not found" in str(error):
            state["path_failures"] = state.get("path_failures", 0) + 1
            hint = "Script not found. Check manifest for correct paths."
            for field in ["smoke_command", "healthcheck_command", "test_command"]:
                if field in manifest:
                    hint += " Known: {} = '{}'".format(field, manifest[field])

    save_routing_state(state)
    return hint

def main():
    exit_if_unmanaged_repo()

    hook_input = read_hook_input()
    if not hook_input:
        sys.exit(0)

    manifest = read_manifest()
    state = load_routing_state()
    hint = get_hint(hook_input, manifest, state)

    if hint:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": hint[:500]
            }
        }
        print(json.dumps(output))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
