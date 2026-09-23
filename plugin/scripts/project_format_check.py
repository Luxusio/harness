#!/usr/bin/env python3
"""Read-only SessionStart reminder for the numbered Harness manifest version."""
from __future__ import annotations

import os
import shlex
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from setup_finalize import (  # type: ignore
    MANIFEST_VERSION,
    OPERATIONAL_IGNORES,
    effective_ignore_errors,
    manifest_maps,
    read_manifest_version,
    require_exact_git_root,
    safe_path,
)


def reminder(repo: Path) -> str:
    try:
        manifest = safe_path(repo, "doc/harness/manifest.yaml")
        version_marker = safe_path(repo, "doc/harness/.version")
        format_marker = safe_path(repo, "doc/harness/.format-version")
        gitignore = safe_path(repo, ".gitignore")
        require_exact_git_root(repo)
        version = read_manifest_version(manifest)
        top, _, manifest_errors = manifest_maps(manifest.read_text(encoding="utf-8"))
        if manifest_errors:
            raise ValueError("; ".join(manifest_errors))
        for marker in (version_marker, format_marker):
            try:
                info = os.lstat(marker)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{marker} must be a regular file")
        lines = set(gitignore.read_text(encoding="utf-8").splitlines()) if gitignore.is_file() else set()
    except (OSError, ValueError) as exc:
        return f"[harness-version] Cannot check Harness version: {exc}. Inspect Harness setup before editing."

    missing = [entry for entry in OPERATIONAL_IGNORES if entry not in lines]
    ignore_errors = effective_ignore_errors(repo) if not missing else []
    has_harness_version_key = "harness_version" in top
    leftover = version_marker.exists() or format_marker.exists()
    if (
        version == MANIFEST_VERSION
        and not has_harness_version_key
        and not missing
        and not ignore_errors
        and not leftover
    ):
        return ""

    script = shlex.quote(str(Path(__file__).with_name("setup_finalize.py")))
    command = f"PYTHONDONTWRITEBYTECODE=1 python3 {script} --repo {shlex.quote(str(repo))} --migrate-harness-version"
    if version < MANIFEST_VERSION:
        detail = f"manifest version {version} -> {MANIFEST_VERSION}"
    elif has_harness_version_key:
        detail = "obsolete harness_version manifest key"
    elif leftover and not missing and not ignore_errors:
        detail = "obsolete standalone version file"
    else:
        detail = "operational .gitignore drift"
    return (
        f"[harness-version] {detail}; {len(missing)} ignore entries missing, "
        f"effective-ignore errors={len(ignore_errors)}. "
        f"For a mutating task, invoke $harness:run and run `{command}`. "
        "Commit the resulting .gitignore, doc/harness/manifest.yaml, and the "
        "deletion of doc/harness/.version. "
        "If an operational file is already tracked, report it; an ignore rule cannot untrack it."
    )


def main() -> int:
    try:
        from _lib import find_repo_root, is_harness_enabled_repo  # type: ignore
        root = find_repo_root()
        if root and is_harness_enabled_repo(root):
            message = reminder(Path(root))
            if message:
                print(message)
    except Exception:
        pass  # SessionStart diagnostics must not break the runtime.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
