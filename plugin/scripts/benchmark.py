#!/usr/bin/env python3
"""Numeric-metric benchmark snapshot, printed to stdout.

Reads `benchmark_components:` from manifest — each entry: {name, command,
unit, lower_is_better}. The command MUST print a single numeric value
(int/float) on its last non-empty stdout line. Anything else is treated as
fail.

Invocation:
  python3 benchmark.py                  # run + print metrics

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

MANIFEST = "doc/harness/manifest.yaml"


def _read_components(repo_root: str) -> list[dict]:
    path = os.path.join(repo_root, MANIFEST)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(
        r"^benchmark_components:\s*\n((?:\s+-.*\n(?:\s{4,}.*\n)*)+)",
        text,
        re.MULTILINE,
    )
    if not m:
        return []
    items: list[dict] = []
    cur: dict = {}
    for ln in m.group(1).splitlines():
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
        it.setdefault("unit", "")
        v = str(it.get("lower_is_better", "true")).lower()
        it["lower_is_better"] = v not in ("false", "0", "no")
    return items


def _run_metric(cmd: str, cwd: str, timeout: int = 600) -> float | None:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.SubprocessError:
        return None
    if r.returncode != 0:
        return None
    for ln in reversed(r.stdout.splitlines()):
        ln = ln.strip()
        if not ln:
            continue
        try:
            return float(ln)
        except ValueError:
            return None
    return None


def run(repo_root: str) -> int:
    components = _read_components(repo_root)
    if not components:
        print("NOTE: no benchmark_components in manifest — declare under "
              "benchmark_components: with name/command/unit/lower_is_better")
        return 1

    metrics: dict[str, float] = {}
    failed = False
    for c in components:
        val = _run_metric(c["command"], repo_root)
        if val is None:
            failed = True
            continue
        metrics[c["name"]] = val
    print("benchmark metrics:")
    for c in components:
        n = c["name"]
        v = metrics.get(n, "FAIL")
        print(f"  {n:20s} value={v} unit={c['unit']:>4}")
    return 2 if failed else 0


def main() -> int:
    argparse.ArgumentParser(description="Numeric benchmark snapshot").parse_args()
    repo_root = find_repo_root()
    return run(repo_root)


if __name__ == "__main__":
    sys.exit(main())
