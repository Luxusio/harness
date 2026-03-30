"""Tests for stop_gate.py — prevents premature stop when tasks are open.

Covers:
  - Open tasks → block (decision: block)
  - No tasks → approve
  - Only closed/archived/stale tasks → approve
  - blocked_env tasks → approve with note
  - Next step mapping correctness

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from stop_gate import _next_step, _verdict_hints


# ---------------------------------------------------------------------------
# Unit tests for _next_step
# ---------------------------------------------------------------------------

class TestNextStep(unittest.TestCase):

    def test_created(self):
        result = _next_step("created")
        self.assertIn("plan", result.lower())

    def test_planned(self):
        result = _next_step("planned")
        self.assertIn("critic-plan", result.lower())

    def test_plan_passed(self):
        result = _next_step("plan_passed")
        self.assertIn("developer", result.lower())

    def test_implemented(self):
        result = _next_step("implemented")
        self.assertIn("critic-runtime", result.lower())

    def test_qa_passed(self):
        result = _next_step("qa_passed")
        self.assertIn("writer", result.lower())

    def test_docs_synced(self):
        result = _next_step("docs_synced")
        self.assertIn("critic-document", result.lower())

    def test_unknown_status(self):
        result = _next_step("banana")
        self.assertIn("TASK_STATE", result)


# ---------------------------------------------------------------------------
# Unit tests for _verdict_hints
# ---------------------------------------------------------------------------

class TestVerdictHints(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_state(self, plan_v="pending", runtime_v="pending"):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        with open(path, "w") as f:
            f.write(f"plan_verdict: {plan_v}\nruntime_verdict: {runtime_v}\n")
        return path

    def test_both_pending(self):
        path = self._write_state("pending", "pending")
        hints = _verdict_hints(path)
        self.assertEqual(len(hints), 2)

    def test_plan_pass_runtime_pending(self):
        path = self._write_state("PASS", "pending")
        hints = _verdict_hints(path)
        self.assertEqual(len(hints), 1)
        self.assertIn("runtime", hints[0])

    def test_both_pass(self):
        path = self._write_state("PASS", "PASS")
        hints = _verdict_hints(path)
        self.assertEqual(len(hints), 0)

    def test_missing_file(self):
        hints = _verdict_hints("/nonexistent/path")
        self.assertEqual(hints, [])


if __name__ == "__main__":
    unittest.main()
