#!/usr/bin/env python3
"""Automate Tier 3 → Tier 2 learnings promotion + pruning.

Extracts the inline bash from self-improvement.md into a proper script.
Steps:
  1. Aggregate learnings.jsonl by key, count occurrences.
  2. Keys with count >= threshold → promote to doc/harness/patterns/<topic>.md.
  3. Prune promoted entries from learnings.jsonl.
  4. Prune stale entries (>90 days, keep eureka/calibration forever).
  5. Report Tier 2 → Tier 1 candidates (pattern docs referenced in 2+ tasks).

Invocation:
  python3 promote_learnings.py                     # full pipeline
  python3 promote_learnings.py --dry-run            # report what would happen
  python3 promote_learnings.py --threshold 3        # require 3+ occurrences

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

LEARNINGS = "doc/harness/learnings.jsonl"
PATTERNS_DIR = "doc/harness/patterns"
STALE_DAYS = 90
KEEP_TYPES = {"eureka", "confidence-calibration"}
DEFAULT_THRESHOLD = 2

TOPIC_MAP = {
    "test": "testing",
    "build": "build",
    "lint": "build",
    "typecheck": "build",
    "verify": "verification",
    "browser": "verification",
    "dev_command": "verification",
    "port": "build",
    "env": "build",
    "architecture": "architecture",
    "security": "security",
}


def _topic_for_key(key: str) -> str:
    key_lower = key.lower()
    for prefix, topic in TOPIC_MAP.items():
        if prefix in key_lower:
            return topic
    return "general"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_entries(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return entries


def _write_entries(path: str, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _append_pattern(patterns_dir: str, topic: str, key: str,
                    insight: str, count: int, dry_run: bool) -> str:
    path = os.path.join(patterns_dir, f"{topic}.md")
    header = f"# {topic.title()} Patterns\n\n| Pattern | Discovered | Source |\n|---------|------------|--------|\n"
    row = f"| {key} | {_now_iso()[:10]} | run-auto-promote |"
    detail = (
        f"\n## {key}\n\n{insight}\n\n"
        f"**Promoted from learnings:** {count} occurrences\n"
    )

    if dry_run:
        print(f"  [dry-run] would append to {path}: {key} ({count}x)")
        return path

    os.makedirs(patterns_dir, exist_ok=True)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if f"## {key}" in content:
            content = re.sub(
                rf"(## {re.escape(key)}\n\n).*?(\n\*\*Promoted.*?\n)",
                rf"\g<1>{insight}\n\n**Promoted from learnings:** {count} occurrences\n",
                content, count=1, flags=re.DOTALL,
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return path
        with open(path, "a", encoding="utf-8") as f:
            f.write(row + "\n")
            f.write(detail)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + row + "\n" + detail)
    return path


def _tier1_candidates(repo_root: str, patterns_dir: str) -> list[str]:
    # Proxy: count git commits touching pattern docs. Not a perfect match for
    # "referenced in 2+ tasks" (plugin/CLAUDE.md §11) but practical — a pattern
    # doc committed in separate tasks will have separate commits.
    if not os.path.isdir(patterns_dir):
        return []
    candidates = []
    for fn in os.listdir(patterns_dir):
        if not fn.endswith(".md"):
            continue
        rel = os.path.relpath(os.path.join(patterns_dir, fn), repo_root)
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", "--follow", "--", rel],
                capture_output=True, text=True, cwd=repo_root, timeout=5,
            )
            commits = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
            if len(commits) >= 2:
                candidates.append(fn)
        except (subprocess.SubprocessError, OSError):
            pass
    return candidates


def run(repo_root: str, threshold: int, dry_run: bool) -> int:
    learn_path = os.path.join(repo_root, LEARNINGS)
    patterns_dir = os.path.join(repo_root, PATTERNS_DIR)
    entries = _load_entries(learn_path)

    if not entries:
        print("(no learnings to process)")
        return 0

    # Step 1: Aggregate
    counts: Counter[str] = Counter()
    latest_insight: dict[str, str] = {}
    for e in entries:
        k = e.get("key", "")
        if k:
            counts[k] += 1
            latest_insight[k] = e.get("insight", "")

    promotable = {k for k, c in counts.items() if c >= threshold}
    print(f"learnings: {len(entries)} entries, {len(counts)} unique keys, {len(promotable)} promotable (threshold={threshold})")

    # Step 2: Promote
    for k in sorted(promotable):
        topic = _topic_for_key(k)
        _append_pattern(patterns_dir, topic, k, latest_insight[k], counts[k], dry_run)

    # Step 3: Prune promoted
    if promotable and not dry_run:
        remaining = [e for e in entries if e.get("key", "") not in promotable]
        pruned_count = len(entries) - len(remaining)
        entries = remaining
        print(f"pruned {pruned_count} promoted entries")
    elif promotable:
        print(f"  [dry-run] would prune {sum(counts[k] for k in promotable)} promoted entries")

    # Step 4: Prune stale
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_ids = set()
    for e in entries:
        ts = e.get("ts", "")
        if ts and ts < cutoff and e.get("type", "") not in KEEP_TYPES:
            stale_ids.add(id(e))
    if stale_ids and not dry_run:
        entries = [e for e in entries if id(e) not in stale_ids]
        print(f"pruned {len(stale_ids)} stale entries (>{STALE_DAYS} days)")
    elif stale_ids:
        print(f"  [dry-run] would prune {len(stale_ids)} stale entries")

    # Write back
    if not dry_run and (promotable or stale_ids):
        _write_entries(learn_path, entries)
        print(f"learnings.jsonl: {len(entries)} entries remaining")

    # Step 5: Tier 1 candidates
    candidates = _tier1_candidates(repo_root, patterns_dir)
    if candidates:
        print(f"Tier 1 candidates (referenced in 2+ commits): {', '.join(candidates)}")
        print("  → Promote key facts to project CLAUDE.md as one-liners")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Promote and prune Tier 3 learnings")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"Min occurrences for promotion (default {DEFAULT_THRESHOLD})")
    args = p.parse_args()
    return run(find_repo_root(), args.threshold, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
