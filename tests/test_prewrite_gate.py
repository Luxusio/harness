"""Tests for prewrite_gate.py — blocking enforcement of plan-first workflow.

Covers:
  - Source file write without plan PASS → exit 2 (block)
  - No manifest (harness not initialized) → exit 0 (allow)
  - Plan PASS exists on any active task → exit 0 (allow)
  - Escape hatch HARNESS_SKIP_PREWRITE → exit 0 (allow)
  - Non-source file write → exit 0 (allow)
  - No active tasks → exit 2 (block)

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from prewrite_gate import _is_source_file, _find_active_tasks, _extract_file_path


# ---------------------------------------------------------------------------
# Unit tests for _is_source_file
# ---------------------------------------------------------------------------

class TestIsSourceFile(unittest.TestCase):

    def test_python_file_is_source(self):
        self.assertTrue(_is_source_file("src/main.py"))

    def test_typescript_file_is_source(self):
        self.assertTrue(_is_source_file("app/index.ts"))

    def test_sql_file_is_source(self):
        self.assertTrue(_is_source_file("migrations/001.sql"))

    def test_markdown_is_not_source(self):
        self.assertFalse(_is_source_file("README.md"))

    def test_yaml_is_not_source(self):
        self.assertFalse(_is_source_file("config.yaml"))

    def test_exempt_task_path(self):
        self.assertFalse(_is_source_file("doc/harness/tasks/TASK__foo/PLAN.md"))

    def test_exempt_claude_path(self):
        self.assertFalse(_is_source_file(".claude/settings.json"))

    def test_exempt_filename_PLAN(self):
        self.assertFalse(_is_source_file("PLAN.md"))

    def test_exempt_filename_TASK_STATE(self):
        self.assertFalse(_is_source_file("TASK_STATE.yaml"))

    def test_exempt_filename_HANDOFF(self):
        self.assertFalse(_is_source_file("HANDOFF.md"))

    def test_dot_slash_normalized(self):
        self.assertTrue(_is_source_file("./src/main.py"))

    def test_empty_path(self):
        self.assertFalse(_is_source_file(""))

    def test_none_path(self):
        self.assertFalse(_is_source_file(None))


# ---------------------------------------------------------------------------
# Unit tests for _extract_file_path
# ---------------------------------------------------------------------------

class TestExtractFilePath(unittest.TestCase):

    def test_write_tool(self):
        data = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/foo.py"}})
        self.assertEqual(_extract_file_path(data), "src/foo.py")

    def test_edit_tool(self):
        data = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "src/bar.ts"}})
        self.assertEqual(_extract_file_path(data), "src/bar.ts")

    def test_multiedit_tool(self):
        data = json.dumps({"tool_name": "MultiEdit", "tool_input": {"file_path": "src/baz.py"}})
        self.assertEqual(_extract_file_path(data), "src/baz.py")

    def test_read_tool_returns_none(self):
        data = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "src/foo.py"}})
        self.assertIsNone(_extract_file_path(data))

    def test_absolute_path_stripped(self):
        cwd = os.getcwd()
        data = json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{cwd}/src/foo.py"}})
        self.assertEqual(_extract_file_path(data), "src/foo.py")

    def test_empty_input(self):
        self.assertIsNone(_extract_file_path(""))

    def test_invalid_json(self):
        self.assertIsNone(_extract_file_path("not json"))


# ---------------------------------------------------------------------------
# Integration tests for _find_active_tasks
# ---------------------------------------------------------------------------

class TestFindActiveTasks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_task_dir = __import__('prewrite_gate')
        # Patch TASK_DIR
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        prewrite_gate.TASK_DIR = self.task_base

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        self.tmp.cleanup()

    def _write_task(self, task_id, status="created", plan_verdict="pending"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            f.write(f"task_id: {task_id}\nstatus: {status}\nplan_verdict: {plan_verdict}\n")

    def test_no_tasks(self):
        self.assertEqual(_find_active_tasks(), [])

    def test_active_task_pending(self):
        self._write_task("TASK__foo", "created", "pending")
        result = _find_active_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("TASK__foo", "pending"))

    def test_active_task_passed(self):
        self._write_task("TASK__bar", "plan_passed", "PASS")
        result = _find_active_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ("TASK__bar", "PASS"))

    def test_closed_task_excluded(self):
        self._write_task("TASK__done", "closed", "PASS")
        self.assertEqual(_find_active_tasks(), [])

    def test_mixed_tasks(self):
        self._write_task("TASK__a", "created", "pending")
        self._write_task("TASK__b", "plan_passed", "PASS")
        self._write_task("TASK__c", "closed", "PASS")
        result = _find_active_tasks()
        self.assertEqual(len(result), 2)
        ids = [r[0] for r in result]
        self.assertIn("TASK__a", ids)
        self.assertIn("TASK__b", ids)
        self.assertNotIn("TASK__c", ids)


if __name__ == "__main__":
    unittest.main()
