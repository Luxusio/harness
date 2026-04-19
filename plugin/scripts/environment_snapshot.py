#!/usr/bin/env python3
"""One-shot environment probe, written to ``ENVIRONMENT_SNAPSHOT.md``.

Called from ``handle_task_start`` right after scaffolding so agents — post
compaction, on resume, or at first-time task orientation — have a compact
file of the repo/toolchain state without re-running ``pwd``, ``git status``,
and ``cat manifest.yaml`` by hand.

Pure probe: no network, stdlib only, read-only. ``snapshot()`` swallows its
own exceptions and returns ``""`` on failure so the MCP server's task_start
never blocks on a probe issue.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import yaml_field, find_repo_root, MANIFEST_PATH  # type: ignore
except Exception:
    yaml_field = None
    find_repo_root = None
    MANIFEST_PATH = "doc/harness/manifest.yaml"


ARTIFACT_NAME = "ENVIRONMENT_SNAPSHOT.md"

_ROOT_ENTRIES_CAP = 20

_TOOLING_FIELDS = (
    "ast_grep_ready",
    "lsp_ready",
    "observability_ready",
    "chrome_devtools_ready",
)

_MANIFEST_TOP_FIELDS = (
    "test_command",
    "build_command",
    "dev_command",
    "smoke_command",
    "healthcheck_command",
)


def _run(cmd: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=3)
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _git_branch(repo_root: str) -> str:
    return _run(["git", "branch", "--show-current"], repo_root) or "unknown"


def _git_dirty(repo_root: str) -> bool:
    """True when working tree has any uncommitted change (staged or unstaged).

    Best-effort: errors render as clean (``False``).
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=repo_root, timeout=3,
        )
    except Exception:
        return False
    if r.returncode != 0:
        return False
    return bool(r.stdout.strip())


def _manifest_fields(repo_root: str) -> dict[str, str]:
    manifest = os.path.join(repo_root, MANIFEST_PATH)
    out: dict[str, str] = {}
    for field in _MANIFEST_TOP_FIELDS:
        if yaml_field is None:
            out[field] = ""
            continue
        out[field] = yaml_field(field, manifest) or ""
    # project_meta.shape is a nested key; flatten by scanning the file.
    try:
        with open(manifest, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        body = ""
    shape = ""
    in_pm = False
    for line in body.splitlines():
        if line.startswith("project_meta:"):
            in_pm = True
            continue
        if in_pm:
            if line.startswith(" ") and "shape:" in line:
                shape = line.split(":", 1)[1].strip().strip('"').strip("'")
                break
            if not line.startswith(" ") and line.strip():
                break
    out["project_shape"] = shape
    return out


def _tooling_block(repo_root: str) -> dict[str, str]:
    manifest = os.path.join(repo_root, MANIFEST_PATH)
    try:
        with open(manifest, encoding="utf-8") as f:
            body = f.read()
    except OSError:
        body = ""
    out = {k: "unknown" for k in _TOOLING_FIELDS}
    in_tooling = False
    for line in body.splitlines():
        if line.startswith("tooling:"):
            in_tooling = True
            continue
        if in_tooling:
            if not line.startswith(" ") and line.strip():
                break
            for field in _TOOLING_FIELDS:
                prefix = f"  {field}:"
                if line.startswith(prefix):
                    val = line[len(prefix):].strip().lower()
                    out[field] = val if val in ("true", "false") else "unknown"
    return out


def _root_entries(repo_root: str) -> list[str]:
    try:
        entries = sorted(os.listdir(repo_root))
    except OSError:
        return []
    visible = [e for e in entries if not e.startswith(".")][:_ROOT_ENTRIES_CAP]
    return visible


def _render(ctx: dict[str, Any]) -> str:
    lines: list[str] = ["# Environment snapshot", ""]
    repo = ctx.get("repo", {})
    lines.append("## Repo")
    lines.append(f"- root: `{repo.get('root', '')}`")
    lines.append(f"- branch: `{repo.get('branch', '')}`")
    lines.append(f"- dirty: {repo.get('dirty', False)}")
    lines.append("")

    lines.append("## Manifest")
    mf = ctx.get("manifest", {})
    for field in _MANIFEST_TOP_FIELDS:
        val = mf.get(field, "")
        lines.append(f"- {field}: `{val}`")
    lines.append(f"- project_shape: `{mf.get('project_shape', '')}`")
    lines.append("")

    lines.append("## Tooling")
    tl = ctx.get("tooling", {})
    for field in _TOOLING_FIELDS:
        lines.append(f"- {field}: {tl.get(field, 'unknown')}")
    lines.append("")

    lines.append("## Root entries")
    entries = ctx.get("root_entries", [])
    if entries:
        lines.extend(f"- {e}" for e in entries)
    else:
        lines.append("- (empty)")
    lines.append("")

    return "\n".join(lines)


def snapshot(task_dir: str, repo_root: str | None = None) -> str:
    """Write ENVIRONMENT_SNAPSHOT.md into ``task_dir``; return its path.

    Overwrites an existing snapshot (resume writes a fresh file). Any failure
    returns ``""``.
    """
    try:
        if not task_dir:
            return ""
        if repo_root is None:
            repo_root = find_repo_root() if find_repo_root else os.getcwd()
        ctx = {
            "repo": {
                "root": repo_root,
                "branch": _git_branch(repo_root),
                "dirty": _git_dirty(repo_root),
            },
            "manifest": _manifest_fields(repo_root),
            "tooling": _tooling_block(repo_root),
            "root_entries": _root_entries(repo_root),
        }
        path = os.path.join(task_dir, ARTIFACT_NAME)
        os.makedirs(task_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_render(ctx))
        return path
    except Exception:
        return ""


def main() -> int:
    """Module runnable standalone for CLI smoke testing.

    Writes snapshot to the active task dir (``.active`` marker) or current dir.
    """
    repo_root = find_repo_root() if find_repo_root else os.getcwd()
    task_dir = os.getcwd()
    active = os.path.join(repo_root, "doc", "harness", "tasks", ".active")
    if os.path.isfile(active):
        try:
            with open(active, encoding="utf-8") as f:
                td = f.read().strip()
            if td and os.path.isdir(td):
                task_dir = td
        except OSError:
            pass
    path = snapshot(task_dir, repo_root)
    if path:
        print(path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
