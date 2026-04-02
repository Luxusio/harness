"""Tests for WS-2: Planning mode defaults and helper.

Covers:
  - get_planning_mode() returns 'standard' by default
  - get_planning_mode() reads 'broad-build' when set
  - get_planning_mode() handles missing file gracefully
  - get_planning_mode() handles missing field → 'standard'
  - get_planning_mode() handles unknown values → 'standard'
  - legacy task bootstrap helper includes planning_mode field

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import compile_routing, get_planning_mode, yaml_field


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


class TestPlanningModeAutoPromotion(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_task(self, task_id="TASK__auto", lane="build", browser_required="false"):
        task_dir = os.path.join(self.tmp.name, task_id)
        os.makedirs(task_dir, exist_ok=True)
        _write(
            os.path.join(task_dir, "TASK_STATE.yaml"),
            "task_id: {task_id}\n"
            "status: created\n"
            "lane: {lane}\n"
            "planning_mode: standard\n"
            "browser_required: {browser_required}\n"
            "runtime_verdict_fail_count: 0\n"
            "risk_tags: []\n"
            "updated: 2026-01-01T00:00:00Z\n".format(
                task_id=task_id,
                lane=lane,
                browser_required=browser_required,
            ),
        )
        return task_dir

    def _write_request(self, task_dir, body):
        _write(
            os.path.join(task_dir, "REQUEST.md"),
            "# Request: TASK__auto\n"
            "created: 2026-01-01T00:00:00Z\n\n"
            f"{body}\n",
        )

    def test_compile_routing_promotes_broad_build_from_request_md(self):
        task_dir = self._make_task(browser_required="true")
        self._write_request(
            task_dir,
            "Create a new admin dashboard web app for customer operations. "
            "Show key metrics, recent alerts, and a detail workflow.",
        )

        routing = compile_routing(task_dir)
        self.assertEqual(routing["planning_mode"], "broad-build")

    def test_compile_routing_promotes_broad_build_for_korean_request(self):
        task_dir = self._make_task(browser_required="true")
        self._write_request(
            task_dir,
            "새로운 관리자 대시보드 웹앱을 만들어줘. 고객 상태와 주요 지표를 한 화면에서 볼 수 있게 해줘.",
        )

        routing = compile_routing(task_dir)
        self.assertEqual(routing["planning_mode"], "broad-build")

    def test_compile_routing_keeps_standard_for_bugfix_request(self):
        task_dir = self._make_task(browser_required="true")
        self._write_request(
            task_dir,
            "Fix the failing /api/users endpoint in api/routes/users.py and add a regression test.",
        )

        routing = compile_routing(task_dir)
        self.assertEqual(routing["planning_mode"], "standard")

    def test_compile_routing_keeps_standard_for_detailed_spec(self):
        task_dir = self._make_task(browser_required="true")
        self._write_request(
            task_dir,
            "Create a dashboard. Use app/routes/dashboard.tsx, api/routes/metrics.py, "
            "and db/schema.sql. Add three cards, one detail table, and a CSV export button.",
        )

        routing = compile_routing(task_dir)
        self.assertEqual(routing["planning_mode"], "standard")

    def test_existing_plan_preserves_standard_mode(self):
        task_dir = self._make_task(browser_required="true")
        self._write_request(
            task_dir,
            "Create a new admin dashboard web app for customer operations.",
        )
        _write(os.path.join(task_dir, "PLAN.md"), "# Plan\n\nExisting contract.\n")

        routing = compile_routing(task_dir)
        self.assertEqual(routing["planning_mode"], "standard")


class TestTaskCreatedGatePlanningMode(unittest.TestCase):
    """Verify that the legacy task bootstrap helper includes planning_mode in TASK_STATE.yaml."""

    def test_task_state_template_has_planning_mode(self):
        """The legacy task bootstrap helper should include planning_mode: standard."""
        import task_created_gate
        import inspect
        source = inspect.getsource(task_created_gate.main)
        self.assertIn("planning_mode:", source,
            "task_created_gate.py must include planning_mode field in TASK_STATE.yaml template")


if __name__ == "__main__":
    unittest.main()
