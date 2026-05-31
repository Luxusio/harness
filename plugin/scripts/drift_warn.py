#!/usr/bin/env python3
"""SessionStart hook — warn when installed plugin is behind source.

Compares SHA256 of every *.py file in plugin/scripts/ (source) against the
installed copy at ~/.claude/harness-dev/plugin/scripts/. Prints a one-line
warning when any source file is missing from or differs in the installed dir.

Files that exist only in the installed dir (legacy/leftover) are NOT counted
as diffs — they are harmless.

Fail-safe: any exception → silent exit 0. Hook wrapper has `|| true` anyway.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _sha256(path: str) -> str | None:
    """Return hex SHA256 of file, or None on any error."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _build_manifest(scripts_dir: str) -> dict[str, str]:
    """Return basename -> SHA256 for every *.py file (skip __pycache__)."""
    manifest: dict[str, str] = {}
    try:
        for entry in os.scandir(scripts_dir):
            if entry.is_dir():
                # Skip __pycache__ and any subdirectory
                continue
            if not entry.name.endswith(".py"):
                continue
            digest = _sha256(entry.path)
            if digest is not None:
                manifest[entry.name] = digest
    except Exception:
        pass
    return manifest


def main() -> int:
    try:
        from _lib import find_repo_root, is_harness_enabled_repo  # type: ignore
    except Exception:
        return 0

    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return 0

        source_dir = os.path.join(repo_root, "plugin", "scripts")
        if not os.path.isdir(source_dir):
            return 0

        installed_dir = str(
            Path.home() / ".claude" / "harness-dev" / "plugin" / "scripts"
        )
        if not os.path.isdir(installed_dir):
            return 0

        source_manifest = _build_manifest(source_dir)
        installed_manifest = _build_manifest(installed_dir)

        diff_count = 0
        for name, src_hash in source_manifest.items():
            inst_hash = installed_manifest.get(name)
            if inst_hash is None or inst_hash != src_hash:
                diff_count += 1

        if diff_count > 0:
            print(
                f"[drift] installed plugin behind source ({diff_count} files differ)"
                " — run `python3 install.py --force`"
            )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
