#!/usr/bin/env python3
"""Minimal plan artifact writer — self-contained, no plugin-legacy dependency.

Usage:
  python3 write_plan_artifact.py --task-dir <path> --artifact <type> --input <file>
                                  [--append] [--checks <file>] [--meta key=val ...]

Artifact types: plan, plan-meta, checks, audit

Validates PLAN_SESSION.json (state=open, phase=write, source=plan-skill) before writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

AUDIT_HEADER = (
    "| # | phase | decision | classification | principle | rationale | rejected_option |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: str, content: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _validate_task_dir(task_dir: str) -> None:
    if not os.path.isfile(os.path.join(task_dir, "TASK_STATE.yaml")):
        print(f"ERROR: '{task_dir}' is not a harness task directory", file=sys.stderr)
        sys.exit(1)


def _validate_session(task_dir: str) -> tuple[bool, str]:
    """Check PLAN_SESSION.json: state=write_open, phase=write, source=plan-skill."""
    token_path = os.path.join(task_dir, "PLAN_SESSION.json")
    if not os.path.isfile(token_path):
        return False, "PLAN_SESSION.json not found"
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, f"PLAN_SESSION.json unreadable: {e}"
    if token.get("state") != "write_open":
        return False, f"state={token.get('state')!r} (expected 'write_open')"
    if token.get("phase") != "write":
        return False, f"phase={token.get('phase')!r} (expected 'write')"
    if token.get("source") != "plan-skill":
        return False, f"source={token.get('source')!r} (expected 'plan-skill')"
    return True, ""


def _parse_meta(kv_list: list[str]) -> dict:
    out: dict = {}
    for kv in kv_list:
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_plan(task_dir: str, content: str, meta_kv: dict) -> dict:
    task_id = os.path.basename(os.path.abspath(task_dir))
    artifact_path = os.path.join(task_dir, "PLAN.md")
    meta_path = os.path.join(task_dir, "PLAN.meta.json")

    _atomic_write(artifact_path, content)

    meta: dict = {"artifact": "PLAN.md", "task_id": task_id,
                  "author_role": "plan-skill", "written_at": _now_iso()}
    if meta_kv:
        meta["plan_meta"] = meta_kv
    _atomic_write(meta_path, json.dumps(meta, indent=2) + "\n")

    return {"artifact": "plan", "task_id": task_id, "path": artifact_path}


def _write_plan_meta(task_dir: str, content: str, meta_kv: dict) -> dict:
    task_id = os.path.basename(os.path.abspath(task_dir))
    meta_path = os.path.join(task_dir, "PLAN.meta.json")

    meta: dict = {"artifact": "plan-meta", "task_id": task_id,
                  "author_role": "plan-skill", "written_at": _now_iso()}
    try:
        input_data = json.loads(content) if content.strip() else {}
        if input_data:
            meta["plan_meta"] = input_data
    except json.JSONDecodeError:
        pass
    if meta_kv:
        meta.setdefault("plan_meta", {}).update(meta_kv)
    _atomic_write(meta_path, json.dumps(meta, indent=2) + "\n")

    return {"artifact": "plan-meta", "task_id": task_id, "path": meta_path}


def _write_checks(task_dir: str, content: str) -> dict:
    task_id = os.path.basename(os.path.abspath(task_dir))
    artifact_path = os.path.join(task_dir, "CHECKS.yaml")
    _atomic_write(artifact_path, content)
    return {"artifact": "checks", "task_id": task_id, "path": artifact_path}


def _append_audit(task_dir: str, content: str) -> dict:
    task_id = os.path.basename(os.path.abspath(task_dir))
    artifact_path = os.path.join(task_dir, "AUDIT_TRAIL.md")

    if os.path.isfile(artifact_path):
        existing = _read(artifact_path)
    else:
        existing = ""

    first_line = existing.lstrip("\n").split("\n")[0] if existing.strip() else ""
    has_header = first_line.startswith("| # |")

    if not existing.strip():
        new_content = AUDIT_HEADER + content.rstrip("\n") + "\n"
    elif has_header:
        new_content = existing.rstrip("\n") + "\n" + content.rstrip("\n") + "\n"
    else:
        new_content = existing.rstrip("\n") + "\n\n" + AUDIT_HEADER + content.rstrip("\n") + "\n"

    _atomic_write(artifact_path, new_content)
    return {"artifact": "audit", "task_id": task_id, "path": artifact_path}


def main() -> int:
    p = argparse.ArgumentParser(description="Write plan artifacts (self-contained)")
    p.add_argument("--task-dir", required=True, help="Task directory path")
    p.add_argument("--artifact", required=True,
                   choices=["plan", "plan-meta", "checks", "audit"],
                   help="Artifact type to write")
    p.add_argument("--input", required=True,
                   help="Input file path (use - for stdin)")
    p.add_argument("--append", action="store_true",
                   help="Append mode (required for audit)")
    p.add_argument("--checks", default=None,
                   help="Checks file path (plan/plan-meta only)")
    p.add_argument("--meta", action="append", default=None,
                   help="key=value metadata pairs")
    args = p.parse_args()

    task_dir = os.path.abspath(args.task_dir)
    _validate_task_dir(task_dir)

    ok, reason = _validate_session(task_dir)
    if not ok:
        print(f"ERROR: plan session not write-open. {reason}", file=sys.stderr)
        sys.exit(1)

    if args.input == "-":
        content = sys.stdin.read()
    else:
        if not os.path.isfile(args.input):
            print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        content = _read(args.input)

    meta_kv = _parse_meta(args.meta or [])

    if args.artifact == "plan":
        result = _write_plan(task_dir, content, meta_kv)
        if args.checks:
            checks_content = _read(args.checks)
            _write_checks(task_dir, checks_content)
    elif args.artifact == "plan-meta":
        result = _write_plan_meta(task_dir, content, meta_kv)
        if args.checks:
            checks_content = _read(args.checks)
            _write_checks(task_dir, checks_content)
    elif args.artifact == "checks":
        result = _write_checks(task_dir, content)
    elif args.artifact == "audit":
        if not args.append:
            print("ERROR: --artifact audit requires --append flag", file=sys.stderr)
            sys.exit(1)
        result = _append_audit(task_dir, content)
    else:
        print(f"ERROR: unknown artifact type: {args.artifact}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
