"""Tests for workflow control surface write-lock in prewrite_gate.py.

WS-4 requirement (PLAN.md §10.3):
  - maintenance_task=false → workflow control surface write BLOCKED
  - maintenance_task=true  → workflow control surface write ALLOWED
  - Non-surface files are unaffected by this gate
  - hctl.py itself is in the surface set
  - plugin/CLAUDE.md is in the surface set (harness orchestrator agent was removed)
  - Escape hatch HARNESS_SKIP_PREWRITE=1 bypasses the lock

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from prewrite_gate import (
    _is_workflow_control_surface,
    _active_task_is_maintenance,
    WORKFLOW_CONTROL_SURFACE,
)


# ---------------------------------------------------------------------------
# Unit tests for _is_workflow_control_surface
# ---------------------------------------------------------------------------

class TestIsWorkflowControlSurface(unittest.TestCase):

    def test_plugin_claude_md(self):
        self.assertTrue(_is_workflow_control_surface("plugin/CLAUDE.md"))

    def test_execution_modes_doc(self):
        self.assertTrue(_is_workflow_control_surface("plugin/docs/execution-modes.md"))

    def test_orchestration_modes_doc(self):
        self.assertTrue(_is_workflow_control_surface("plugin/docs/orchestration-modes.md"))

    def test_hooks_json(self):
        self.assertTrue(_is_workflow_control_surface("plugin/hooks/hooks.json"))

    def test_plugin_mcp_json(self):
        self.assertTrue(_is_workflow_control_surface("plugin/.mcp.json"))

    def test_setup_skill(self):
        self.assertTrue(_is_workflow_control_surface("plugin/skills/setup/SKILL.md"))

    def test_setup_template_claude_md(self):
        self.assertTrue(_is_workflow_control_surface(
            "plugin/skills/setup/templates/CLAUDE.md"
        ))

    def test_setup_template_manifest(self):
        self.assertTrue(_is_workflow_control_surface(
            "plugin/skills/setup/templates/doc/harness/manifest.yaml"
        ))

    def test_hctl_py(self):
        self.assertTrue(_is_workflow_control_surface("plugin/scripts/hctl.py"))

    def test_harness_mcp_server(self):
        self.assertTrue(_is_workflow_control_surface("plugin/mcp/harness_server.py"))

    def test_mcp_bash_guard(self):
        self.assertTrue(_is_workflow_control_surface("plugin/scripts/mcp_bash_guard.py"))

    def test_dot_slash_normalized(self):
        self.assertTrue(_is_workflow_control_surface("./plugin/CLAUDE.md"))

    def test_regular_source_not_surface(self):
        self.assertFalse(_is_workflow_control_surface("plugin/scripts/verify.py"))

    def test_lib_not_surface(self):
        self.assertFalse(_is_workflow_control_surface("plugin/scripts/_lib.py"))

    def test_test_file_not_surface(self):
        self.assertFalse(_is_workflow_control_surface("tests/test_hctl.py"))

    def test_task_state_not_surface(self):
        self.assertFalse(_is_workflow_control_surface("TASK_STATE.yaml"))

    def test_readme_not_surface(self):
        self.assertFalse(_is_workflow_control_surface("README.md"))

    def test_none_safe(self):
        self.assertFalse(_is_workflow_control_surface(None))

    def test_empty_string(self):
        self.assertFalse(_is_workflow_control_surface(""))

    def test_surface_set_not_empty(self):
        self.assertGreater(len(WORKFLOW_CONTROL_SURFACE), 0)


# ---------------------------------------------------------------------------
# Unit tests for _active_task_is_maintenance
# ---------------------------------------------------------------------------

class TestActiveTaskIsMaintenance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)
        import prewrite_gate
        self._orig_task_dir = prewrite_gate.TASK_DIR
        prewrite_gate.TASK_DIR = self.task_base

    def tearDown(self):
        import prewrite_gate
        prewrite_gate.TASK_DIR = self._orig_task_dir
        self.tmp.cleanup()

    def _write_task(self, task_id, status="plan_passed", plan_verdict="PASS",
                    maintenance_task="false", updated="2026-01-01T00:00:00Z"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            f.write(
                f"task_id: {task_id}\n"
                f"status: {status}\n"
                f"plan_verdict: {plan_verdict}\n"
                f"maintenance_task: {maintenance_task}\n"
                f"updated: {updated}\n"
            )
        return task_dir

    def test_no_tasks_returns_false(self):
        self.assertFalse(_active_task_is_maintenance())

    def test_maintenance_false(self):
        self._write_task("TASK__normal", maintenance_task="false")
        self.assertFalse(_active_task_is_maintenance())

    def test_maintenance_true(self):
        self._write_task("TASK__maint", maintenance_task="true")
        self.assertTrue(_active_task_is_maintenance())

    def test_maintenance_true_uppercase(self):
        self._write_task("TASK__maint", maintenance_task="True")
        self.assertTrue(_active_task_is_maintenance())

    def test_closed_task_excluded(self):
        self._write_task("TASK__done", status="closed", maintenance_task="true")
        self.assertFalse(_active_task_is_maintenance())

    def test_most_recent_active_wins(self):
        # Two tasks: one normal (older), one maintenance (newer)
        self._write_task("TASK__a_normal",
                         maintenance_task="false",
                         updated="2026-01-01T00:00:00Z")
        self._write_task("TASK__b_maint",
                         maintenance_task="true",
                         updated="2026-01-02T00:00:00Z")
        self.assertTrue(_active_task_is_maintenance())

    def test_most_recent_active_wins_reverse(self):
        # Two tasks: one maintenance (older), one normal (newer)
        self._write_task("TASK__a_maint",
                         maintenance_task="true",
                         updated="2026-01-01T00:00:00Z")
        self._write_task("TASK__b_normal",
                         maintenance_task="false",
                         updated="2026-01-02T00:00:00Z")
        self.assertFalse(_active_task_is_maintenance())


# ---------------------------------------------------------------------------
# Integration tests: workflow surface lock via main()
# ---------------------------------------------------------------------------

class TestWorkflowSurfaceLockIntegration(unittest.TestCase):
    """End-to-end test of the workflow surface lock through main()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)

        # Create a manifest so the gate is active
        manifest_dir = os.path.join(self.tmp.name, "doc", "harness")
        os.makedirs(manifest_dir, exist_ok=True)
        self.manifest_path = os.path.join(manifest_dir, "manifest.yaml")
        with open(self.manifest_path, "w") as f:
            f.write("version: 5\n")

        import prewrite_gate
        import _lib as _harness_lib
        self._orig_task_dir = prewrite_gate.TASK_DIR
        self._orig_manifest = prewrite_gate.MANIFEST
        prewrite_gate.TASK_DIR = self.task_base
        prewrite_gate.MANIFEST = self.manifest_path

        # Reset stdin cache in _lib (the actual global, not the imported copy)
        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False

        # Save + clear env that might leak between tests
        self._orig_skip_prewrite = os.environ.pop("HARNESS_SKIP_PREWRITE", None)
        self._orig_skip_stdin = os.environ.pop("HARNESS_SKIP_STDIN", None)

        # Patch cwd so absolute-path stripping works
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        import prewrite_gate
        import _lib as _harness_lib
        prewrite_gate.TASK_DIR = self._orig_task_dir
        prewrite_gate.MANIFEST = self._orig_manifest
        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False
        os.chdir(self._orig_cwd)
        # Restore env
        if self._orig_skip_prewrite is not None:
            os.environ["HARNESS_SKIP_PREWRITE"] = self._orig_skip_prewrite
        if self._orig_skip_stdin is not None:
            os.environ["HARNESS_SKIP_STDIN"] = self._orig_skip_stdin
        self.tmp.cleanup()

    def _write_task(self, task_id, maintenance_task="false",
                    plan_verdict="PASS", status="plan_passed",
                    updated="2026-01-01T00:00:00Z"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
            f.write(
                f"task_id: {task_id}\n"
                f"status: {status}\n"
                f"plan_verdict: {plan_verdict}\n"
                f"maintenance_task: {maintenance_task}\n"
                f"updated: {updated}\n"
            )

    def _hook_payload(self, path):
        return json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": path},
        })

    def _run_main(self, hook_data):
        """Run main() and return exit code."""
        import prewrite_gate
        import _lib as _harness_lib
        import io

        orig_stdin = sys.stdin
        sys.stdin = io.StringIO(hook_data)

        # Reset the actual cache in _lib (read_hook_input uses _lib globals)
        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False

        try:
            prewrite_gate.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0
        finally:
            sys.stdin = orig_stdin
            _harness_lib._HOOK_INPUT = None
            _harness_lib._HOOK_INPUT_READ = False

    def test_surface_blocked_when_not_maintenance(self):
        self._write_task("TASK__normal", maintenance_task="false")
        payload = self._hook_payload("plugin/CLAUDE.md")
        code = self._run_main(payload)
        self.assertEqual(code, 2, "Control surface write should be blocked for normal task")

    def test_surface_allowed_when_maintenance(self):
        self._write_task("TASK__maint", maintenance_task="true")
        payload = self._hook_payload("plugin/CLAUDE.md")
        code = self._run_main(payload)
        self.assertEqual(code, 0, "Control surface write should be allowed for maintenance task")

    def test_hctl_blocked_when_not_maintenance(self):
        self._write_task("TASK__normal", maintenance_task="false")
        payload = self._hook_payload("plugin/scripts/hctl.py")
        code = self._run_main(payload)
        self.assertEqual(code, 2)

    def test_hctl_allowed_when_maintenance(self):
        self._write_task("TASK__maint", maintenance_task="true")
        payload = self._hook_payload("plugin/scripts/hctl.py")
        code = self._run_main(payload)
        self.assertEqual(code, 0)

    def test_non_surface_source_unaffected(self):
        """Non-surface source writes still follow normal plan-gate rules."""
        self._write_task("TASK__normal", maintenance_task="false",
                         plan_verdict="PASS")
        payload = self._hook_payload("plugin/scripts/verify.py")
        code = self._run_main(payload)
        # verify.py is a source file — gate allows it when plan PASS
        self.assertEqual(code, 0)

    def test_skip_prewrite_bypasses_surface_lock(self):
        self._write_task("TASK__normal", maintenance_task="false")
        os.environ["HARNESS_SKIP_PREWRITE"] = "1"
        try:
            payload = self._hook_payload("plugin/CLAUDE.md")
            code = self._run_main(payload)
            self.assertEqual(code, 0, "Escape hatch should bypass surface lock")
        finally:
            os.environ.pop("HARNESS_SKIP_PREWRITE", None)

    def test_surface_lock_no_tasks(self):
        """No active task + surface write → blocked (no maintenance task = false)."""
        payload = self._hook_payload("plugin/CLAUDE.md")
        code = self._run_main(payload)
        self.assertEqual(code, 2)

    def test_hooks_json_blocked_when_not_maintenance(self):
        self._write_task("TASK__normal", maintenance_task="false")
        payload = self._hook_payload("plugin/hooks/hooks.json")
        code = self._run_main(payload)
        self.assertEqual(code, 2)

    def test_setup_template_blocked_when_not_maintenance(self):
        self._write_task("TASK__normal", maintenance_task="false")
        payload = self._hook_payload("plugin/skills/setup/templates/CLAUDE.md")
        code = self._run_main(payload)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
