#!/usr/bin/env python3
"""Update one acceptance criterion in CHECKS.yaml.

Not plan-session-gated — intended for developer / qa agents after plan close.
Writes atomically. Stdlib only.

Schema per AC (extended from plan-time baseline):
  - id: AC-001
    title: "..."
    status: open | implemented_candidate | passed | failed | deferred
    kind: functional | verification | doc | ... | bugfix
    owner: developer | qa-browser | ...
    completeness: 0-10   # plan-time score, preserved round-trip (not mutated by this CLI)
    root_cause: "..."    # REQUIRED for kind=bugfix before promotion to implemented_candidate (Iron Law)
    reopen_count: 0
    last_updated: <ISO8601>
    evidence: "<file:line | test name | HANDOFF ref>"   # optional
    note: "<one-line note>"                             # optional

Transitions:
  * setting status=failed increments reopen_count
  * last_updated is always refreshed
  * evidence/note are replaced when provided, left alone otherwise
  * completeness is plan-owned — this CLI never mutates it, preserved via block round-trip
  * Iron Law: kind=bugfix AC blocked from implemented_candidate / passed unless root_cause is non-empty
    (set via --root-cause "<one-line confirmed cause>")
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

VALID_STATUS = {"open", "implemented_candidate", "passed", "failed", "deferred"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: str, content: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".checks.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _split_blocks(text: str) -> list[str]:
    """Split CHECKS.yaml body into AC blocks starting with '- id:' lines.

    Returns [preamble, block1, block2, ...]. Non-AC tail content is appended
    to the last block so it survives round-trip.
    """
    lines = text.splitlines(keepends=True)
    blocks: list[list[str]] = [[]]
    in_ac = False
    for ln in lines:
        if re.match(r"^\s*-\s+id:\s*", ln):
            blocks.append([ln])
            in_ac = True
        elif in_ac:
            blocks[-1].append(ln)
        else:
            blocks[-1].append(ln)
    return ["".join(b) for b in blocks]


def _ac_id(block: str) -> str | None:
    m = re.match(r"^\s*-\s+id:\s*(\S+)", block)
    return m.group(1).strip().strip('"').strip("'") if m else None


def _field_value(block: str, field: str) -> str | None:
    m = re.search(rf"^\s+{re.escape(field)}:\s*(.*)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None


def _set_field(block: str, field: str, value: str) -> str:
    pattern = rf"^(\s+){re.escape(field)}:\s*.*$"
    replacement = rf"\g<1>{field}: {value}"
    new_block, n = re.subn(pattern, replacement, block, count=1, flags=re.MULTILINE)
    if n:
        return new_block
    # Field missing — append with 2-space indent before the block's trailing newline.
    trailing = ""
    body = block
    if body.endswith("\n"):
        trailing = "\n"
        body = body[:-1]
    return f"{body}\n  {field}: {value}{trailing}"


def _yaml_quote(s: str) -> str:
    if s == "":
        return '""'
    if re.search(r"[:#\n\"']", s) or s.strip() != s:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def update_check(
    checks_path: str,
    ac_id: str,
    status: str,
    evidence: str | None = None,
    note: str | None = None,
    root_cause: str | None = None,
) -> dict:
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status '{status}' — must be one of {sorted(VALID_STATUS)}")
    if not os.path.isfile(checks_path):
        raise FileNotFoundError(checks_path)

    with open(checks_path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = _split_blocks(text)
    target_idx = None
    for i, blk in enumerate(blocks):
        if _ac_id(blk) == ac_id:
            target_idx = i
            break
    if target_idx is None:
        raise KeyError(f"AC '{ac_id}' not found in {checks_path}")

    block = blocks[target_idx]
    prior = _field_value(block, "status")
    prior_reopen = _field_value(block, "reopen_count") or "0"
    try:
        reopen = int(prior_reopen)
    except ValueError:
        reopen = 0
    if status == "failed" and prior != "failed":
        reopen += 1

    # Iron Law: bugfix ACs require root_cause before non-open status
    kind = (_field_value(block, "kind") or "").strip()
    if kind == "bugfix" and status in ("implemented_candidate", "passed"):
        existing_rc = (_field_value(block, "root_cause") or "").strip().strip('"').strip("'")
        incoming_rc = (root_cause or "").strip()
        if not existing_rc and not incoming_rc:
            raise ValueError(
                f"Iron Law violation: AC '{ac_id}' is kind=bugfix and cannot be "
                f"promoted to '{status}' without root_cause. Use --root-cause "
                f'"<one-line confirmed cause>".'
            )

    block = _set_field(block, "status", status)
    block = _set_field(block, "reopen_count", str(reopen))
    block = _set_field(block, "last_updated", now_iso())
    if evidence is not None:
        block = _set_field(block, "evidence", _yaml_quote(evidence))
    if note is not None:
        block = _set_field(block, "note", _yaml_quote(note))
    if root_cause is not None:
        block = _set_field(block, "root_cause", _yaml_quote(root_cause))

    blocks[target_idx] = block
    _atomic_write(checks_path, "".join(blocks))

    return {
        "ac": ac_id,
        "prior_status": prior,
        "status": status,
        "reopen_count": reopen,
        "path": checks_path,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Update a single AC in CHECKS.yaml")
    p.add_argument("--task-dir", required=True)
    p.add_argument("--ac", required=True, help="Acceptance criterion id (e.g. AC-001)")
    p.add_argument("--status", required=True, choices=sorted(VALID_STATUS))
    p.add_argument("--evidence", default=None, help="One-line evidence (file:line, test name, HANDOFF ref)")
    p.add_argument("--note", default=None, help="Free-form note")
    p.add_argument("--root-cause", default=None, dest="root_cause",
                   help="Confirmed root cause (Iron Law: required for kind=bugfix promotion)")
    args = p.parse_args()

    checks = os.path.join(os.path.abspath(args.task_dir), "CHECKS.yaml")
    try:
        result = update_check(
            checks, args.ac, args.status, args.evidence, args.note, args.root_cause
        )
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(
        f"{result['ac']}: {result['prior_status']} -> {result['status']} "
        f"(reopen_count={result['reopen_count']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
