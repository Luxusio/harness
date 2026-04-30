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
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

VALID_STATUS = {"open", "implemented_candidate", "passed", "failed", "deferred"}

# kinds gated by the feature/functional test-evidence rule. Bugfix is gated by the
# separate Iron Law (root_cause); doc / verification produce no functional code so
# they skip the evidence requirement. Missing kind defaults to 'unknown' and skips
# (preserves backward-compat with legacy CHECKS.yaml that pre-dates the kind field).
TEST_EVIDENCE_KINDS = {"feature", "functional"}
TEST_EVIDENCE_GATED_STATUSES = {"implemented_candidate", "passed"}
NO_TEST_REQUIRED_REASON_MAX = 400
LEARNINGS_REL = "doc/harness/learnings.jsonl"


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


def _repo_root_from_checks(checks_path: str) -> str:
    """Walk up from the CHECKS.yaml location looking for the harness manifest.

    The manifest is the canonical repo root marker. Falls back to the directory
    two levels above CHECKS.yaml (doc/harness/tasks/<id>/CHECKS.yaml) if no
    manifest is found — this preserves behavior for fixture / test setups where
    the manifest is intentionally absent.
    """
    cur = os.path.dirname(os.path.abspath(checks_path))
    for _ in range(8):
        if os.path.isfile(os.path.join(cur, "doc", "harness", "manifest.yaml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # Fallback: <repo>/doc/harness/tasks/<id>/CHECKS.yaml -> <repo>
    task_dir = os.path.dirname(os.path.abspath(checks_path))
    return os.path.abspath(os.path.join(task_dir, "..", "..", "..", ".."))


def _validate_test_evidence_path(evidence: str, repo_root: str) -> str:
    """Resolve evidence to an absolute path; reject symlinks, missing files, and
    paths that escape repo_root. Returns the resolved real path on success.

    Raises ValueError with a distinct message per failure mode (see error catalog
    in plugin/CLAUDE.md §9).
    """
    if not evidence or not evidence.strip():
        raise ValueError("--test-evidence value is empty.")
    candidate = evidence.strip()
    abs_candidate = (
        candidate if os.path.isabs(candidate) else os.path.join(repo_root, candidate)
    )
    if os.path.islink(abs_candidate):
        raise ValueError(
            f"Iron Law violation: --test-evidence path resolves to a symlink and "
            f"cannot be accepted as test evidence. Supply the real file path "
            f"directly.\n  Path: {abs_candidate}\n"
            f"Symlinks are rejected to prevent evidence pointing outside the "
            f"repository."
        )
    real = os.path.realpath(abs_candidate)
    real_root = os.path.realpath(repo_root)
    if not (real == real_root or real.startswith(real_root + os.sep)):
        raise ValueError(
            f"Iron Law violation: --test-evidence path resolves outside repo_root.\n"
            f"  Resolved: {real}\n  repo_root: {real_root}\n"
            f"Both relative and absolute paths are accepted, but they must "
            f"resolve inside the repository."
        )
    if not os.path.isfile(real):
        raise ValueError(
            f"Iron Law violation: --test-evidence path does not exist on disk.\n"
            f"  Resolved: {real}\n"
            f"Verify the path is relative to the repo root or supply an absolute "
            f"path. Both relative and absolute paths are accepted."
        )
    return real


def _suggest_test_evidence(repo_root: str, ac_id: str) -> str | None:
    """Best-effort: scan tests/ for files matching the AC id (e.g. test_ac_001__*.py).

    Returns one path (relative to repo_root) on a unique match, otherwise None.
    Used to enrich the missing-evidence error so the gate becomes a helpful nudge
    instead of a bare rejection.
    """
    m = re.match(r"^AC-(\d+)$", ac_id.strip())
    if not m:
        return None
    n = int(m.group(1))
    pat = re.compile(rf"^test_ac_{n:03d}__")
    tests_root = os.path.join(repo_root, "tests")
    matches: list[str] = []
    if os.path.isdir(tests_root):
        for dirpath, _dirs, files in os.walk(tests_root):
            for f in files:
                if pat.match(f):
                    matches.append(
                        os.path.relpath(os.path.join(dirpath, f), repo_root)
                    )
                    if len(matches) > 2:
                        return None  # ambiguous
    if len(matches) == 1:
        return matches[0]
    return None


def _log_bypass(repo_root: str, ac_id: str, reason: str) -> None:
    """Append a one-line JSON event to learnings.jsonl. Best-effort; never raises."""
    try:
        path = os.path.join(repo_root, LEARNINGS_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "ts": now_iso(),
            "type": "test-evidence-bypass",
            "source": "update_checks",
            "ac": ac_id,
            "reason": reason,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def update_check(
    checks_path: str,
    ac_id: str,
    status: str,
    evidence: str | None = None,
    note: str | None = None,
    root_cause: str | None = None,
    test_evidence: str | None = None,
    no_test_required: str | None = None,
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
    kind_raw = (_field_value(block, "kind") or "").strip().strip('"').strip("'")
    kind = kind_raw if kind_raw else "unknown"
    if kind == "bugfix" and status in ("implemented_candidate", "passed"):
        existing_rc = (_field_value(block, "root_cause") or "").strip().strip('"').strip("'")
        incoming_rc = (root_cause or "").strip()
        if not existing_rc and not incoming_rc:
            raise ValueError(
                f"Iron Law violation: AC '{ac_id}' is kind=bugfix and cannot be "
                f"promoted to '{status}' without root_cause. Use --root-cause "
                f'"<one-line confirmed cause>".'
            )

    # Test-Evidence Gate: feature / functional ACs require evidence (or explicit bypass)
    # before promotion to implemented_candidate / passed. Mirrors Iron Law shape.
    if kind in TEST_EVIDENCE_KINDS and status in TEST_EVIDENCE_GATED_STATUSES:
        existing_ev = (_field_value(block, "evidence") or "").strip().strip('"').strip("'")
        incoming_ev = (test_evidence or "").strip()
        repo_root = _repo_root_from_checks(checks_path)
        bypass_reason = (no_test_required or "").strip()

        if no_test_required is not None:
            if not bypass_reason:
                raise ValueError(
                    "Iron Law violation: --no-test-required requires a non-empty "
                    "reason string.\n"
                    "Provide a one-line explanation of why no test file exists "
                    "for this AC.\n"
                    'Example: --no-test-required "UI-only change, covered by '
                    'existing snapshot suite"\n'
                    "Reason is stored in CHECKS.yaml evidence field and appears "
                    "in the audit log."
                )
            if len(bypass_reason) > NO_TEST_REQUIRED_REASON_MAX:
                raise ValueError(
                    f"Iron Law violation: --no-test-required reason exceeds "
                    f"{NO_TEST_REQUIRED_REASON_MAX} char cap "
                    f"(got {len(bypass_reason)})."
                )
            _log_bypass(repo_root, ac_id, bypass_reason)
            # Bypass overwrites evidence with the documented reason for audit trail.
            evidence = f"BYPASS: {bypass_reason}"
        elif incoming_ev:
            resolved = _validate_test_evidence_path(incoming_ev, repo_root)
            evidence = os.path.relpath(resolved, repo_root)
        elif not existing_ev:
            suggestion = _suggest_test_evidence(repo_root, ac_id)
            sug_line = (
                f"\n  Suggested: --test-evidence {suggestion}\n"
                f"  (found in tests/ matching this AC id)"
                if suggestion else ""
            )
            raise ValueError(
                f"Iron Law violation: AC '{ac_id}' is kind={kind} and cannot be "
                f"promoted to '{status}' without test evidence. Use "
                f"--test-evidence <path> pointing to a regression test file "
                f"(must exist, no symlinks), or use --no-test-required "
                f'"<reason>" to bypass with a documented justification.\n'
                f"  Example: --test-evidence tests/regression/task_xx/test_ac_001__behavior.py\n"
                f"  ACs without kind: field are not gated by this rule."
                f"{sug_line}"
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
    p.add_argument("--test-evidence", default=None, dest="test_evidence",
                   help="Path to a regression test file covering this AC. "
                        "Required for kind in {feature, functional} on promotion to "
                        "implemented_candidate / passed. Path must exist, must not be "
                        "a symlink, and must resolve inside repo_root.")
    p.add_argument("--no-test-required", default=None, dest="no_test_required",
                   help="Bypass the test-evidence gate with a non-empty reason "
                        f"(<= {NO_TEST_REQUIRED_REASON_MAX} chars). Each bypass is "
                        "logged to doc/harness/learnings.jsonl as type=test-evidence-bypass.")
    args = p.parse_args()

    checks = os.path.join(os.path.abspath(args.task_dir), "CHECKS.yaml")
    try:
        result = update_check(
            checks, args.ac, args.status, args.evidence, args.note, args.root_cause,
            test_evidence=args.test_evidence, no_test_required=args.no_test_required,
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
