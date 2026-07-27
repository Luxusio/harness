#!/usr/bin/env python3
"""One-shot environment probe, written to ``ENVIRONMENT_SNAPSHOT.md``.

Called from ``handle_task_start`` right after scaffolding so agents — post
compaction, on resume, or at first-time task orientation — have a compact
file of the repo/toolchain state without re-running ``pwd`` and
``cat manifest.yaml`` by hand.

Pure probe: no network, stdlib only, read-only. ``snapshot()`` swallows its
own exceptions and returns ``""`` on failure so the MCP server's task_start
never blocks on a probe issue.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import find_repo_root, MANIFEST_PATH  # type: ignore
except Exception:
    find_repo_root = None
    MANIFEST_PATH = "doc/harness/manifest.yaml"


ARTIFACT_NAME = "ENVIRONMENT_SNAPSHOT.md"

_ROOT_ENTRIES_CAP = 20
_PROBE_BUDGET_SECONDS = 4.0
_COMMAND_TIMEOUT_SECONDS = 3.0
_MANIFEST_SIZE_CAP = 256 * 1024

_TOOLING_FIELDS = (
    "ast_grep_ready",
    "lsp_ready",
    "observability_ready",
    "chrome_devtools_ready",
)

_TOOL_MANAGER_COMMANDS = {
    "mise": {
        "probe": ["mise", "--version"],
        "activate": "eval \"$(mise activate bash)\"",
    },
    "asdf": {
        "probe": ["asdf", "--version"],
        "activate": ". \"$(asdf where asdf 2>/dev/null)/asdf.sh\"",
    },
    "volta": {
        "probe": ["volta", "--version"],
        "activate": "volta is shim-based; ensure ~/.volta/bin is on PATH",
    },
}

_VERSION_COMMANDS = {
    "python": ["python", "--version"],
    "python3": ["python3", "--version"],
    "uv": ["uv", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "cargo": ["cargo", "--version"],
    "java": ["java", "-version"],
    "gradle": ["gradle", "--version"],
    "docker": ["docker", "--version"],
    "git": ["git", "--version"],
    "gh": ["gh", "--version"],
    "mise": ["mise", "--version"],
    "asdf": ["asdf", "--version"],
    "volta": ["volta", "--version"],
}

_MANIFEST_TOP_FIELDS = (
    "test_command",
    "build_command",
    "dev_command",
    "smoke_command",
    "healthcheck_command",
)


def _run(cmd: list[str], cwd: str, deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=min(_COMMAND_TIMEOUT_SECONDS, remaining),
        )
    except Exception:
        return ""
    if r.returncode != 0:
        return ""
    return (r.stdout.strip() or r.stderr.strip())


def _short_version(raw: str) -> str:
    if not raw:
        return "missing"
    first = raw.splitlines()[0].strip()
    if len(first) > 120:
        first = first[:117].rstrip() + "..."
    return first or "missing"


def _git_branch(repo_root: str, deadline: float) -> str:
    return _run(["git", "branch", "--show-current"], repo_root, deadline) or "unknown"


def _read_manifest(repo_root: str) -> str:
    """Read one bounded regular manifest without following its leaf."""
    manifest = os.path.join(repo_root, MANIFEST_PATH)
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        before = os.lstat(manifest)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MANIFEST_SIZE_CAP:
            return ""
        fd = os.open(manifest, flags)
    except (FileNotFoundError, OSError):
        return ""
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > _MANIFEST_SIZE_CAP
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            return ""
        with os.fdopen(fd, encoding="utf-8") as file_obj:
            fd = -1
            body = file_obj.read(_MANIFEST_SIZE_CAP + 1)
        if len(body.encode("utf-8")) > _MANIFEST_SIZE_CAP:
            return ""
        after = os.lstat(manifest)
        if (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)) != (
            opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)
        ):
            return ""
        return body
    except (OSError, UnicodeError):
        return ""
    finally:
        if fd >= 0:
            os.close(fd)


def _top_level_value(body: str, field: str) -> str:
    prefix = f"{field}:"
    for line in body.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if value in {"null", "~", "", "[]"}:
                return ""
            return value.strip('"').strip("'")
    return ""


def _manifest_fields(body: str) -> dict[str, str]:
    out = {field: _top_level_value(body, field) for field in _MANIFEST_TOP_FIELDS}
    # project_meta.shape is a nested key; flatten by scanning the captured text.
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


def _tooling_block(body: str) -> dict[str, str]:
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


def _tool_managers(repo_root: str, deadline: float) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for name, spec in _TOOL_MANAGER_COMMANDS.items():
        path = shutil.which(name)
        version = (
            _short_version(_run(spec["probe"], repo_root, deadline))
            if path else "missing"
        )
        out[name] = {
            "path": path or "missing",
            "version": version,
            "activate": spec["activate"] if path else "",
        }
    return out


def _tool_versions(repo_root: str, deadline: float) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, cmd in _VERSION_COMMANDS.items():
        if not shutil.which(cmd[0]):
            out[name] = "missing"
            continue
        out[name] = _short_version(_run(cmd, repo_root, deadline))
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

    lines.append("## Tool managers")
    managers = ctx.get("tool_managers", {})
    if managers:
        for name in sorted(managers):
            info = managers.get(name, {})
            line = f"- {name}: `{info.get('version', 'missing')}`"
            path = info.get("path", "missing")
            if path and path != "missing":
                line += f" at `{path}`"
            activate = info.get("activate", "")
            if activate:
                line += f" | activate: `{activate}`"
            lines.append(line)
    else:
        lines.append("- (none checked)")
    lines.append("")

    lines.append("## Tool versions")
    versions = ctx.get("tool_versions", {})
    if versions:
        for name in sorted(versions):
            lines.append(f"- {name}: `{versions.get(name, 'missing')}`")
    else:
        lines.append("- (none checked)")
    lines.append("")

    lines.append("## Root entries")
    entries = ctx.get("root_entries", [])
    if entries:
        lines.extend(f"- {e}" for e in entries)
    else:
        lines.append("- (empty)")
    lines.append("")

    return "\n".join(lines)


def _write_snapshot(path: str, body: str) -> None:
    """Atomically replace the snapshot leaf without following special files."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=directory, prefix=".environment-snapshot.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            fd = -1
            file_obj.write(body)
            file_obj.flush()
            try:
                os.fsync(file_obj.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    finally:
        if fd >= 0:
            os.close(fd)


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
        deadline = time.monotonic() + _PROBE_BUDGET_SECONDS
        manifest = _read_manifest(repo_root)
        ctx = {
            "repo": {
                "root": repo_root,
                "branch": _git_branch(repo_root, deadline),
            },
            "manifest": _manifest_fields(manifest),
            "tooling": _tooling_block(manifest),
            "tool_managers": _tool_managers(repo_root, deadline),
            "tool_versions": _tool_versions(repo_root, deadline),
            "root_entries": _root_entries(repo_root),
        }
        path = os.path.join(task_dir, ARTIFACT_NAME)
        _write_snapshot(path, _render(ctx))
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
