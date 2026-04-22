#!/usr/bin/env python3
"""maintain_restore.py — restore a doc archived by doc_hygiene.py.

Usage:
  python3 plugin/scripts/maintain_restore.py <archive-path>

  <archive-path> must be a file under a _archive/ subdirectory,
  e.g. doc/changes/_archive/foo.md

Example:
  python3 plugin/scripts/maintain_restore.py doc/changes/_archive/foo.md

To find archives in git log:
  git log --all --follow --diff-filter=R -- 'doc/changes/_archive/*.md'

AC-011: uses subprocess git mv; refuses overwrite; git log --follow works.
AC-012: --help shows usage with concrete example and recovery hint.
AC-RS-05: script body <= 75 LOC (soft budget).

Stdlib only. Exits non-zero on any error condition.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys


_ARCHIVE_DIR = "_archive"


def _strip_sha7_suffix(basename: str) -> str:
    """Remove .archived-YYYYMMDDTHHMMSSZ suffix if present."""
    name, ext = os.path.splitext(basename)
    # Match .archived-YYYYMMDDTHHMMSSZ
    m = re.search(r"\.archived-\d{8}T\d{6}Z$", name)
    if m:
        name = name[:m.start()]
    return name + ext


def restore(archive_path: str, repo_root: str | None = None) -> int:
    """Restore archive_path to its original location via git mv.

    Returns 0 on success, 1 on any failure.
    """
    if repo_root is None:
        # Auto-detect
        d = os.path.abspath(os.getcwd())
        while d != "/":
            if os.path.isdir(os.path.join(d, ".git")):
                repo_root = d
                break
            d = os.path.dirname(d)
        if repo_root is None:
            repo_root = os.getcwd()

    # Resolve to absolute
    abs_archive = os.path.abspath(os.path.join(repo_root, archive_path))

    # Validate: must be under _archive/
    parts = os.path.relpath(abs_archive, repo_root).replace(os.sep, "/").split("/")
    if _ARCHIVE_DIR not in parts:
        print(f"ERROR: path is not under '{_ARCHIVE_DIR}/': {archive_path}", file=sys.stderr)
        print("Restore only works on files previously archived by doc_hygiene.py.", file=sys.stderr)
        return 1

    if not os.path.isfile(abs_archive):
        print(f"ERROR: archive file not found: {abs_archive}", file=sys.stderr)
        return 1

    # Compute original location: parent of _archive/ + original basename
    archive_dir = os.path.dirname(abs_archive)
    source_dir = os.path.dirname(archive_dir)  # parent of _archive/
    original_basename = _strip_sha7_suffix(os.path.basename(abs_archive))
    dest_abs = os.path.join(source_dir, original_basename)

    if os.path.exists(dest_abs):
        print(f"ERROR: destination already exists: {os.path.relpath(dest_abs, repo_root)}",
              file=sys.stderr)
        print("Refusing to overwrite. Move or remove the existing file first.", file=sys.stderr)
        return 1

    rel_src = os.path.relpath(abs_archive, repo_root)
    rel_dest = os.path.relpath(dest_abs, repo_root)

    r = subprocess.run(
        ["git", "mv", rel_src, rel_dest],
        cwd=repo_root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"ERROR: git mv failed: {r.stderr.strip()}", file=sys.stderr)
        return 1

    subprocess.run(
        ["git", "commit", "-m",
         f"hygiene: restore {rel_dest}\n\nRestored from archive via maintain_restore.py."],
        cwd=repo_root, capture_output=True, text=True,
    )
    print(f"Restored: {rel_dest}")
    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Restore a doc archived by doc_hygiene.py.",
        epilog=(
            "Example:\n"
            "  python3 plugin/scripts/maintain_restore.py "
            "doc/changes/_archive/foo.md\n\n"
            "To find archived files in git history:\n"
            "  git log --all --follow --diff-filter=R -- "
            "'doc/changes/_archive/*.md'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("archive_path", help="Path to the archived file (under _archive/)")
    p.add_argument("--repo-root", default=None, help="Repo root (default: auto-detect)")
    args = p.parse_args()
    return restore(args.archive_path, args.repo_root)


if __name__ == "__main__":
    sys.exit(main())
