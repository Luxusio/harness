#!/usr/bin/env python3
"""Golden replay regression tests for harness scripts.

Stdlib only. Runs a fixed set of known-good inputs through the harness
scripts and compares outputs against expected snapshots. Exit 0 on
all-pass, 1 on any regression.

Covered today:
  1. contract_lint.py — the shipped template must lint clean.
  2. update_checks.py — AC lifecycle transitions are deterministic.
  3. note_freshness.py — current → suspect flip on path match.
  4. contract_lint.py --check-weight — flags over-budget SKILL.md files.

Invoke:
  python3 plugin/scripts/golden_replay.py           # all tests
  python3 plugin/scripts/golden_replay.py -v        # verbose
  python3 plugin/scripts/golden_replay.py --only update_checks  # single

Used by: CI / pre-release smoke / manual regression check after script
edits. Never invoked from a hook (slow, writes tmp files).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
TEMPLATES = os.path.join(ROOT, "skills", "setup", "templates")


class TestResult:
    def __init__(self, name: str, ok: bool, msg: str = ""):
        self.name = name
        self.ok = ok
        self.msg = msg

    def __str__(self) -> str:
        tag = "PASS" if self.ok else "FAIL"
        return f"[{tag}] {self.name}" + (f" — {self.msg}" if self.msg else "")


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30)


def test_contract_lint_template() -> TestResult:
    """Shipped CONTRACTS.md template must lint clean."""
    tmpl = os.path.join(TEMPLATES, "CONTRACTS.md")
    if not os.path.isfile(tmpl):
        return TestResult("contract_lint_template", False, f"template missing: {tmpl}")
    r = _run(["python3", os.path.join(SCRIPTS, "contract_lint.py"),
              "--path", tmpl, "--repo-root", ROOT])
    if r.returncode != 0:
        return TestResult("contract_lint_template", False,
                          f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
    return TestResult("contract_lint_template", True)


def test_update_checks_lifecycle() -> TestResult:
    """AC lifecycle: open -> implemented_candidate -> passed (reopen_count stays 0)."""
    with tempfile.TemporaryDirectory() as td:
        task_dir = os.path.join(td, "task")
        os.makedirs(task_dir)
        checks = os.path.join(task_dir, "CHECKS.yaml")
        with open(checks, "w") as f:
            f.write(
                "- id: AC-001\n"
                "  title: test ac\n"
                "  status: open\n"
                "  kind: functional\n"
                "  owner: developer\n"
                "  reopen_count: 0\n"
                "  last_updated: 2026-01-01T00:00:00Z\n"
                "  evidence: ''\n"
                "  note: ''\n"
            )

        for status, evidence in [("implemented_candidate", "pending"),
                                 ("passed", "test_x passes")]:
            r = _run(["python3", os.path.join(SCRIPTS, "update_checks.py"),
                      "--task-dir", task_dir, "--ac", "AC-001",
                      "--status", status, "--evidence", evidence])
            if r.returncode != 0:
                return TestResult("update_checks_lifecycle", False,
                                  f"{status} failed: {r.stderr.strip()[:200]}")

        body = open(checks).read()
        if "status: passed" not in body:
            return TestResult("update_checks_lifecycle", False,
                              "final status not 'passed'")
        if "reopen_count: 0" not in body:
            return TestResult("update_checks_lifecycle", False,
                              "reopen_count drifted from 0 on clean path")

    return TestResult("update_checks_lifecycle", True)


def test_update_checks_reopen() -> TestResult:
    """passed -> failed must increment reopen_count."""
    with tempfile.TemporaryDirectory() as td:
        task_dir = os.path.join(td, "task")
        os.makedirs(task_dir)
        checks = os.path.join(task_dir, "CHECKS.yaml")
        with open(checks, "w") as f:
            f.write(
                "- id: AC-002\n"
                "  title: reopen ac\n"
                "  status: passed\n"
                "  kind: functional\n"
                "  owner: developer\n"
                "  reopen_count: 0\n"
                "  last_updated: 2026-01-01T00:00:00Z\n"
                "  evidence: ''\n"
                "  note: ''\n"
            )

        r = _run(["python3", os.path.join(SCRIPTS, "update_checks.py"),
                  "--task-dir", task_dir, "--ac", "AC-002",
                  "--status", "failed", "--note", "regressed"])
        if r.returncode != 0:
            return TestResult("update_checks_reopen", False, r.stderr.strip()[:200])
        body = open(checks).read()
        if "reopen_count: 1" not in body:
            return TestResult("update_checks_reopen", False,
                              f"reopen_count did not increment; body=\n{body}")

    return TestResult("update_checks_reopen", True)


def test_note_freshness_flip() -> TestResult:
    """Note with matching invalidated_by_paths flips current -> suspect."""
    with tempfile.TemporaryDirectory() as td:
        note_dir = os.path.join(td, "doc")
        os.makedirs(note_dir)
        note = os.path.join(note_dir, "example.md")
        with open(note, "w") as f:
            f.write(
                "---\n"
                "freshness: current\n"
                "invalidated_by_paths:\n"
                "  - src/changed.py\n"
                "---\n"
                "body\n"
            )

        r = _run(["python3", os.path.join(SCRIPTS, "note_freshness.py"),
                  "--paths", "src/changed.py",
                  "--doc-root", note_dir, "--quiet"])
        if r.returncode != 0:
            return TestResult("note_freshness_flip", False,
                              f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
        body = open(note).read()
        if "freshness: suspect" not in body:
            return TestResult("note_freshness_flip", False,
                              "freshness did not flip to 'suspect'")

    return TestResult("note_freshness_flip", True)


def test_check_weight_flags_oversized() -> TestResult:
    """--check-weight should flag at least one SKILL.md >500 lines when any exist."""
    with tempfile.TemporaryDirectory() as td:
        fake_plugin = os.path.join(td, "plugin")
        os.makedirs(os.path.join(fake_plugin, "skills", "big"))
        skill = os.path.join(fake_plugin, "skills", "big", "SKILL.md")
        with open(skill, "w") as f:
            f.write("\n".join(f"line {i}" for i in range(600)))

        # Need a CONTRACTS.md for lint to run at all — use shipped template.
        tmpl = os.path.join(TEMPLATES, "CONTRACTS.md")
        r = _run(["python3", os.path.join(SCRIPTS, "contract_lint.py"),
                  "--path", tmpl, "--repo-root", ROOT,
                  "--check-weight", "--plugin-root", fake_plugin])
        if r.returncode != 0:
            return TestResult("check_weight_flags_oversized", False,
                              f"exit={r.returncode} stderr={r.stderr.strip()[:200]}")
        if "C-13 weight" not in r.stdout:
            return TestResult("check_weight_flags_oversized", False,
                              f"C-13 weight warning missing from stdout:\n{r.stdout}")

    return TestResult("check_weight_flags_oversized", True)


TESTS = [
    test_contract_lint_template,
    test_update_checks_lifecycle,
    test_update_checks_reopen,
    test_note_freshness_flip,
    test_check_weight_flags_oversized,
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="Run only the test matching this substring")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    results = []
    for fn in TESTS:
        if args.only and args.only not in fn.__name__:
            continue
        try:
            res = fn()
        except Exception as e:
            res = TestResult(fn.__name__, False, f"exception: {e}")
        results.append(res)
        if args.verbose or not res.ok:
            print(res)

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    print(f"\ngolden_replay: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
