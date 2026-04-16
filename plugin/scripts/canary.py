#!/usr/bin/env python3
"""Visual regression canary — baseline manifest + diff orchestrator.

Stores per-task PNG baselines under `doc/harness/visual-baselines/<task-id>/`
plus a `manifest.json` mapping page slug -> {url, captured_at, file, sha256}.
Screenshot capture is delegated to the calling agent (Chrome DevTools MCP) —
this script manages the baseline state and computes byte-level diffs.

Workflow:
  1. Pre-change: agent captures screenshots via MCP, writes PNG bytes to
     baselines dir, then calls:
       python3 canary.py register --task TASK__xxx --page home --url http://... --file PATH.png
  2. Post-change: agent re-captures into a candidates dir, then:
       python3 canary.py compare --task TASK__xxx --candidates DIR
     Output verdict: MATCH | CHANGED | NEW | MISSING per page.

Pixel-precise diffs require PIL (optional). Without PIL, falls back to sha256
byte equality.

Stdlib only (PIL optional).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root

DIR = "doc/harness/visual-baselines"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _task_dir(repo_root: str, task: str) -> str:
    return os.path.join(repo_root, DIR, task)


def _manifest_path(task_dir: str) -> str:
    return os.path.join(task_dir, "manifest.json")


def _load(manifest_path: str) -> dict:
    if not os.path.isfile(manifest_path):
        return {"pages": {}}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"pages": {}}


def _save(manifest_path: str, data: dict) -> None:
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def register(repo_root: str, task: str, page: str, url: str, file_path: str) -> int:
    task_dir = _task_dir(repo_root, task)
    os.makedirs(task_dir, exist_ok=True)
    src = os.path.abspath(file_path)
    if not os.path.isfile(src):
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        return 1
    dst = os.path.join(task_dir, f"{page}.png")
    if src != dst:
        with open(src, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
    sha = _sha256(dst)
    mp = _manifest_path(task_dir)
    data = _load(mp)
    data["pages"][page] = {
        "url": url, "file": f"{page}.png",
        "captured_at": _now(), "sha256": sha,
    }
    _save(mp, data)
    print(f"registered: {task}/{page} -> {dst}")
    return 0


def _pixel_diff_ratio(a_path: str, b_path: str) -> float | None:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    try:
        a = Image.open(a_path).convert("RGB")
        b = Image.open(b_path).convert("RGB")
    except Exception:
        return None
    if a.size != b.size:
        return 1.0
    pa, pb = a.load(), b.load()
    w, h = a.size
    diff = 0
    for y in range(h):
        for x in range(w):
            if pa[x, y] != pb[x, y]:
                diff += 1
    return diff / float(w * h)


def compare(repo_root: str, task: str, candidates: str) -> int:
    task_dir = _task_dir(repo_root, task)
    mp = _manifest_path(task_dir)
    data = _load(mp)
    pages = data.get("pages", {})
    if not pages:
        print(f"NOTE: no baseline for task '{task}'", file=sys.stderr)
        return 1
    candidates_dir = os.path.abspath(candidates)
    if not os.path.isdir(candidates_dir):
        print(f"ERROR: candidates dir not found: {candidates_dir}", file=sys.stderr)
        return 1

    worst = "MATCH"
    order = {"MATCH": 0, "NEW": 1, "MISSING": 2, "CHANGED": 3}
    for page, info in pages.items():
        cand = os.path.join(candidates_dir, info["file"])
        base = os.path.join(task_dir, info["file"])
        if not os.path.isfile(cand):
            print(f"  {page:30s} MISSING (no candidate {info['file']})")
            if order["MISSING"] > order[worst]:
                worst = "MISSING"
            continue
        cand_sha = _sha256(cand)
        if cand_sha == info["sha256"]:
            print(f"  {page:30s} MATCH")
            continue
        ratio = _pixel_diff_ratio(base, cand)
        if ratio is None:
            print(f"  {page:30s} CHANGED (sha mismatch, no PIL for pixel ratio)")
        else:
            print(f"  {page:30s} CHANGED (pixel diff {ratio:.2%})")
        if order["CHANGED"] > order[worst]:
            worst = "CHANGED"

    extras = []
    if os.path.isdir(candidates_dir):
        baselined = {info["file"] for info in pages.values()}
        for fn in os.listdir(candidates_dir):
            if fn.endswith(".png") and fn not in baselined:
                extras.append(fn)
    for fn in extras:
        print(f"  {fn:30s} NEW (not in baseline)")
        if order["NEW"] > order[worst]:
            worst = "NEW"

    print(f"verdict: {worst}")
    return 0 if worst == "MATCH" else 2


def main() -> int:
    p = argparse.ArgumentParser(description="Visual regression canary")
    sub = p.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser("register", help="Add a page to the baseline")
    reg.add_argument("--task", required=True)
    reg.add_argument("--page", required=True, help="Slug (alphanumeric + -)")
    reg.add_argument("--url", required=True)
    reg.add_argument("--file", required=True, help="Path to PNG to register")

    cmp_ = sub.add_parser("compare", help="Compare candidates dir against baseline")
    cmp_.add_argument("--task", required=True)
    cmp_.add_argument("--candidates", required=True, help="Dir of candidate PNGs")

    args = p.parse_args()
    repo_root = find_repo_root()
    if args.cmd == "register":
        return register(repo_root, args.task, args.page, args.url, args.file)
    if args.cmd == "compare":
        return compare(repo_root, args.task, args.candidates)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
