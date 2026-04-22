#!/usr/bin/env python3
"""doc_hygiene.py — content-signal-based KEEP/REMOVE/REVIEW classifier for doc/ files.

Scans doc/changes/ and doc/common/. HARD-excludes doc/harness/patterns/ (owned
by promote_learnings.py). Classifies each file as KEEP, REMOVE, or REVIEW based
on content signals, then:
  - REMOVE: archives via git mv to <source-dir>/_archive/ (history preserved)
  - REVIEW: appends entry to doc/harness/.maintain-pending.json (atomic write)
  - KEEP: no action

AC-005: HARD-exclude doc/harness/patterns/ — never touched here.
AC-006: Signal computation — token-grep across CLAUDE.md + doc/** + active PLAN.md files.
AC-007: Classification rules — absence of new frontmatter NEVER classifies as REMOVE.
AC-008: Archive via git mv; SHA7 suffix on collision; commit msg with restore command.
AC-009: REVIEW appended to .maintain-pending.json via atomic write.
AC-010: hygiene.yaml pin_paths honored; path validation; malformed yaml fail-safe.

.maintain-pending.json schema (single source of truth):
  List of objects:
    {
      "path": str,            # repo-relative path of file needing review
      "kind": "review",       # always "review" for REVIEW entries
      "signals": {            # computed signals dict
        "reference_count": int,
        "freshness": str,
        "cited_paths_alive": float,
        "superseded_by": str|null,
        "distilled_to": str|null,
        "tag_overlap": float
      },
      "added_at": str         # ISO timestamp
    }

Stdlib only. Never raises — all errors are logged as INFO and skipped.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (  # type: ignore
        split_frontmatter,
        read_array_field,
        read_scalar_field,
        find_repo_root,
        write_json_state,
        read_json_state,
        TASK_DIR,
    )
except ImportError as _e:
    print(f"[doc_hygiene] WARN: _lib import failed: {_e}", file=sys.stderr)
    sys.exit(0)


# ── Constants ────────────────────────────────────────────────────────────

SCAN_DIRS = ["doc/changes", "doc/common"]
# AC-005 HARD-exclude — never touch this directory
PATTERNS_DIR_SUFFIX = os.path.join("doc", "harness", "patterns")
HYGIENE_YAML = "doc/harness/hygiene.yaml"
PENDING_JSON = "doc/harness/.maintain-pending.json"
LAST_RUN_FILE = "doc/harness/.maintain-last-run"
ARCHIVE_DIR_NAME = "_archive"

KEEP = "KEEP"
REMOVE = "REMOVE"
REVIEW = "REVIEW"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── hygiene.yaml loading (AC-010) ────────────────────────────────────────

def _load_hygiene_config(repo_root: str) -> dict:
    """Load hygiene.yaml; fail-safe to defaults on any error."""
    defaults = {"enabled": True, "observer_until_session": 14, "pin_paths": []}
    cfg_path = os.path.join(repo_root, HYGIENE_YAML)
    if not os.path.isfile(cfg_path):
        return defaults
    try:
        import re as _re
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = f.read()
        # Simple YAML parse for our known fields
        cfg = dict(defaults)
        # enabled
        m = _re.search(r"^enabled:\s*(\S+)", raw, _re.MULTILINE)
        if m:
            cfg["enabled"] = m.group(1).lower() not in ("false", "no", "0")
        # observer_until_session
        m = _re.search(r"^observer_until_session:\s*(\d+)", raw, _re.MULTILINE)
        if m:
            cfg["observer_until_session"] = int(m.group(1))
        # pin_paths (block list)
        pin_paths = []
        in_pin = False
        for line in raw.splitlines():
            if line.startswith("pin_paths:"):
                in_pin = True
                rest = line[len("pin_paths:"):].strip()
                if rest and rest != "[]":
                    # compact form like pin_paths: [a, b]
                    inner = rest.strip("[]")
                    for x in inner.split(","):
                        x = x.strip().strip('"').strip("'")
                        if x:
                            pin_paths.append(x)
                continue
            if in_pin:
                sm = _re.match(r"^\s+-\s+(.+)$", line)
                if sm:
                    pin_paths.append(sm.group(1).strip().strip('"').strip("'"))
                elif line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                    in_pin = False
        cfg["pin_paths"] = _validate_pin_paths(pin_paths, repo_root)
        return cfg
    except Exception as exc:
        print(f"[doc_hygiene] INFO: malformed hygiene.yaml, using defaults: {exc}")
        return defaults


def _validate_pin_paths(raw_paths: list, repo_root: str) -> list:
    """Validate pin_paths entries. Reject absolute paths and .. traversal."""
    valid = []
    for p in raw_paths:
        if not p:
            continue
        if os.path.isabs(p):
            print(f"[doc_hygiene] INFO: pin_paths entry rejected (absolute): {p!r}")
            continue
        if ".." in p.split(os.sep) or ".." in p.split("/"):
            print(f"[doc_hygiene] INFO: pin_paths entry rejected (.. traversal): {p!r}")
            continue
        if not (p.startswith("doc/") or p.startswith("doc" + os.sep)):
            print(f"[doc_hygiene] INFO: pin_paths entry rejected (not under doc/): {p!r}")
            continue
        valid.append(p)
    return valid


def _is_pinned(rel_path: str, pin_paths: list) -> bool:
    """Check if rel_path matches any pin_paths entry (exact or fnmatch glob)."""
    for pattern in pin_paths:
        if fnmatch.fnmatch(rel_path, pattern) or rel_path == pattern:
            return True
    return False


# ── Signal computation (AC-006) ──────────────────────────────────────────

def _gather_search_corpus(repo_root: str) -> list:
    """Build list of (abs_path, rel_path) for reference-count scanning.

    Sources: CLAUDE.md, doc/**/*.md, active TASK_STATE PLAN.md files.
    Token-grep mandate: scans including inside fenced code blocks (plain regex).
    """
    corpus = []
    # CLAUDE.md
    claude_md = os.path.join(repo_root, "CLAUDE.md")
    if os.path.isfile(claude_md):
        corpus.append((claude_md, "CLAUDE.md"))
    # doc/**/*.md
    doc_root = os.path.join(repo_root, "doc")
    if os.path.isdir(doc_root):
        for dirpath, _dirs, filenames in os.walk(doc_root):
            for fn in filenames:
                if fn.endswith(".md"):
                    abs_p = os.path.join(dirpath, fn)
                    rel_p = os.path.relpath(abs_p, repo_root)
                    corpus.append((abs_p, rel_p))
    # Active task PLAN.md files (AC-006 + CL-12)
    tasks_dir = os.path.join(repo_root, TASK_DIR)
    if os.path.isdir(tasks_dir):
        for entry in os.scandir(tasks_dir):
            if entry.is_dir() and entry.name.startswith("TASK__"):
                plan_path = os.path.join(entry.path, "PLAN.md")
                if os.path.isfile(plan_path):
                    rel_p = os.path.relpath(plan_path, repo_root)
                    corpus.append((plan_path, rel_p))
    return corpus


def _count_references(rel_path: str, corpus: list, self_abs: str) -> int:
    """Count how many corpus files contain rel_path as a token (AC-006).

    Token-grep: simple string search including inside fenced code blocks.
    Excludes self-reference.
    """
    count = 0
    # Also check basename as a token
    tokens = {rel_path, os.path.basename(rel_path)}
    for abs_p, _rel in corpus:
        if abs_p == self_abs:
            continue
        try:
            with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for tok in tokens:
                if tok in text:
                    count += 1
                    break
        except OSError:
            continue
    return count


def _compute_cited_paths_ratio(body: str, repo_root: str) -> float:
    """Ratio of paths mentioned in body that still exist in repo."""
    # Extract path-like tokens: starts with doc/ or plugin/ or similar
    path_re = re.compile(r"\b(doc/[^\s`\"')\]]+|plugin/[^\s`\"')\]]+)")
    candidates = path_re.findall(body)
    if not candidates:
        return 1.0
    alive = sum(1 for p in candidates if os.path.exists(os.path.join(repo_root, p)))
    return alive / len(candidates)


def _compute_tag_overlap(tags: list, all_docs_tags: list) -> float:
    """Compute max tag overlap ratio between this doc and any other doc."""
    if not tags:
        return 0.0
    tag_set = set(tags)
    best = 0.0
    for other_tags in all_docs_tags:
        if not other_tags:
            continue
        other_set = set(other_tags)
        intersection = len(tag_set & other_set)
        union = len(tag_set | other_set)
        if union > 0:
            ratio = intersection / union
            if ratio > best:
                best = ratio
    return best


def _compute_signals(abs_path: str, rel_path: str, repo_root: str, corpus: list,
                     all_docs_tags: list) -> dict:
    """Compute all content signals for a single doc file."""
    signals = {
        "reference_count": 0,
        "freshness": "current",
        "cited_paths_alive": 1.0,
        "superseded_by": None,
        "distilled_to": None,
        "tag_overlap": 0.0,
    }
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return signals

    fm, body, _ = split_frontmatter(text)

    # Frontmatter-derived signals
    if fm is not None:
        signals["freshness"] = read_scalar_field(fm, "freshness") or "current"
        signals["superseded_by"] = read_scalar_field(fm, "superseded_by") or None
        signals["distilled_to"] = read_scalar_field(fm, "distilled_to") or None
        tags = read_array_field(fm, "tags")
        signals["tag_overlap"] = _compute_tag_overlap(tags, all_docs_tags)
    else:
        body = text

    # Reference count (token-grep)
    signals["reference_count"] = _count_references(rel_path, corpus, abs_path)

    # Cited paths alive ratio
    signals["cited_paths_alive"] = _compute_cited_paths_ratio(body, repo_root)

    return signals


# ── Classification (AC-007) ──────────────────────────────────────────────

def classify(signals: dict, repo_root: str, pin_paths: list, rel_path: str) -> str:
    """Apply KEEP/REMOVE/REVIEW classification rules.

    Safety invariant: absence of superseded_by AND distilled_to NEVER
    classifies as REMOVE. The 24 cold-start docs must classify as KEEP or
    REVIEW only on day 1 (AC-007 hard rule).
    """
    # Hardcoded KEEP: pinned or referenced — overrides everything
    if _is_pinned(rel_path, pin_paths):
        return KEEP
    if signals["reference_count"] > 0:
        return KEEP

    # REMOVE conditions — require explicit frontmatter signal.
    # Checked before freshness-based KEEP: superseded_by/distilled_to take
    # priority over freshness (the doc is explicitly marked for removal).
    superseded_by = signals.get("superseded_by")
    distilled_to = signals.get("distilled_to")

    if superseded_by:
        target = os.path.join(repo_root, superseded_by)
        if os.path.isfile(target):
            return REMOVE
        # target missing → fail-safe to REVIEW (AC-007 CL-06)
        return REVIEW

    if distilled_to:
        target = os.path.join(repo_root, distilled_to)
        if os.path.isfile(target) and signals["reference_count"] == 0:
            # CL-07: restrict trust — only if target is under doc/
            if distilled_to.startswith("doc/") or distilled_to.startswith("doc" + os.sep):
                return REMOVE
        return REVIEW

    # KEEP: freshness current + most cited paths alive (no explicit removal signal)
    freshness = signals["freshness"]
    if freshness == "current" and signals["cited_paths_alive"] >= 0.8:
        return KEEP

    # Hard safety rule: no new frontmatter → never REMOVE alone.
    # Stale without explicit superseded_by/distilled_to → REVIEW only.
    return REVIEW


# ── Archive (AC-008) ─────────────────────────────────────────────────────

def _git_sha7(repo_root: str) -> str:
    """Get short git SHA (7 chars)."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=repo_root,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _is_dirty(abs_path: str, repo_root: str) -> bool:
    """Check if file has uncommitted changes."""
    try:
        rel = os.path.relpath(abs_path, repo_root)
        r = subprocess.run(
            ["git", "status", "--porcelain", rel],
            capture_output=True, text=True, cwd=repo_root,
        )
        return bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def archive_file(abs_path: str, rel_path: str, repo_root: str) -> bool:
    """Archive a REMOVE-classified file via git mv to _archive/ subdir.

    Returns True on success, False on any failure (fail-safe).
    """
    # AC-008: guard against _archive/ recursion
    parts = rel_path.replace(os.sep, "/").split("/")
    if ARCHIVE_DIR_NAME in parts:
        print(f"[doc_hygiene] INFO: archive recursion guard — skipping {rel_path}")
        return False

    # AC-AR-05: dirty file → skip
    if _is_dirty(abs_path, repo_root):
        print(f"[doc_hygiene] INFO: dirty file — skipping archive of {rel_path}")
        return False

    src_dir = os.path.dirname(abs_path)
    archive_dir = os.path.join(src_dir, ARCHIVE_DIR_NAME)
    os.makedirs(archive_dir, exist_ok=True)

    basename = os.path.basename(abs_path)
    dest_path = os.path.join(archive_dir, basename)

    # Collision handling (AC-008): append timestamp suffix
    if os.path.exists(dest_path):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name, ext = os.path.splitext(basename)
        dest_path = os.path.join(archive_dir, f"{name}.archived-{ts}{ext}")

    # AC-AR-04: refuse to move outside the doc dir's own _archive/
    abs_dest = os.path.abspath(dest_path)
    abs_src_parent = os.path.abspath(src_dir)
    expected_archive = os.path.join(abs_src_parent, ARCHIVE_DIR_NAME)
    if not abs_dest.startswith(expected_archive):
        print(f"[doc_hygiene] INFO: archive path traversal guard — refusing {rel_path}")
        return False

    # Use git mv via subprocess (AC-008)
    rel_src = os.path.relpath(abs_path, repo_root)
    rel_dest = os.path.relpath(dest_path, repo_root)

    try:
        r = subprocess.run(
            ["git", "mv", rel_src, rel_dest],
            capture_output=True, text=True, cwd=repo_root,
        )
        if r.returncode != 0:
            print(f"[doc_hygiene] INFO: git mv failed for {rel_path}: {r.stderr.strip()}")
            return False
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[doc_hygiene] INFO: git mv exception for {rel_path}: {exc}")
        return False

    # Restore command embedded in commit message (AC-008)
    restore_cmd = f"python3 plugin/scripts/maintain_restore.py {rel_dest}"
    commit_msg = (
        f"hygiene: archive {rel_src}\n\n"
        f"Auto-archived by doc_hygiene.py (content-signal classification: REMOVE).\n"
        f"Restore: {restore_cmd}"
    )
    try:
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, cwd=repo_root,
        )
    except (OSError, subprocess.SubprocessError):
        pass

    print(f"[doc_hygiene] archived: {rel_src} -> {rel_dest}")
    print(f"[doc_hygiene] restore:  {restore_cmd}")
    return True


# ── REVIEW queue (AC-009) ────────────────────────────────────────────────

def append_review_entry(rel_path: str, signals: dict, repo_root: str) -> None:
    """Append a REVIEW entry to .maintain-pending.json (atomic write)."""
    pending_path = os.path.join(repo_root, PENDING_JSON)
    existing = read_json_state(pending_path)
    if not isinstance(existing, list):
        existing = []

    # Remove any existing entry for this path to avoid duplicates
    existing = [e for e in existing if e.get("path") != rel_path]

    entry = {
        "path": rel_path,
        "kind": "review",
        "signals": {
            "reference_count": signals.get("reference_count", 0),
            "freshness": signals.get("freshness", "current"),
            "cited_paths_alive": signals.get("cited_paths_alive", 1.0),
            "superseded_by": signals.get("superseded_by"),
            "distilled_to": signals.get("distilled_to"),
            "tag_overlap": signals.get("tag_overlap", 0.0),
        },
        "added_at": now_iso(),
    }
    existing.append(entry)
    write_json_state(pending_path, existing)


# ── All-doc tags collection (for tag_overlap) ────────────────────────────

def _collect_all_tags(scan_dirs_abs: list) -> list:
    """Collect tags arrays from all doc files for tag_overlap computation."""
    all_tags = []
    for scan_dir in scan_dirs_abs:
        if not os.path.isdir(scan_dir):
            continue
        for dirpath, _dirs, filenames in os.walk(scan_dir):
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                abs_p = os.path.join(dirpath, fn)
                try:
                    with open(abs_p, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read(2000)
                    fm, _, _ = split_frontmatter(text)
                    if fm:
                        tags = read_array_field(fm, "tags")
                        all_tags.append(tags)
                except OSError:
                    pass
    return all_tags


# ── Main scan entrypoint ─────────────────────────────────────────────────

def run_scan(repo_root: str, dry_run: bool = False) -> dict:
    """Run the full doc hygiene scan.

    Returns dict with counts: kept, removed, reviewed, errors.
    dry_run=True: classify only, no archive or queue writes.
    """
    cfg = _load_hygiene_config(repo_root)
    if not cfg["enabled"]:
        print("[doc_hygiene] disabled via hygiene.yaml")
        return {"kept": 0, "removed": 0, "reviewed": 0, "errors": 0}

    pin_paths = cfg["pin_paths"]

    scan_dirs_abs = [os.path.join(repo_root, d) for d in SCAN_DIRS]
    corpus = _gather_search_corpus(repo_root)
    all_docs_tags = _collect_all_tags(scan_dirs_abs)

    counts = {"kept": 0, "removed": 0, "reviewed": 0, "errors": 0}

    for scan_dir_abs in scan_dirs_abs:
        if not os.path.isdir(scan_dir_abs):
            continue
        for dirpath, dirs, filenames in os.walk(scan_dir_abs):
            # AC-005 HARD-exclude doc/harness/patterns/
            abs_dirpath = os.path.abspath(dirpath)
            patterns_abs = os.path.join(repo_root, PATTERNS_DIR_SUFFIX)
            if abs_dirpath == os.path.abspath(patterns_abs) or \
               abs_dirpath.startswith(os.path.abspath(patterns_abs) + os.sep):
                dirs[:] = []
                continue
            # Skip _archive/ dirs
            if ARCHIVE_DIR_NAME in os.path.relpath(dirpath, repo_root).split(os.sep):
                dirs[:] = []
                continue

            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fn)
                rel_path = os.path.relpath(abs_path, repo_root)

                try:
                    signals = _compute_signals(abs_path, rel_path, repo_root,
                                               corpus, all_docs_tags)
                    verdict = classify(signals, repo_root, pin_paths, rel_path)

                    if verdict == KEEP:
                        counts["kept"] += 1
                    elif verdict == REMOVE:
                        if dry_run:
                            print(f"[doc_hygiene] DRY-RUN REMOVE: {rel_path}")
                            counts["removed"] += 1
                        else:
                            ok = archive_file(abs_path, rel_path, repo_root)
                            if ok:
                                counts["removed"] += 1
                            else:
                                counts["errors"] += 1
                    elif verdict == REVIEW:
                        counts["reviewed"] += 1
                        if not dry_run:
                            append_review_entry(rel_path, signals, repo_root)
                        else:
                            print(f"[doc_hygiene] DRY-RUN REVIEW: {rel_path}")
                except Exception as exc:
                    print(f"[doc_hygiene] INFO: error processing {rel_path}: {exc}")
                    counts["errors"] += 1

    return counts


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Content-signal doc hygiene scanner")
    p.add_argument("--repo-root", default=None, help="Repo root (default: auto-detect)")
    p.add_argument("--dry-run", action="store_true", help="Classify only, no writes")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    repo_root = args.repo_root or find_repo_root()
    counts = run_scan(repo_root, dry_run=args.dry_run)

    if not args.quiet:
        print(f"[doc_hygiene] done: kept={counts['kept']} removed={counts['removed']} "
              f"reviewed={counts['reviewed']} errors={counts['errors']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[doc_hygiene] fatal: {exc}", file=sys.stderr)
        sys.exit(0)
