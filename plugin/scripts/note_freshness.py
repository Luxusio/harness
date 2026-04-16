#!/usr/bin/env python3
"""Mark doc notes suspect when their invalidated_by_paths have changed.

Walks `doc/**/*.md`, reads YAML frontmatter between leading `---` fences,
and for any note whose `invalidated_by_paths:` list intersects the given
changed-path set AND whose `freshness:` is `current`, flips `freshness`
to `suspect`.

Change source:
  --paths <p>...    explicit list (repeatable or space-separated)
  --from-git N      `git diff --name-only HEAD~N HEAD` (default when no --paths)

Safe to run on every SessionStart. Best-effort — never fails the session.
Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

FRESHNESS_CURRENT = "current"
FRESHNESS_SUSPECT = "suspect"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: str, content: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".note.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """Return (frontmatter_content, body_after_closing_fence, closing_fence_line_index).

    Returns (None, text, -1) if no valid frontmatter found.
    """
    if not text.startswith("---"):
        return None, text, -1
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None, text, -1
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            fm = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            return fm, body, i
    return None, text, -1


def _read_array(frontmatter: str, field: str) -> list[str]:
    lines = frontmatter.splitlines()
    prefix = field + ":"
    for i, ln in enumerate(lines):
        if ln.startswith(prefix):
            rest = ln[len(prefix):].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if not inner:
                    return []
                return [x.strip().strip('"').strip("'") for x in inner.split(",")]
            items: list[str] = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.+?)\s*$", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def _read_scalar(frontmatter: str, field: str) -> str | None:
    m = re.search(rf"^{re.escape(field)}:\s*(.*)$", frontmatter, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _set_scalar(frontmatter: str, field: str, value: str) -> str:
    pattern = rf"^{re.escape(field)}:\s*.*$"
    replacement = f"{field}: {value}"
    new_fm, n = re.subn(pattern, replacement, frontmatter, count=1, flags=re.MULTILINE)
    if n:
        return new_fm
    new_fm = new_fm.rstrip("\n") + "\n"
    return new_fm + f"{field}: {value}\n"


def _path_matches(note_pattern: str, changed: set[str]) -> bool:
    """Treat note_pattern as an exact file match OR directory prefix match."""
    p = note_pattern.rstrip("/")
    for ch in changed:
        if ch == p or ch.startswith(p + "/"):
            return True
    return False


def _gather_changed(from_git: int | None, explicit: list[str]) -> set[str]:
    paths: set[str] = set()
    for p in explicit:
        for part in p.split():
            if part:
                paths.add(part)
    if paths:
        return paths
    if from_git is None:
        return paths
    try:
        repo_root = os.getcwd()
        try:
            rr = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, check=False,
            )
            if rr.returncode == 0 and rr.stdout.strip():
                repo_root = rr.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        rev = f"HEAD~{from_git}" if from_git > 0 else "HEAD"
        r = subprocess.run(
            ["git", "diff", "--name-only", rev, "HEAD"],
            capture_output=True, text=True, check=False,
            cwd=repo_root,
        )
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln:
                paths.add(ln)
    except (OSError, subprocess.SubprocessError):
        pass
    return paths


def scan(doc_root: str, changed: set[str]) -> list[dict]:
    results: list[dict] = []
    if not os.path.isdir(doc_root):
        return results
    for dirpath, _, filenames in os.walk(doc_root):
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            fm, body, _fence_idx = _split_frontmatter(text)
            if fm is None:
                continue
            invalidated = _read_array(fm, "invalidated_by_paths")
            if not invalidated:
                continue
            freshness = _read_scalar(fm, "freshness") or FRESHNESS_CURRENT
            if freshness != FRESHNESS_CURRENT:
                continue
            hit = next((p for p in invalidated if _path_matches(p, changed)), None)
            if not hit:
                continue
            new_fm = _set_scalar(fm, "freshness", FRESHNESS_SUSPECT)
            new_fm = _set_scalar(new_fm, "freshness_updated", now_iso())
            if not new_fm.endswith("\n"):
                new_fm += "\n"
            new_text = "---\n" + new_fm + "---\n" + body
            try:
                _atomic_write(path, new_text)
            except OSError:
                continue
            results.append({"path": path, "matched": hit})
    return results


def main() -> int:
    p = argparse.ArgumentParser(description="Invalidate doc notes by changed paths")
    p.add_argument("--doc-root", default="doc")
    p.add_argument("--paths", nargs="*", default=[])
    p.add_argument("--from-git", type=int, default=None,
                   help="fallback to `git diff HEAD~N HEAD` if --paths empty")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    changed = _gather_changed(args.from_git, args.paths)
    if not changed:
        if not args.quiet:
            print("note_freshness: no changed paths — nothing to do")
        return 0
    results = scan(args.doc_root, changed)
    if not args.quiet:
        if not results:
            print(f"note_freshness: 0 notes invalidated ({len(changed)} paths checked)")
        else:
            for r in results:
                print(f"SUSPECT: {r['path']} (matched {r['matched']})")
            print(f"note_freshness: {len(results)} note(s) flipped current -> suspect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
