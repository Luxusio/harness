#!/usr/bin/env python3
"""Create a bounded environment snapshot artifact for a task.

The snapshot is intentionally compact and deterministic. It captures the
minimum environment facts that often cost the agent a few exploratory turns:
working directory, root layout, git cleanliness, important manifest commands,
and availability of common developer tools.

This artifact is meant to be *written eagerly* at task start, but only surfaced
by task_context when it is likely to help (broad-build planning or blocked-env
recovery).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import MANIFEST, get_browser_qa_status, manifest_field, now_iso, yaml_array

SNAPSHOT_FILENAME = "ENVIRONMENT_SNAPSHOT.md"
ROOT_LIST_LIMIT = 12
COMMAND_TIMEOUT = 2
TOOL_ORDER = [
    "python3",
    "pytest",
    "rg",
    "git",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "uv",
    "go",
    "rustc",
    "cargo",
    "docker",
]


def _run_capture(argv: list[str], cwd: str | None = None, timeout: int = COMMAND_TIMEOUT) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""

    if result.returncode != 0:
        return ""

    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return ""
    first_line = output.splitlines()[0].strip()
    return first_line[:120]


def _root_listing(repo_root: str, limit: int = ROOT_LIST_LIMIT) -> list[str]:
    try:
        entries = sorted(os.listdir(repo_root))
    except OSError:
        return []

    visible = []
    for entry in entries:
        if entry in (".git", ".DS_Store"):
            continue
        full = os.path.join(repo_root, entry)
        visible.append(entry + "/" if os.path.isdir(full) else entry)
        if len(visible) >= limit:
            break
    return visible


def _git_summary(repo_root: str) -> str:
    branch = _run_capture(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
    if not branch:
        return "git: unavailable"

    dirty = _run_capture(["git", "status", "--short"], cwd=repo_root)
    state = "dirty" if dirty else "clean"
    return f"git: {branch} ({state})"


def _tool_versions(tools: Iterable[str] = TOOL_ORDER) -> list[str]:
    results = []
    for tool in tools:
        path = shutil.which(tool)
        if not path:
            continue

        version = ""
        if tool == "python3":
            version = _run_capture([tool, "--version"]) or _run_capture([tool, "-V"])
        elif tool == "pytest":
            version = _run_capture([tool, "--version"])
        elif tool == "git":
            version = _run_capture([tool, "--version"])
        elif tool in ("node", "npm", "pnpm", "yarn", "uv", "go", "rustc", "cargo", "docker"):
            version = _run_capture([tool, "--version"])
        elif tool == "rg":
            version = _run_capture([tool, "--version"])

        if version:
            results.append(f"{tool}: {version}")
        else:
            results.append(f"{tool}: {path}")
    return results[:8]


def collect_environment_snapshot(repo_root: str = ".") -> dict:
    repo_root = os.path.abspath(repo_root)
    verify_commands = yaml_array("verify_commands", MANIFEST) if os.path.isfile(MANIFEST) else []

    return {
        "captured_at": now_iso(),
        "repo_root": repo_root,
        "cwd": os.getcwd(),
        "project_name": manifest_field("name") or "repo",
        "project_type": manifest_field("type") or "unknown",
        "browser": get_browser_qa_status(),
        "observability": manifest_field("observability_enabled") or manifest_field("observability_ready") or "false",
        "test_command": manifest_field("test_command") or "",
        "verify_commands": verify_commands[:3],
        "git_summary": _git_summary(repo_root),
        "root_entries": _root_listing(repo_root),
        "tool_versions": _tool_versions(),
    }


def render_environment_snapshot(snapshot: dict, reason: str = "task_start") -> str:
    verify_commands = snapshot.get("verify_commands") or []
    tool_versions = snapshot.get("tool_versions") or []
    root_entries = snapshot.get("root_entries") or []

    lines = [
        "# Environment Snapshot",
        f"captured_at: {snapshot.get('captured_at', now_iso())}",
        f"reason: {reason}",
        "",
        "## Quick facts",
        f"- repo_root: {snapshot.get('repo_root', '')}",
        f"- cwd: {snapshot.get('cwd', '')}",
        f"- project: {snapshot.get('project_name', 'repo')} ({snapshot.get('project_type', 'unknown')})",
        f"- {snapshot.get('git_summary', 'git: unavailable')}",
        f"- browser: {snapshot.get('browser', 'unknown')}",
        f"- observability_ready: {snapshot.get('observability', 'false')}",
    ]

    test_command = snapshot.get("test_command") or ""
    if test_command:
        lines.append(f"- test_command: {test_command}")
    if verify_commands:
        lines.append(f"- verify_commands: {' ; '.join(verify_commands)}")
    if root_entries:
        lines.append(f"- root_entries: {', '.join(root_entries)}")

    if tool_versions:
        lines.append("")
        lines.append("## Available tools")
        for item in tool_versions:
            lines.append(f"- {item}")

    return "\n".join(lines).rstrip() + "\n"


def write_environment_snapshot(task_dir: str, repo_root: str = ".", reason: str = "task_start") -> str:
    if not task_dir or not os.path.isdir(task_dir):
        return ""

    snapshot = collect_environment_snapshot(repo_root=repo_root)
    content = render_environment_snapshot(snapshot, reason=reason)
    path = os.path.join(task_dir, SNAPSHOT_FILENAME)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError:
        return ""
    return path


if __name__ == "__main__":
    task_dir = sys.argv[1] if len(sys.argv) > 1 else ""
    if not task_dir:
        raise SystemExit(1)
    out = write_environment_snapshot(task_dir, repo_root=os.getcwd())
    if out:
        print(out)
