"""Tests for CHECKS.yaml blocking in task_completed_gate.py.

Covers:
  - CHECKS.yaml with failed criteria → failure list includes CHECKS entry
  - CHECKS.yaml absent → no CHECKS-related failure
  - CHECKS.yaml with all passed → no CHECKS-related failure
  - CHECKS.yaml with planned/implemented_candidate → no CHECKS blocking (only failed blocks)
  - Multiple failed criteria → single failure with all IDs listed

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from task_completed_gate import compute_completion_failures, _parse_checks_yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_non_mutating_passing_task(task_dir):
    """Minimal passing non-mutating task (no runtime/doc requirements)."""
    _write(os.path.join(task_dir, "TASK_STATE.yaml"),
        "task_id: TASK__test\n"
        "status: implemented\n"
        "mutates_repo: false\n"
        "plan_verdict: PASS\n"
        "runtime_verdict: PASS\n"
        "document_verdict: skipped\n"
        "execution_mode: standard\n"
        "orchestration_mode: solo\n"
        "workflow_violations: []\n"
    )
    _write(os.path.join(task_dir, "PLAN.md"), "# Plan\n")
    _write(os.path.join(task_dir, "CRITIC__plan.md"), "verdict: PASS\n")
    _write(os.path.join(task_dir, "HANDOFF.md"),
           "# Handoff\n## Result\nfrom: dev\nscope: t\nchanges: x\nverification_inputs: y\nblockers: none\nnext_action: done\n")


# ---------------------------------------------------------------------------
# _parse_checks_yaml unit tests
# ---------------------------------------------------------------------------

class TestParseChecksYaml(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_single_criterion(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, 'checks:\n  - id: AC-001\n    title: "test"\n    status: failed\n')
        result = _parse_checks_yaml(path)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "AC-001")
        self.assertEqual(result[0]["status"], "failed")

    def test_parse_multiple_criteria(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path,
            'checks:\n'
            '  - id: AC-001\n    title: "first"\n    status: passed\n'
            '  - id: AC-002\n    title: "second"\n    status: failed\n'
            '  - id: AC-003\n    title: "third"\n    status: planned\n'
        )
        result = _parse_checks_yaml(path)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[1]["status"], "failed")

    def test_parse_empty_file(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, "checks: []\n")
        result = _parse_checks_yaml(path)
        self.assertEqual(result, [])

    def test_parse_missing_file(self):
        result = _parse_checks_yaml("/nonexistent/CHECKS.yaml")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# CHECKS.yaml blocking in compute_completion_failures
# ---------------------------------------------------------------------------

class TestChecksBlocking(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _has_checks_failure(self, failures):
        return any("CHECKS.yaml" in f for f in failures)

    def test_failed_criterion_blocks(self):
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "must pass"\n    status: failed\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(self._has_checks_failure(failures),
            f"Failed criterion must block. Got: {failures}")

    def test_no_checks_file_no_block(self):
        _make_non_mutating_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(self._has_checks_failure(failures),
            f"No CHECKS.yaml must not cause CHECKS blocking. Got: {failures}")

    def test_all_passed_no_block(self):
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "ok"\n    status: passed\n'
            '  - id: AC-002\n    title: "also ok"\n    status: passed\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(self._has_checks_failure(failures),
            f"All passed must not block. Got: {failures}")

    def test_planned_does_not_block(self):
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "pending"\n    status: planned\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(self._has_checks_failure(failures),
            f"Planned status must not trigger CHECKS blocking. Got: {failures}")

    def test_implemented_candidate_does_not_block(self):
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(self._has_checks_failure(failures),
            f"implemented_candidate must not trigger CHECKS blocking. Got: {failures}")

    def test_multiple_failed_lists_all_ids(self):
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n'
            '  - id: AC-001\n    title: "a"\n    status: failed\n'
            '  - id: AC-002\n    title: "b"\n    status: passed\n'
            '  - id: AC-003\n    title: "c"\n    status: failed\n')
        failures = compute_completion_failures(self.task_dir)
        checks_failures = [f for f in failures if "CHECKS.yaml" in f]
        self.assertEqual(len(checks_failures), 1, "Should be single CHECKS failure entry")
        self.assertIn("AC-001", checks_failures[0])
        self.assertIn("AC-003", checks_failures[0])
        self.assertNotIn("AC-002", checks_failures[0])


if __name__ == "__main__":
    unittest.main()
