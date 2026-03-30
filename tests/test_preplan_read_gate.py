"""Tests for preplan_read_gate.py — blocking enforcement of pre-plan source reads.

Covers:
  - no task + source Read → block
  - task exists but no plan session + source Read → block
  - plan session context open + source Read → allow
  - plan PASS + source Read → allow
  - Bash `cat plugin/scripts/foo.py` pre-plan → block
  - manifest/CLAUDE read pre-plan → allow
  - .meta.json reads → allow
  - Non-harness repo (no manifest) → allow

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from preplan_read_gate import (
    _is_guarded_path,
    _is_always_allowed,
    _has_plan_access,
    _extract_read_target,
    _normalize_path,
    _check_bash_command,
)


# ---------------------------------------------------------------------------
# Unit tests for _is_always_allowed
# ---------------------------------------------------------------------------

class TestIsAlwaysAllowed(unittest.TestCase):

    def test_manifest_allowed(self):
        self.assertTrue(_is_always_allowed("doc/harness/manifest.yaml"))

    def test_root_claude_allowed(self):
        self.assertTrue(_is_always_allowed("CLAUDE.md"))

    def test_doc_claude_allowed(self):
        self.assertTrue(_is_always_allowed("doc/CLAUDE.md"))

    def test_doc_common_claude_allowed(self):
        self.assertTrue(_is_always_allowed("doc/common/CLAUDE.md"))

    def test_task_local_files_allowed(self):
        self.assertTrue(_is_always_allowed("doc/harness/tasks/TASK__foo/TASK_STATE.yaml"))
        self.assertTrue(_is_always_allowed("doc/harness/tasks/TASK__foo/REQUEST.md"))
        self.assertTrue(_is_always_allowed("doc/harness/tasks/TASK__foo/SESSION_HANDOFF.json"))

    def test_claude_dir_allowed(self):
        self.assertTrue(_is_always_allowed(".claude/settings.json"))

    def test_empty_allowed(self):
        self.assertTrue(_is_always_allowed(""))

    def test_source_file_not_allowed(self):
        self.assertFalse(_is_always_allowed("plugin/scripts/foo.py"))

    def test_agent_file_not_allowed(self):
        self.assertFalse(_is_always_allowed("plugin/agents/harness.md"))


# ---------------------------------------------------------------------------
# Unit tests for _is_guarded_path
# ---------------------------------------------------------------------------

class TestIsGuardedPath(unittest.TestCase):

    def test_plugin_script_guarded(self):
        self.assertTrue(_is_guarded_path("plugin/scripts/prewrite_gate.py"))

    def test_plugin_agent_guarded(self):
        self.assertTrue(_is_guarded_path("plugin/agents/developer.md"))

    def test_plugin_skill_guarded(self):
        self.assertTrue(_is_guarded_path("plugin/skills/plan/SKILL.md"))

    def test_src_guarded(self):
        self.assertTrue(_is_guarded_path("src/main.py"))

    def test_tests_guarded(self):
        self.assertTrue(_is_guarded_path("tests/test_foo.py"))

    def test_source_extension_guarded(self):
        self.assertTrue(_is_guarded_path("some/path/module.ts"))
        self.assertTrue(_is_guarded_path("some/path/module.go"))

    def test_manifest_not_guarded(self):
        self.assertFalse(_is_guarded_path("doc/harness/manifest.yaml"))

    def test_claude_md_not_guarded(self):
        self.assertFalse(_is_guarded_path("CLAUDE.md"))

    def test_task_state_not_guarded(self):
        self.assertFalse(_is_guarded_path("doc/harness/tasks/TASK__x/TASK_STATE.yaml"))

    def test_dot_slash_normalized(self):
        self.assertTrue(_is_guarded_path("./plugin/scripts/foo.py"))

    def test_empty_not_guarded(self):
        self.assertFalse(_is_guarded_path(""))

    def test_package_json_not_guarded(self):
        """Non-source, non-guarded config files are allowed."""
        self.assertFalse(_is_guarded_path("package.json"))

    def test_config_yaml_not_guarded(self):
        self.assertFalse(_is_guarded_path("config.yaml"))


# ---------------------------------------------------------------------------
# Unit tests for _extract_read_target
# ---------------------------------------------------------------------------

class TestExtractReadTarget(unittest.TestCase):

    def test_read_tool(self):
        data = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "plugin/scripts/foo.py"}})
        tool, path = _extract_read_target(data)
        self.assertEqual(tool, "Read")
        self.assertEqual(path, "plugin/scripts/foo.py")

    def test_glob_tool(self):
        data = json.dumps({"tool_name": "Glob", "tool_input": {"path": "plugin/scripts/", "pattern": "*.py"}})
        tool, path = _extract_read_target(data)
        self.assertEqual(tool, "Glob")
        self.assertEqual(path, "plugin/scripts/")

    def test_grep_tool(self):
        data = json.dumps({"tool_name": "Grep", "tool_input": {"path": "src/", "pattern": "def main"}})
        tool, path = _extract_read_target(data)
        self.assertEqual(tool, "Grep")
        self.assertEqual(path, "src/")

    def test_write_tool_ignored(self):
        """Write/Edit tools are not reads — should return None."""
        data = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/foo.py"}})
        tool, path = _extract_read_target(data)
        self.assertIsNone(tool)

    def test_empty_input(self):
        tool, path = _extract_read_target("")
        self.assertIsNone(tool)

    def test_invalid_json(self):
        tool, path = _extract_read_target("not json")
        self.assertIsNone(tool)


# ---------------------------------------------------------------------------
# Unit tests for _check_bash_command
# ---------------------------------------------------------------------------

class TestCheckBashCommand(unittest.TestCase):

    def test_cat_guarded_path(self):
        tool, path = _check_bash_command("cat plugin/scripts/foo.py")
        self.assertEqual(tool, "Bash")
        self.assertIn("plugin/scripts/foo.py", path)

    def test_grep_guarded_path(self):
        tool, path = _check_bash_command("grep -r 'pattern' src/")
        self.assertEqual(tool, "Bash")
        self.assertIn("src/", path)

    def test_head_guarded_path(self):
        tool, path = _check_bash_command("head -20 tests/test_foo.py")
        self.assertEqual(tool, "Bash")
        self.assertIn("tests/test_foo.py", path)

    def test_ls_guarded_path(self):
        tool, path = _check_bash_command("ls plugin/agents/")
        self.assertEqual(tool, "Bash")
        self.assertIn("plugin/agents/", path)

    def test_non_read_command(self):
        tool, path = _check_bash_command("python3 -m pytest tests/")
        self.assertIsNone(tool)

    def test_allowed_path(self):
        tool, path = _check_bash_command("cat doc/harness/manifest.yaml")
        self.assertIsNone(tool)

    def test_empty_command(self):
        tool, path = _check_bash_command("")
        self.assertIsNone(tool)


# ---------------------------------------------------------------------------
# Integration tests for _has_plan_access
# ---------------------------------------------------------------------------

class TestHasPlanAccess(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        import preplan_read_gate
        self._orig_task_dir = preplan_read_gate.TASK_DIR
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        preplan_read_gate.TASK_DIR = self.task_base

    def tearDown(self):
        import preplan_read_gate
        preplan_read_gate.TASK_DIR = self._orig_task_dir
        self.tmp.cleanup()

    def _write_task(self, task_id, **fields):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        defaults = {
            "task_id": task_id,
            "status": "created",
            "plan_verdict": "pending",
            "plan_session_state": "closed",
            "updated": "2026-01-01T00:00:00Z",
        }
        defaults.update(fields)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            for k, v in defaults.items():
                f.write(f"{k}: {v}\n")
        return task_dir

    def _write_plan_session(self, task_dir, state="open", phase="context"):
        token = {
            "task_id": os.path.basename(task_dir),
            "state": state,
            "phase": phase,
            "source": "plan-skill",
        }
        with open(os.path.join(task_dir, "PLAN_SESSION.json"), "w") as f:
            json.dump(token, f)

    def test_no_tasks_denied(self):
        allowed, _ = _has_plan_access()
        self.assertFalse(allowed)

    def test_task_no_plan_session_denied(self):
        self._write_task("TASK__foo")
        allowed, reason = _has_plan_access()
        self.assertFalse(allowed)
        self.assertIn("no active plan session", reason)

    def test_plan_session_context_allowed(self):
        task_dir = self._write_task("TASK__foo")
        self._write_plan_session(task_dir, state="open", phase="context")
        allowed, reason = _has_plan_access()
        self.assertTrue(allowed)
        self.assertIn("context", reason)

    def test_plan_session_write_allowed(self):
        task_dir = self._write_task("TASK__foo")
        self._write_plan_session(task_dir, state="open", phase="write")
        allowed, reason = _has_plan_access()
        self.assertTrue(allowed)
        self.assertIn("write", reason)

    def test_plan_verdict_pass_allowed(self):
        self._write_task("TASK__foo", plan_verdict="PASS")
        allowed, reason = _has_plan_access()
        self.assertTrue(allowed)
        self.assertIn("PASS", reason)

    def test_closed_session_denied(self):
        task_dir = self._write_task("TASK__foo")
        self._write_plan_session(task_dir, state="closed", phase="done")
        allowed, _ = _has_plan_access()
        self.assertFalse(allowed)

    def test_closed_task_excluded(self):
        self._write_task("TASK__done", status="closed", plan_verdict="PASS")
        allowed, _ = _has_plan_access()
        self.assertFalse(allowed)

    def test_plan_session_state_context_open_allowed(self):
        """plan_session_state field in TASK_STATE allows access."""
        self._write_task("TASK__foo", plan_session_state="context_open")
        allowed, reason = _has_plan_access()
        self.assertTrue(allowed)
        self.assertIn("context_open", reason)

    def test_plan_session_state_write_open_allowed(self):
        self._write_task("TASK__foo", plan_session_state="write_open")
        allowed, reason = _has_plan_access()
        self.assertTrue(allowed)
        self.assertIn("write_open", reason)


if __name__ == "__main__":
    unittest.main()
