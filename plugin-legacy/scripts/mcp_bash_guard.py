#!/usr/bin/env python3
"""PreToolUse hook: block direct Bash execution of harness-managed flows.

Two classes of bypasses are rejected:
  1. Direct invocation of harness-managed CLI entrypoints (use MCP tools instead).
  2. Direct Bash file mutations that would bypass prewrite / provenance checks
     for source files, protected artifacts, or workflow control surfaces.

Escape hatch: HARNESS_SKIP_MCP_GUARD=1
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import exit_if_unmanaged_repo, read_hook_input  # noqa: E402
from prewrite_gate import (  # noqa: E402
    _is_protected_artifact,
    _is_source_file,
    _is_workflow_control_surface,
)

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
        "allowed_subcommands": {"plan"},
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

PROTECTED_ARTIFACT_TO_TOOL = {
    "CRITIC__runtime.md": "mcp__plugin_harness_harness__write_critic_runtime",
    "CRITIC__plan.md": "mcp__plugin_harness_harness__write_critic_plan",
    "CRITIC__document.md": "mcp__plugin_harness_harness__write_critic_document",
    "HANDOFF.md": "mcp__plugin_harness_harness__write_handoff",
    "DOC_SYNC.md": "mcp__plugin_harness_harness__write_doc_sync",
    "PLAN.md": "/harness:plan",
    "QA__runtime.md": "mcp__plugin_harness_harness__write_critic_runtime",
}

REDIRECT_TOKENS = {">", ">>", "1>", "1>>", "2>", "2>>"}
LAST_ARG_MUTATORS = {"cp", "mv", "install", "touch", "truncate"}


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


def _split_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _is_env_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token or ""))


INTERPRETER_PREFIXES = ("python", "python3", "pypy")
SHELL_RUNNERS = {"bash", "sh", "zsh"}
WRAPPER_COMMANDS = {"env", "command", "nohup", "time", "stdbuf", "sudo"}


def _find_invoked_script_token(tokens: list[str]) -> str | None:
    if not tokens:
        return None

    index = 0
    while index < len(tokens) and _is_env_assignment(tokens[index]):
        index += 1
    while index < len(tokens) and os.path.basename(tokens[index]) in WRAPPER_COMMANDS:
        index += 1
        if index < len(tokens) and os.path.basename(tokens[index - 1]) == "env":
            while index < len(tokens) and _is_env_assignment(tokens[index]):
                index += 1
    if index >= len(tokens):
        return None

    cmd = os.path.basename(tokens[index])
    if cmd in MANAGED_SCRIPT_PATTERNS:
        return cmd

    if cmd.startswith(INTERPRETER_PREFIXES):
        script_index = index + 1
        while script_index < len(tokens):
            token = tokens[script_index]
            if token in {"-m", "-c"}:
                return None
            if token.startswith("-"):
                script_index += 1
                continue
            basename = os.path.basename(token)
            if basename in MANAGED_SCRIPT_PATTERNS:
                return basename
            return None
    if cmd in SHELL_RUNNERS:
        script_index = index + 1
        while script_index < len(tokens):
            token = tokens[script_index]
            if token.startswith("-"):
                script_index += 1
                continue
            basename = os.path.basename(token)
            if basename in MANAGED_SCRIPT_PATTERNS:
                return basename
            return None
    return None


def _find_managed_script(command: str) -> str | None:
    for segment in _split_segments(command):
        tokens = _split_tokens(segment)
        script_name = _find_invoked_script_token(tokens)
        if script_name:
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


def _normalize_candidate_path(token: str) -> str:
    value = str(token or "").strip().strip("'").strip('"')
    if not value:
        return ""
    if value.startswith("./"):
        value = value[2:]
    cwd = os.getcwd()
    if os.path.isabs(value):
        try:
            rel = os.path.relpath(value, cwd)
        except ValueError:
            return ""
        if rel.startswith(".."):
            return ""
        value = rel
    return value.rstrip(",)")


def _classify_gated_path(path_value: str) -> str:
    if not path_value:
        return ""
    if _is_workflow_control_surface(path_value):
        return "workflow-control-surface"
    if _is_protected_artifact(path_value):
        return "protected-artifact"
    if _is_source_file(path_value):
        return "source"
    return ""


def _append_target(targets: list[dict[str, str]], token: str, method: str) -> None:
    path_value = _normalize_candidate_path(token)
    category = _classify_gated_path(path_value)
    if not category:
        return
    item = {"path": path_value, "category": category, "method": method}
    if item not in targets:
        targets.append(item)


def _split_segments(command: str) -> list[str]:
    parts = re.split(r"(?:&&|\|\||;|\n)", command)
    return [part.strip() for part in parts if part and part.strip()]


def _extract_redirect_targets(tokens: list[str], targets: list[dict[str, str]]) -> None:
    for index, token in enumerate(tokens):
        if token in REDIRECT_TOKENS and index + 1 < len(tokens):
            _append_target(targets, tokens[index + 1], "shell redirection")
            continue
        inline = re.match(r"^(?:\d*)?(>>?)(.+)$", token)
        if inline:
            candidate = inline.group(2).strip()
            if candidate and candidate not in ("&1", "&2"):
                _append_target(targets, candidate, "shell redirection")


def _last_non_option(tokens: list[str]) -> str:
    for token in reversed(tokens[1:]):
        if token.startswith("-"):
            continue
        return token
    return ""


def _extract_python_inline_targets(tokens: list[str], targets: list[dict[str, str]]) -> None:
    if "-c" not in tokens:
        return
    try:
        code = tokens[tokens.index("-c") + 1]
    except IndexError:
        return

    patterns = [
        r"open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa+]",
        r"(?:pathlib\.)?Path\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:write_text|write_bytes|open)",
        r"os\.replace\([^,]+,\s*['\"]([^'\"]+)['\"]\)",
        r"shutil\.copy(?:2)?\([^,]+,\s*['\"]([^'\"]+)['\"]\)",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, code):
            _append_target(targets, match, "python inline write")


def _extract_mutation_targets(command: str) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for segment in _split_segments(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue

        _extract_redirect_targets(tokens, targets)

        cmd = os.path.basename(tokens[0])
        if cmd == "sed" and any(tok == "-i" or tok.startswith("-i") for tok in tokens[1:]):
            _append_target(targets, _last_non_option(tokens), "sed -i")
            continue
        if cmd == "perl" and any(tok == "-pi" or tok.startswith("-pi") for tok in tokens[1:]):
            _append_target(targets, _last_non_option(tokens), "perl -pi")
            continue
        if cmd in LAST_ARG_MUTATORS:
            _append_target(targets, _last_non_option(tokens), cmd)
            continue
        if cmd == "tee":
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                _append_target(targets, token, "tee")
            continue
        if cmd.startswith("python"):
            _extract_python_inline_targets(tokens, targets)
            continue
    return targets


def _subcommand_in_allowlist(script_name: str, command: str) -> bool:
    """Return True if the command's first positional after script_name is
    in the script's allowed_subcommands set."""
    meta = MANAGED_SCRIPT_PATTERNS.get(script_name, {})
    allowed = meta.get("allowed_subcommands") or set()
    if not allowed:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        if os.path.basename(token) == script_name:
            for candidate in tokens[index + 1 :]:
                if candidate.startswith("-"):
                    continue
                return candidate in allowed
    return False


def _managed_cli_message(script_name: str, tool_name: str, command: str) -> str:
    return (
        "BLOCKED: harness-managed control-plane CLI calls must use MCP tools, not Bash.\n"
        f"Detected direct invocation of {script_name}.\n"
        f"Use {tool_name} instead.\n"
        f"Command: {command}"
    )


def _target_tool_hint(target: dict[str, str]) -> str:
    path_value = target.get("path", "")
    category = target.get("category", "")
    basename = os.path.basename(path_value)
    if category == "protected-artifact":
        return PROTECTED_ARTIFACT_TO_TOOL.get(basename, "the corresponding MCP write_* tool")
    if category == "workflow-control-surface":
        return "a maintenance task + Write/Edit (not Bash redirection)"
    return "Write/Edit via the developer flow"


def _mutation_message(target: dict[str, str], command: str) -> str:
    path_value = target.get("path", "")
    category = target.get("category", "file")
    method = target.get("method", "bash mutation")
    return (
        "BLOCKED: direct Bash file mutation bypasses harness provenance and prewrite enforcement.\n"
        f"Detected {category} mutation via {method}.\n"
        f"Target: {path_value}\n"
        f"Use {_target_tool_hint(target)} instead.\n"
        f"Command: {command}"
    )


def main() -> int:
    if os.environ.get("HARNESS_SKIP_MCP_GUARD"):
        return 0

    exit_if_unmanaged_repo()

    command = _extract_command()
    if not command:
        return 0

    script_name = _find_managed_script(command)
    if script_name:
        if _subcommand_in_allowlist(script_name, command):
            return 0
        tool_name = _infer_tool(script_name, command)
        print(_managed_cli_message(script_name, tool_name, command), file=sys.stderr)
        return 2

    targets = _extract_mutation_targets(command)
    if targets:
        print(_mutation_message(targets[0], command), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
