#!/usr/bin/env python3
"""Composite project health score (0-10) with append-only history.

Reads `doc/harness/manifest.yaml` `health_components:` (optional) — a list of
{name, command, weight} entries. Falls back to a generic default that runs
`test_command` from manifest if no health_components declared.

Each component runs via shell. Exit 0 = full points for that component;
non-zero = 0. Composite = sum(weight_i * pass_i) normalized to 10.

Appends one JSONL line to `doc/harness/health-history.jsonl`:
    {ts, branch, score, components: {name: {pass, weight}}}

Stdlib only.

Invocation:
  python3 health.py              # run + append history line
  python3 health.py --dry-run    # run, print, do not append
  python3 health.py --recent 5   # show last 5 history entries (no run)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root, yaml_field

MANIFEST = "doc/harness/manifest.yaml"
HISTORY = "doc/harness/health-history.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_components(repo_root: str) -> list[dict]:
    """Parse `health_components:` block from manifest. Tolerant mini-YAML."""
    path = os.path.join(repo_root, MANIFEST)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^health_components:\s*\n((?:\s+-.*\n(?:\s{4,}.*\n)*)+)", text, re.MULTILINE)
    if not m:
        return []
    block = m.group(1)
    items = []
    cur: dict = {}
    for ln in block.splitlines():
        if re.match(r"^\s+-\s+", ln):
            if cur:
                items.append(cur)
            cur = {}
            ln = re.sub(r"^\s+-\s+", "", ln)
        kv = re.match(r"^\s*(\w+):\s*(.*)$", ln)
        if kv:
            cur[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    if cur:
        items.append(cur)
    for it in items:
        try:
            it["weight"] = max(0.0, float(it.get("weight", 1)))
        except (ValueError, TypeError):
            it["weight"] = 1.0
    return items


def _default_components(repo_root: str) -> list[dict]:
    test_cmd = yaml_field("test_command", os.path.join(repo_root, MANIFEST))
    if test_cmd:
        return [{"name": "test", "command": test_cmd, "weight": 1.0}]
    return []


def _run(cmd: str, cwd: str, timeout: int = 300) -> bool:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout
        )
        return r.returncode == 0
    except subprocess.SubprocessError:
        return False


def compute(repo_root: str) -> dict:
    components = _read_components(repo_root) or _default_components(repo_root)
    if not components:
        return {
            "score": None,
            "components": {},
            "note": "no health_components and no test_command — declare in manifest",
        }
    total_weight = sum(c["weight"] for c in components) or 1.0
    results: dict[str, dict] = {}
    weighted = 0.0
    for c in components:
        passed = _run(c["command"], repo_root)
        results[c["name"]] = {"pass": passed, "weight": c["weight"]}
        if passed:
            weighted += c["weight"]
    score = round(10.0 * weighted / total_weight, 2)
    return {"score": score, "components": results}


def append_history(repo_root: str, payload: dict) -> str:
    branch = ""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=repo_root, capture_output=True, text=True, timeout=3,
        )
        branch = r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        pass
    line = {
        "ts": _now(),
        "branch": branch or "unknown",
        "score": payload.get("score"),
        "components": payload.get("components"),
    }
    path = os.path.join(repo_root, HISTORY)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def show_recent(repo_root: str, n: int) -> None:
    path = os.path.join(repo_root, HISTORY)
    if not os.path.isfile(path):
        print("(no history)")
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for ln in lines[-n:]:
        try:
            obj = json.loads(ln)
            print(f"{obj['ts']}  branch={obj['branch']}  score={obj['score']}")
        except (json.JSONDecodeError, KeyError):
            print(ln.rstrip())


def main() -> int:
    p = argparse.ArgumentParser(description="Composite health score")
    p.add_argument("--dry-run", action="store_true", help="Compute but don't append")
    p.add_argument("--recent", type=int, default=0, help="Show last N entries (no run)")
    args = p.parse_args()

    repo_root = find_repo_root()
    if args.recent:
        show_recent(repo_root, args.recent)
        return 0

    payload = compute(repo_root)
    if payload.get("note"):
        print(f"NOTE: {payload['note']}")
        return 1

    score = payload["score"]
    print(f"health: {score}/10")
    for name, info in payload["components"].items():
        mark = "PASS" if info["pass"] else "FAIL"
        print(f"  {name:20s} weight={info['weight']:>4}  {mark}")

    if not args.dry_run:
        path = append_history(repo_root, payload)
        print(f"appended: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
