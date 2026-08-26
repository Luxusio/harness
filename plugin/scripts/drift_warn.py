#!/usr/bin/env python3
"""SessionStart hook — warn when the loaded plugin is behind source.

Compares SHA256 of every *.py file in plugin/scripts/ (source) against the
scripts directory this file is executing from — which is, by definition, the
tree the session actually loaded.

That self-location matters. This check previously hardcoded
``~/.claude/harness-dev/plugin/scripts`` as "the installed copy", which is
exactly the directory ``install.py --force`` faithfully updates. On 2026-08-26
the session was loading hooks from an entirely different tree
(``~/.claude/plugins/cache/harness/harness/2.3.0``, registered in
``installed_plugins.json`` and never re-resolved after the marketplace was
repointed). Source and harness-dev agreed byte for byte, so this hook reported
no drift while the loaded tree was three months stale and missing the whole
receipt subsystem. The one check meant to catch that was structurally incapable
of catching it.

Reading ``__file__`` needs no env var and no config parsing, and it works across
layout changes (``scripts/`` at tree root in 2.3.0, ``plugin/scripts/`` now).

Scope note: this reports staleness of the *loaded* tree only. When hooks are
loaded straight from the repo checkout the check goes silent, even though a
separate install target may itself be behind — the MCP server is registered by
absolute path and can resolve to a different tree than the hooks do. That split
is the whole subject of `hook_tree_health.py`; this hook covers one side of it.

Files that exist only in the loaded dir (legacy/leftover) are NOT counted
as diffs — they are harmless.

Fail-safe: any exception → silent exit 0. Hook wrapper has `|| true` anyway.
"""
from __future__ import annotations

import hashlib
import os
import sys

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

        # The tree this hook was loaded from, not a guess at where it should be.
        loaded_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isdir(loaded_dir) or os.path.samefile(loaded_dir, source_dir):
            # Running straight out of the repo checkout: there is no separate
            # installed copy to be behind.
            return 0

        source_manifest = _build_manifest(source_dir)
        loaded_manifest = _build_manifest(loaded_dir)

        diff_count = 0
        for name, src_hash in source_manifest.items():
            loaded_hash = loaded_manifest.get(name)
            if loaded_hash is None or loaded_hash != src_hash:
                diff_count += 1

        if diff_count > 0:
            # Name the resolved root: when the loaded tree is not the one
            # install.py writes to, "run install.py --force" is the wrong
            # remedy and the path is the only way to tell.
            print(
                f"[drift] loaded plugin behind source ({diff_count} files differ)"
                f" — loaded from {loaded_dir}"
                " — run `python3 install.py --force`, and if that path is not"
                " the tree you install to, update the plugin and restart"
            )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
