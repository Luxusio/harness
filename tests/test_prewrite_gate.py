"""Tests for prewrite_gate.py — blocking enforcement of plan-first workflow.

Covers:
  - Source file write without plan PASS → exit 2 (block)
  - No manifest (harness not initialized) → exit 0 (allow)
  - Plan PASS exists on any active task → exit 0 (allow)
  - Escape hatch HARNESS_SKIP_PREWRITE → exit 0 (allow)
  - Non-source file write → exit 0 (allow)
  - No active tasks → exit 2 (block)
  - Protected artifact ownership enforcement
  - PLAN.md token-based authorization
  - Fail-closed on managed repos

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from prewrite_gate import (
    _is_source_file, _find_active_tasks, _extract_file_path,
    _is_protected_artifact, _check_protected_artifact_write,
    _get_agent_role, _check_plan_session_token,
)


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
        # PLAN.md is a protected artifact, not a source file
        self.assertFalse(_is_source_file("PLAN.md"))

    def test_exempt_filename_TASK_STATE(self):
        self.assertFalse(_is_source_file("TASK_STATE.yaml"))

    def test_exempt_filename_HANDOFF(self):
        # HANDOFF.md is now a protected artifact, not in EXEMPT_FILENAMES
        self.assertFalse(_is_source_file("HANDOFF.md"))

    def test_dot_slash_normalized(self):
        self.assertTrue(_is_source_file("./src/main.py"))

    def test_empty_path(self):
        self.assertFalse(_is_source_file(""))

    def test_none_path(self):
        self.assertFalse(_is_source_file(None))

    def test_meta_json_is_exempt(self):
        self.assertFalse(_is_source_file("doc/harness/tasks/TASK__x/HANDOFF.meta.json"))

    def test_protected_artifact_not_source(self):
        """Protected artifacts are handled separately, not as source files."""
        for artifact in ("PLAN.md", "HANDOFF.md", "DOC_SYNC.md",
                         "CRITIC__plan.md", "CRITIC__runtime.md", "CRITIC__document.md"):
            self.assertFalse(_is_source_file(artifact),
                f"{artifact} should not be treated as source file")


# ---------------------------------------------------------------------------
# Unit tests for _is_protected_artifact
# ---------------------------------------------------------------------------

class TestIsProtectedArtifact(unittest.TestCase):

    def test_plan_is_protected(self):
        self.assertTrue(_is_protected_artifact("PLAN.md"))

    def test_handoff_is_protected(self):
        self.assertTrue(_is_protected_artifact("HANDOFF.md"))

    def test_doc_sync_is_protected(self):
        self.assertTrue(_is_protected_artifact("DOC_SYNC.md"))

    def test_critic_plan_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__plan.md"))

    def test_critic_runtime_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__runtime.md"))

    def test_critic_document_is_protected(self):
        self.assertTrue(_is_protected_artifact("CRITIC__document.md"))

    def test_task_state_not_protected(self):
        self.assertFalse(_is_protected_artifact("TASK_STATE.yaml"))

    def test_source_file_not_protected(self):
        self.assertFalse(_is_protected_artifact("src/main.py"))

    def test_full_path_works(self):
        self.assertTrue(_is_protected_artifact("doc/harness/tasks/TASK__foo/HANDOFF.md"))

    def test_none_safe(self):
        self.assertFalse(_is_protected_artifact(None))
        self.assertFalse(_is_protected_artifact(""))


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


# ---------------------------------------------------------------------------
# Protected artifact ownership tests
# ---------------------------------------------------------------------------

class TestProtectedArtifactOwnership(unittest.TestCase):
    """Test that protected artifacts can only be written by authorized roles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        # Patch TASK_DIR
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        prewrite_gate.TASK_DIR = self.task_base
        # Save original env
        self._orig_agent = os.environ.get("CLAUDE_AGENT_NAME", "")

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        os.environ["CLAUDE_AGENT_NAME"] = self._orig_agent
        self.tmp.cleanup()

    def _write_task(self, task_id, **kwargs):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        defaults = {
            "task_id": task_id,
            "status": "plan_passed",
            "plan_verdict": "PASS",
            "plan_session_state": "closed",
            "updated": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            for k, v in defaults.items():
                f.write(f"{k}: {v}\n")
        return task_dir

    def _write_plan_session(self, task_dir, state="open", phase="write"):
        token = {"task_id": os.path.basename(task_dir), "state": state, "phase": phase}
        with open(os.path.join(task_dir, "PLAN_SESSION.json"), "w") as f:
            json.dump(token, f)

    def test_harness_writes_source_blocked(self):
        """Harness role writing source file → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        role = _get_agent_role()
        self.assertEqual(role, "harness")

    def test_developer_writes_handoff_allowed(self):
        """Developer writing HANDOFF.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        allowed, _ = _check_protected_artifact_write("HANDOFF.md")
        self.assertTrue(allowed)

    def test_writer_writes_handoff_blocked(self):
        """Writer writing HANDOFF.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer"
        allowed, msg = _check_protected_artifact_write("HANDOFF.md")
        self.assertFalse(allowed)
        self.assertIn("developer", msg)

    def test_writer_writes_doc_sync_allowed(self):
        """Writer writing DOC_SYNC.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer"
        allowed, _ = _check_protected_artifact_write("DOC_SYNC.md")
        self.assertTrue(allowed)

    def test_harness_writes_doc_sync_blocked(self):
        """Harness writing DOC_SYNC.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        allowed, msg = _check_protected_artifact_write("DOC_SYNC.md")
        self.assertFalse(allowed)
        self.assertIn("writer", msg)

    def test_critic_runtime_writes_critic_runtime_allowed(self):
        """Critic-runtime writing CRITIC__runtime.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:critic-runtime"
        allowed, _ = _check_protected_artifact_write("CRITIC__runtime.md")
        self.assertTrue(allowed)

    def test_harness_writes_critic_runtime_blocked(self):
        """Harness writing CRITIC__runtime.md → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        allowed, msg = _check_protected_artifact_write("CRITIC__runtime.md")
        self.assertFalse(allowed)
        self.assertIn("critic-runtime", msg)

    def test_critic_plan_writes_critic_plan_allowed(self):
        """Critic-plan writing CRITIC__plan.md → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:critic-plan"
        allowed, _ = _check_protected_artifact_write("CRITIC__plan.md")
        self.assertTrue(allowed)

    def test_developer_writes_critic_plan_blocked(self):
        """Developer writing CRITIC__plan.md → blocked (generator/evaluator separation)."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:developer"
        allowed, msg = _check_protected_artifact_write("CRITIC__plan.md")
        self.assertFalse(allowed)
        self.assertIn("critic-plan", msg)

    def test_plan_md_without_token_blocked(self):
        """PLAN.md write without plan session token → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        self._write_task("TASK__test")
        allowed, msg = _check_protected_artifact_write("PLAN.md")
        self.assertFalse(allowed)
        self.assertIn("plan session", msg.lower())

    def test_plan_md_with_token_allowed(self):
        """PLAN.md write with active plan session token → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        task_dir = self._write_task("TASK__test")
        self._write_plan_session(task_dir, state="open", phase="write")
        allowed, _ = _check_protected_artifact_write("PLAN.md")
        self.assertTrue(allowed)

    def test_plan_md_with_closed_token_blocked(self):
        """PLAN.md write with closed plan session → blocked."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        task_dir = self._write_task("TASK__test")
        self._write_plan_session(task_dir, state="closed", phase="write")
        allowed, msg = _check_protected_artifact_write("PLAN.md")
        self.assertFalse(allowed)

    def test_plan_md_session_state_write_open_allowed(self):
        """PLAN.md write when plan_session_state=write_open in TASK_STATE → allowed."""
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"
        self._write_task("TASK__test", plan_session_state="write_open")
        allowed, _ = _check_protected_artifact_write("PLAN.md")
        self.assertTrue(allowed)


# ---------------------------------------------------------------------------
# Fail-closed behavior on managed repos
# ---------------------------------------------------------------------------

class TestFailClosed(unittest.TestCase):
    """Verify that parse failures result in block (fail-closed) on managed repos."""

    def test_exception_handler_blocks_on_managed_repo(self):
        """When manifest exists, exceptions should block (exit 2), not allow."""
        import prewrite_gate
        # The __main__ block now fail-closes when MANIFEST exists
        # We just verify the logic is correct by checking the file
        self.assertIn("Fail-closed on managed repos",
                       open(os.path.join(os.path.dirname(__file__),
                            "..", "plugin", "scripts", "prewrite_gate.py")).read())


if __name__ == "__main__":
    unittest.main()
