#!/usr/bin/env python3
"""Generic categorized audit framework (CSO-derived, domain-agnostic).

Categories are user-defined under `audit_categories:` in manifest.yaml. Each
category lists checks; each check has a name, command, severity (low/med/high),
and confidence threshold (0-10). Findings are appended to per-category JSONL
files at `doc/harness/audits/<category>-history.jsonl`.

Manifest example:
  audit_categories:
    security:
      - name: secrets-scan
        command: "git secrets --scan || true"
        severity: high
        min_confidence: 8
    accessibility:
      - name: axe-cli
        command: "npx axe-cli ./public"
        severity: med
        min_confidence: 6

A check command exits 0 = clean (no finding); non-zero = finding (severity
applies). Stdout last 10 lines captured as evidence.

NOTE: In each check entry, `name` MUST be the first field on the `- ` line.
The YAML mini-parser is order-dependent — subsequent fields (command, severity,
min_confidence) are parsed from indented continuation lines.

Findings JSONL line:
  {ts, category, check, severity, confidence, evidence_tail, branch}

Invocation:
  python3 audit.py --category security
  python3 audit.py --list                    # list configured categories
  python3 audit.py --recent security 5

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
DIR = "doc/harness/audits"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_categories(repo_root: str) -> dict[str, list[dict]]:
    path = os.path.join(repo_root, MANIFEST)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(
        r"^audit_categories:\s*\n((?:[ \t]+\S.*\n(?:[ \t]+.*\n)*)+)",
        text,
        re.MULTILINE,
    )
    if not m:
        return {}
    out: dict[str, list[dict]] = {}
    cur_cat: str | None = None
    cur_check: dict = {}

    def flush() -> None:
        nonlocal cur_check
        if cur_cat and cur_check:
            out.setdefault(cur_cat, []).append(cur_check)
        cur_check = {}

    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        cat_m = re.match(r"^\s{2}(\w[\w-]*):\s*$", ln)
        chk_start = re.match(r"^\s+-\s+(\w+):\s*(.*)$", ln)
        chk_field = re.match(r"^\s{6,}(\w+):\s*(.*)$", ln)
        if cat_m:
            flush()
            cur_cat = cat_m.group(1)
            continue
        if chk_start:
            flush()
            key, val = chk_start.group(1), chk_start.group(2).strip().strip('"').strip("'")
            cur_check = {key: val}
            continue
        if chk_field:
            cur_check[chk_field.group(1)] = chk_field.group(2).strip().strip('"').strip("'")
    flush()

    for cat, checks in out.items():
        for c in checks:
            try:
                c["min_confidence"] = int(c.get("min_confidence", 5))
            except (ValueError, TypeError):
                c["min_confidence"] = 5
            c.setdefault("severity", "med")
    return out


def _run(cmd: str, cwd: str, timeout: int = 600) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.SubprocessError as e:
        return False, f"<subprocess error: {e}>"
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.splitlines()[-10:])
    return r.returncode == 0, tail


def run_category(repo_root: str, category: str) -> int:
    cats = _parse_categories(repo_root)
    if category not in cats:
        print(f"ERROR: category '{category}' not in manifest audit_categories", file=sys.stderr)
        return 1
    checks = cats[category]
    if not checks:
        print(f"NOTE: category '{category}' has no checks")
        return 0

    branch = ""
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repo_root,
            capture_output=True, text=True, timeout=3,
        )
        branch = r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        pass

    out_path = os.path.join(repo_root, DIR, f"{category}-history.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    findings = 0
    print(f"audit category: {category}")
    for c in checks:
        name = c.get("name") or c.get("check") or "<unnamed>"
        cmd = c.get("command", "")
        if not cmd:
            print(f"  {name:30s} SKIP (no command)")
            continue
        clean, tail = _run(cmd, repo_root)
        status = "CLEAN" if clean else "FINDING"
        sev = c.get("severity", "med")
        conf = c.get("min_confidence", 5)
        print(f"  {name:30s} sev={sev:4s} conf>={conf}  {status}")
        if not clean:
            findings += 1
            line = {
                "ts": _now(), "branch": branch or "unknown",
                "category": category, "check": name,
                "severity": sev, "confidence": conf,
                "evidence_tail": tail,
            }
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

    print(f"findings: {findings}")
    return 0 if findings == 0 else 2


def list_categories(repo_root: str) -> None:
    cats = _parse_categories(repo_root)
    if not cats:
        print("(no audit_categories in manifest)")
        return
    for cat, checks in cats.items():
        print(f"{cat}: {len(checks)} check(s)")
        for c in checks:
            print(f"  - {c.get('name', '<unnamed>')} (sev={c.get('severity', 'med')})")


def show_recent(repo_root: str, category: str, n: int) -> None:
    path = os.path.join(repo_root, DIR, f"{category}-history.jsonl")
    if not os.path.isfile(path):
        print(f"(no history for category '{category}')")
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for ln in lines[-n:]:
        try:
            obj = json.loads(ln)
            print(f"{obj['ts']}  sev={obj['severity']:4s}  {obj['check']}")
        except (json.JSONDecodeError, KeyError):
            print(ln.rstrip())


def main() -> int:
    p = argparse.ArgumentParser(description="Generic categorized audit")
    p.add_argument("--category", help="Category to audit")
    p.add_argument("--list", action="store_true", help="List configured categories")
    p.add_argument("--recent", nargs=2, metavar=("CATEGORY", "N"),
                   help="Show last N entries for a category")
    args = p.parse_args()
    repo_root = find_repo_root()
    if args.list:
        list_categories(repo_root)
        return 0
    if args.recent:
        show_recent(repo_root, args.recent[0], int(args.recent[1]))
        return 0
    if args.category:
        return run_category(repo_root, args.category)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
