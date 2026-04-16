#!/usr/bin/env python3
"""Weekly engineering retrospective from harness state.

Combines git log, learnings.jsonl, health-history.jsonl, and timeline.jsonl
over a configurable period (default 7 days) into a structured report.

Sections:
  1. Commits — count, authors, top changed files
  2. Tasks — completed tasks from timeline (skill=run, event=completed)
  3. Learnings — new entries by type, key highlights
  4. Health trend — score trajectory from health-history.jsonl
  5. Patterns — what repeated, what improved, what regressed

Output: stdout (markdown). Optionally append to doc/harness/retros/<date>.md.

Invocation:
  python3 retro.py                          # last 7 days, stdout
  python3 retro.py --days 14                # last 14 days
  python3 retro.py --save                   # also write to doc/harness/retros/

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

LEARNINGS = "doc/harness/learnings.jsonl"
HEALTH = "doc/harness/health-history.jsonl"
TIMELINE = "doc/harness/timeline.jsonl"
RETROS = "doc/harness/retros"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _load_jsonl_since(path: str, since: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entry = json.loads(ln)
                if (entry.get("ts") or "") >= since:
                    results.append(entry)
            except json.JSONDecodeError:
                pass
    return results


def _section_commits(repo_root: str, days: int) -> str:
    since = f"{days} days ago"
    log = _git(["log", f"--since={since}", "--oneline", "--no-merges"], repo_root)
    lines = [ln for ln in log.splitlines() if ln.strip()] if log else []
    if not lines:
        return "## Commits\n\n(none in this period)\n"

    authors_raw = _git(
        ["log", f"--since={since}", "--format=%aN", "--no-merges"], repo_root
    )
    authors = Counter(a.strip() for a in authors_raw.splitlines() if a.strip())

    files_raw = _git(
        ["log", f"--since={since}", "--name-only", "--format=", "--no-merges"], repo_root
    )
    files = Counter(f.strip() for f in files_raw.splitlines() if f.strip())
    top_files = files.most_common(5)

    parts = [f"## Commits\n\n- **{len(lines)}** commits"]
    if authors:
        parts.append(f"- Authors: {', '.join(f'{a} ({c})' for a, c in authors.most_common(5))}")
    if top_files:
        parts.append("- Most changed files:")
        for fp, cnt in top_files:
            parts.append(f"  - `{fp}` ({cnt})")
    return "\n".join(parts) + "\n"


def _section_tasks(entries: list[dict]) -> str:
    completed = [
        e for e in entries
        if e.get("event") == "completed" and e.get("skill") in ("run", "develop")
    ]
    if not completed:
        return "## Tasks\n\n(no completed tasks in this period)\n"

    parts = [f"## Tasks\n\n- **{len(completed)}** task cycle(s) completed"]
    for e in completed[-5:]:
        outcome = e.get("outcome", "?")
        branch = e.get("branch", "?")
        dur = e.get("duration_s", "?")
        parts.append(f"  - branch={branch} outcome={outcome} duration={dur}s")
    return "\n".join(parts) + "\n"


def _section_learnings(entries: list[dict]) -> str:
    if not entries:
        return "## Learnings\n\n(none in this period)\n"

    by_type = Counter(e.get("type", "unknown") for e in entries)
    parts = [f"## Learnings\n\n- **{len(entries)}** new entries"]
    parts.append(f"- By type: {', '.join(f'{t} ({c})' for t, c in by_type.most_common())}")

    eurekas = [e for e in entries if e.get("type") == "eureka"]
    if eurekas:
        parts.append("- Eureka discoveries:")
        for e in eurekas[:3]:
            parts.append(f"  - `{e.get('key', '?')}`: {e.get('insight', '')}")

    key_counts = Counter(e.get("key", "") for e in entries if e.get("key"))
    repeated = [(k, c) for k, c in key_counts.most_common(5) if c >= 2]
    if repeated:
        parts.append("- Repeated keys (promotion candidates):")
        for k, c in repeated:
            parts.append(f"  - `{k}` ({c}x)")

    return "\n".join(parts) + "\n"


def _section_health(entries: list[dict]) -> str:
    if not entries:
        return "## Health Trend\n\n(no health data in this period)\n"

    scores = [e.get("score") for e in entries if e.get("score") is not None]
    if not scores:
        return "## Health Trend\n\n(no valid scores)\n"

    first, last = scores[0], scores[-1]
    delta = last - first
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    parts = [
        f"## Health Trend\n",
        f"- **{len(scores)}** measurements",
        f"- First: {first}/10 → Last: {last}/10 ({delta:+.1f} {arrow})",
        f"- Range: {min(scores):.1f} – {max(scores):.1f}",
    ]
    return "\n".join(parts) + "\n"


def generate(repo_root: str, days: int) -> str:
    since = _cutoff(days)
    timeline = _load_jsonl_since(os.path.join(repo_root, TIMELINE), since)
    learnings = _load_jsonl_since(os.path.join(repo_root, LEARNINGS), since)
    health = _load_jsonl_since(os.path.join(repo_root, HEALTH), since)

    header = f"# Retro — {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (last {days} days)\n"
    sections = [
        header,
        _section_commits(repo_root, days),
        _section_tasks(timeline),
        _section_learnings(learnings),
        _section_health(health),
    ]
    return "\n".join(sections)


def main() -> int:
    p = argparse.ArgumentParser(description="Weekly engineering retrospective")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--save", action="store_true", help="Also write to doc/harness/retros/")
    args = p.parse_args()

    repo_root = find_repo_root()
    report = generate(repo_root, args.days)
    print(report)

    if args.save:
        retro_dir = os.path.join(repo_root, RETROS)
        os.makedirs(retro_dir, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(retro_dir, f"{date_str}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nsaved: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
