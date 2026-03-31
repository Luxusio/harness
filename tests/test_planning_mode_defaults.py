"""Tests for WS-2: Planning mode defaults and helper.

Covers:
  - get_planning_mode() returns 'standard' by default
  - get_planning_mode() reads 'broad-build' when set
  - get_planning_mode() handles missing file gracefully
  - get_planning_mode() handles missing field → 'standard'
  - get_planning_mode() handles unknown values → 'standard'
  - task_created_gate includes planning_mode field

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import get_planning_mode, yaml_field


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TestGetPlanningMode(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_standard(self):
        result = get_planning_mode("/nonexistent/TASK_STATE.yaml")
        self.assertEqual(result, "standard")

    def test_none_path_returns_standard(self):
        result = get_planning_mode(None)
        self.assertEqual(result, "standard")

    def test_empty_string_returns_standard(self):
        result = get_planning_mode("")
        self.assertEqual(result, "standard")

    def test_no_planning_mode_field_returns_standard(self):
        """Legacy task without planning_mode → standard."""
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "task_id: TASK__test\n"
            "status: created\n"
            "execution_mode: standard\n"
        )
        result = get_planning_mode(path)
        self.assertEqual(result, "standard")

    def test_planning_mode_standard(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "task_id: TASK__test\n"
            "planning_mode: standard\n"
        )
        result = get_planning_mode(path)
        self.assertEqual(result, "standard")

    def test_planning_mode_broad_build(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "task_id: TASK__test\n"
            "planning_mode: broad-build\n"
        )
        result = get_planning_mode(path)
        self.assertEqual(result, "broad-build")

    def test_unknown_value_returns_standard(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "task_id: TASK__test\n"
            "planning_mode: something_else\n"
        )
        result = get_planning_mode(path)
        self.assertEqual(result, "standard")


class TestTaskCreatedGatePlanningMode(unittest.TestCase):
    """Verify that task_created_gate includes planning_mode in TASK_STATE.yaml."""

    def test_task_state_template_has_planning_mode(self):
        """The task_created_gate.py template should include planning_mode: standard."""
        import task_created_gate
        import inspect
        source = inspect.getsource(task_created_gate.main)
        self.assertIn("planning_mode:", source,
            "task_created_gate.py must include planning_mode field in TASK_STATE.yaml template")


if __name__ == "__main__":
    unittest.main()
