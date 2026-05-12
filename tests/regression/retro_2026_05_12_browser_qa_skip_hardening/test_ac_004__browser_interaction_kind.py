"""AC-004 — browser_interaction kind + owner=qa-browser promotion gate.

Covers:
  - kind=browser_interaction + owner=developer + status=passed → ValueError.
  - kind=browser_interaction + owner=qa-browser + valid evidence → success.
  - kind=browser_interaction + --test-evidence=CRITIC__qa.md (no qa-browser header) → ValueError.
  - kind=browser_interaction + --test-evidence=CRITIC__qa.md (with qa-browser header) → success.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UPDATE = REPO / "plugin" / "scripts" / "update_checks.py"


def _setup_fake_task(td_root: str):
    """Build a fake repo with a CHECKS.yaml containing 2 browser_interaction ACs."""
    os.makedirs(os.path.join(td_root, "doc", "harness", "tasks", "TASK__demo"), exist_ok=True)
    os.makedirs(os.path.join(td_root, "tests", "regression", "demo"), exist_ok=True)
    # Real test file for the standard evidence path
    with open(os.path.join(td_root, "tests", "regression", "demo", "test_ac_001__demo.py"),
              "w", encoding="utf-8") as f:
        f.write("# placeholder regression test\n")
    # Two ACs: AC-001 with owner=developer, AC-002 with owner=qa-browser
    checks_body = (
        "- id: AC-001\n"
        "  title: bad owner\n"
        "  status: implemented_candidate\n"
        "  kind: browser_interaction\n"
        "  owner: developer\n"
        "  completeness: 7\n"
        "  reopen_count: 0\n"
        "  last_updated: 2026-05-12T00:00:00Z\n"
        "  evidence: tests/regression/demo/test_ac_001__demo.py\n"
        "  note: \"\"\n"
        "- id: AC-002\n"
        "  title: good owner\n"
        "  status: implemented_candidate\n"
        "  kind: browser_interaction\n"
        "  owner: qa-browser\n"
        "  completeness: 7\n"
        "  reopen_count: 0\n"
        "  last_updated: 2026-05-12T00:00:00Z\n"
        "  evidence: tests/regression/demo/test_ac_001__demo.py\n"
        "  note: \"\"\n"
    )
    with open(os.path.join(td_root, "doc", "harness", "tasks", "TASK__demo", "CHECKS.yaml"),
              "w", encoding="utf-8") as f:
        f.write(checks_body)
    # Manifest (any content; not used for kind logic)
    with open(os.path.join(td_root, "doc", "harness", "manifest.yaml"),
              "w", encoding="utf-8") as f:
        f.write("name: demo\ntype: library\n")


def _run_update(td_root, *args):
    return subprocess.run(
        [sys.executable, str(UPDATE),
         "--task-dir", os.path.join(td_root, "doc", "harness", "tasks", "TASK__demo")]
        + list(args),
        capture_output=True, text=True,
    )


class TestBrowserInteractionKind(unittest.TestCase):
    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.td = self.td_obj.name
        _setup_fake_task(self.td)

    def tearDown(self):
        self.td_obj.cleanup()

    def test_passed_blocked_when_owner_is_developer(self):
        res = _run_update(self.td,
                          "--ac", "AC-001", "--status", "passed",
                          "--test-evidence", "tests/regression/demo/test_ac_001__demo.py")
        self.assertNotEqual(res.returncode, 0, msg=res.stderr)
        self.assertIn("browser_interaction", res.stderr)
        self.assertIn("qa-browser", res.stderr)

    def test_passed_succeeds_when_owner_is_qa_browser(self):
        res = _run_update(self.td,
                          "--ac", "AC-002", "--status", "passed",
                          "--test-evidence", "tests/regression/demo/test_ac_001__demo.py")
        self.assertEqual(res.returncode, 0, msg=f"stderr: {res.stderr}\nstdout: {res.stdout}")

    def test_critic_qa_evidence_without_qa_browser_header_rejected(self):
        # Write a CRITIC__qa.md WITHOUT qa-browser header
        critic = os.path.join(self.td, "doc", "harness", "tasks", "TASK__demo", "CRITIC__qa.md")
        with open(critic, "w", encoding="utf-8") as f:
            f.write("# CRITIC — qa\n\n## qa-api verdict: PASS\n\nNo qa-browser section.\n")
        res = _run_update(self.td,
                          "--ac", "AC-002", "--status", "passed",
                          "--test-evidence", "doc/harness/tasks/TASK__demo/CRITIC__qa.md")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("qa-browser section", res.stderr)

    def test_critic_qa_evidence_with_qa_browser_header_accepted(self):
        critic = os.path.join(self.td, "doc", "harness", "tasks", "TASK__demo", "CRITIC__qa.md")
        with open(critic, "w", encoding="utf-8") as f:
            f.write("# CRITIC — qa\n\n## qa-browser verdict: PASS\nAll interactions verified.\n")
        res = _run_update(self.td,
                          "--ac", "AC-002", "--status", "passed",
                          "--test-evidence", "doc/harness/tasks/TASK__demo/CRITIC__qa.md")
        self.assertEqual(res.returncode, 0, msg=f"stderr: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
