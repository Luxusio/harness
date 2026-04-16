#!/usr/bin/env python3
"""Numeric-metric benchmark with baseline + regression thresholds.

Reads `benchmark_components:` from manifest — each entry: {name, command,
unit, lower_is_better}. The command MUST print a single numeric value
(int/float) on its last non-empty stdout line. Anything else is treated as
fail.

State files (gitignored, see .gitignore):
  doc/harness/benchmark/baseline.json   — current baseline metrics
  doc/harness/benchmark/history.jsonl   — append-only run history

Regression thresholds (gstack-derived defaults, override via flags):
  WARN  : ±20% from baseline
  REGR  : ±50% OR ±500ms (when unit is "ms") from baseline

Invocation:
  python3 benchmark.py                  # run + compare to baseline + log
  python3 benchmark.py --set-baseline   # run, replace baseline, log
  python3 benchmark.py --recent 5       # show last 5 history entries

Stdlib only.
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
from _lib import find_repo_root

MANIFEST = "doc/harness/manifest.yaml"
DIR = "doc/harness/benchmark"
BASELINE = "baseline.json"
HISTORY = "history.jsonl"

WARN_RATIO = 0.20
REGR_RATIO = 0.50
REGR_MS_DELTA = 500.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _classify(curr: float, base: float, unit: str, lower_is_better: bool) -> str:
    if base == 0:
        return "OK"
    delta = curr - base
    delta_signed_for_worse = delta if lower_is_better else -delta
    if delta_signed_for_worse <= 0:
        return "OK"
    ratio = abs(delta) / abs(base)
    if unit == "ms" and abs(delta) >= REGR_MS_DELTA:
        return "REGR"
    if ratio >= REGR_RATIO:
        return "REGR"
    if ratio >= WARN_RATIO:
        return "WARN"
    return "OK"


def run(repo_root: str, set_baseline: bool) -> int:
    components = _read_components(repo_root)
    if not components:
        print("NOTE: no benchmark_components in manifest — declare under "
              "benchmark_components: with name/command/unit/lower_is_better")
        return 1

    baseline_path = os.path.join(repo_root, DIR, BASELINE)
    history_path = os.path.join(repo_root, DIR, HISTORY)
    os.makedirs(os.path.dirname(baseline_path), exist_ok=True)

    baseline: dict[str, float] = {}
    if os.path.isfile(baseline_path) and not set_baseline:
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline = json.load(f).get("metrics", {})
        except (json.JSONDecodeError, OSError):
            baseline = {}

    metrics: dict[str, float] = {}
    verdicts: dict[str, str] = {}
    worst = "OK"
    for c in components:
        val = _run_metric(c["command"], repo_root)
        if val is None:
            verdicts[c["name"]] = "FAIL"
            worst = "REGR"
            continue
        metrics[c["name"]] = val
        if c["name"] in baseline:
            v = _classify(val, baseline[c["name"]], c["unit"], c["lower_is_better"])
        else:
            v = "BASE-INIT"
        verdicts[c["name"]] = v
        order = {"OK": 0, "BASE-INIT": 0, "WARN": 1, "REGR": 2, "FAIL": 2}
        if order[v] > order[worst]:
            worst = v

    print(f"benchmark verdict: {worst}")
    for c in components:
        n = c["name"]
        v = metrics.get(n, "—")
        b = baseline.get(n, "—")
        print(f"  {n:20s} now={v} baseline={b} unit={c['unit']:>4} -> {verdicts[n]}")

    branch = ""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repo_root,
            capture_output=True, text=True, timeout=3,
        )
        branch = r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        pass

    with open(history_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": _now(), "branch": branch or "unknown",
            "verdict": worst, "metrics": metrics, "verdicts": verdicts,
        }, ensure_ascii=False) + "\n")

    if set_baseline or not os.path.isfile(baseline_path):
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump({
                "ts": _now(), "branch": branch or "unknown", "metrics": metrics,
            }, f, indent=2)
        print(f"baseline written: {baseline_path}")

    return 0 if worst in ("OK", "BASE-INIT", "WARN") else 2


def show_recent(repo_root: str, n: int) -> None:
    path = os.path.join(repo_root, DIR, HISTORY)
    if not os.path.isfile(path):
        print("(no history)")
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for ln in lines[-n:]:
        try:
            obj = json.loads(ln)
            print(f"{obj['ts']}  {obj['verdict']:10s}  branch={obj['branch']}")
        except (json.JSONDecodeError, KeyError):
            print(ln.rstrip())


def main() -> int:
    p = argparse.ArgumentParser(description="Numeric benchmark vs baseline")
    p.add_argument("--set-baseline", action="store_true")
    p.add_argument("--recent", type=int, default=0)
    args = p.parse_args()
    repo_root = find_repo_root()
    if args.recent:
        show_recent(repo_root, args.recent)
        return 0
    return run(repo_root, args.set_baseline)


if __name__ == "__main__":
    sys.exit(main())
