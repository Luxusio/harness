#!/usr/bin/env python3
"""Read-only SessionStart reminder for numbered Harness project-file migrations."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from setup_finalize import (  # type: ignore
    OPERATIONAL_IGNORES,
    PROJECT_FORMAT_VERSION,
    effective_ignore_errors,
    read_project_format_version,
    require_exact_git_root,
    safe_path,
)


def reminder(repo: Path) -> str:
    try:
        manifest = safe_path(repo, "doc/harness/manifest.yaml")
        legacy = safe_path(repo, "doc/harness/.format-version")
        gitignore = safe_path(repo, ".gitignore")
        require_exact_git_root(repo)
        version = read_project_format_version(manifest)
        lines = set(gitignore.read_text(encoding="utf-8").splitlines()) if gitignore.is_file() else set()
    except (OSError, ValueError) as exc:
        return f"[harness-version] Cannot check Harness version: {exc}. Inspect Harness setup before editing."

    missing = [entry for entry in OPERATIONAL_IGNORES if entry not in lines]
    ignore_errors = effective_ignore_errors(repo) if not missing else []
    if version == PROJECT_FORMAT_VERSION and not missing and not ignore_errors and not legacy.exists():
        return ""

    script = shlex.quote(str(Path(__file__).with_name("setup_finalize.py")))
    command = f"PYTHONDONTWRITEBYTECODE=1 python3 {script} --repo {shlex.quote(str(repo))} --migrate-harness-version"
    if version < PROJECT_FORMAT_VERSION:
        detail = f"harness version {version} -> {PROJECT_FORMAT_VERSION}"
    elif legacy.exists() and not missing and not ignore_errors:
        detail = "obsolete standalone version file"
    else:
        detail = "operational .gitignore drift"
    return (
        f"[harness-version] {detail}; {len(missing)} ignore entries missing, "
        f"effective-ignore errors={len(ignore_errors)}. "
        f"For a mutating task, invoke $harness:run and run `{command}`. "
        "Commit the resulting .gitignore and doc/harness/manifest.yaml changes. "
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
