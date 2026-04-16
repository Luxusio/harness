#!/usr/bin/env python3
"""Search doc/harness/learnings.jsonl with keyword + filters.

The Tier 3 learnings.jsonl is append-only and grows large. This grep-based
searcher surfaces relevant entries by keyword, type, skill, or recency.

JSONL line shape (per plugin/CLAUDE.md §12):
    {"ts": "...", "type": "...", "skill": "...", "branch": "...",
     "key": "...", "insight": "...", "source": "..."}

Invocation:
  python3 search_learnings.py KEYWORD [KEYWORD2 ...]
  python3 search_learnings.py --type operational
  python3 search_learnings.py --skill plan --recent 30
  python3 search_learnings.py --since 2026-01-01

All filters AND together. Output is one line per match: ts | type | skill | key.
Use --json to dump full entries.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

LEARNINGS = "doc/harness/learnings.jsonl"


def _matches(entry: dict, kws: list[str], filters: dict) -> bool:
    if filters.get("type") and entry.get("type") != filters["type"]:
        return False
    if filters.get("skill") and entry.get("skill") != filters["skill"]:
        return False
    if filters.get("since") and (entry.get("ts") or "") < filters["since"]:
        return False
    if kws:
        hay = " ".join(
            str(v) for v in entry.values() if isinstance(v, (str, int, float))
        ).lower()
        for k in kws:
            if k.lower() not in hay:
                return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Search Tier 3 learnings.jsonl")
    p.add_argument("keywords", nargs="*", help="Keywords (AND across)")
    p.add_argument("--type")
    p.add_argument("--skill")
    p.add_argument("--since", help="ISO date or datetime")
    p.add_argument("--recent", type=int, default=0, help="Cap output to last N matches")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    repo_root = find_repo_root()
    path = os.path.join(repo_root, LEARNINGS)
    if not os.path.isfile(path):
        print("(no learnings.jsonl)")
        return 0

    filters = {"type": args.type, "skill": args.skill, "since": args.since}
    matches: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entry = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if _matches(entry, args.keywords, filters):
                matches.append(entry)

    if args.recent and len(matches) > args.recent:
        matches = matches[-args.recent:]

    for entry in matches:
        if args.json:
            print(json.dumps(entry, ensure_ascii=False))
        else:
            ts = entry.get("ts", "?")
            typ = entry.get("type", "?")
            skl = entry.get("skill", "?")
            key = entry.get("key", "?")
            insight = entry.get("insight", "")
            print(f"{ts}  [{typ}/{skl}]  {key}: {insight}")

    if not matches:
        print("(no matches)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
