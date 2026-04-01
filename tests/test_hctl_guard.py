"""Tests for hctl_guard.py — stronger write gate for all repo writes.

Covers:
  - no active task -> block guarded writes
  - routing_compiled=false -> block
  - context not recorded -> block
  - PLAN.md missing -> block
  - plan_verdict != PASS -> block
  - ready task -> allow guarded writes
  - protected artifacts / task-local artifacts are exempt from this guard
  - escape hatches bypass the guard
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from hctl_guard import _is_guarded_write


class TestIsGuardedWrite(unittest.TestCase):

    def test_package_json_is_guarded(self):
        self.assertTrue(_is_guarded_write("package.json"))

    def test_readme_is_guarded(self):
        self.assertTrue(_is_guarded_write("README.md"))

    def test_task_state_is_exempt(self):
        self.assertFalse(_is_guarded_write("doc/harness/tasks/TASK__x/TASK_STATE.yaml"))

    def test_plan_md_is_exempt(self):
        self.assertFalse(_is_guarded_write("doc/harness/tasks/TASK__x/PLAN.md"))

    def test_meta_sidecar_is_exempt(self):
        self.assertFalse(_is_guarded_write("doc/harness/tasks/TASK__x/HANDOFF.meta.json"))


class TestHctlGuardIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_base = os.path.join(self.tmp.name, "doc", "harness", "tasks")
        os.makedirs(self.task_base, exist_ok=True)

        manifest_dir = os.path.join(self.tmp.name, "doc", "harness")
        os.makedirs(manifest_dir, exist_ok=True)
        self.manifest_path = os.path.join(manifest_dir, "manifest.yaml")
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write("version: 5\n")

        import hctl_guard
        import _lib as _harness_lib
        self._orig_task_dir = hctl_guard.TASK_DIR
        self._orig_manifest = hctl_guard.MANIFEST
        hctl_guard.TASK_DIR = self.task_base
        hctl_guard.MANIFEST = self.manifest_path

        self._orig_skip_prewrite = os.environ.pop("HARNESS_SKIP_PREWRITE", None)
        self._orig_skip_guard = os.environ.pop("HARNESS_SKIP_HCTL_GUARD", None)
        self._orig_skip_stdin = os.environ.pop("HARNESS_SKIP_STDIN", None)

        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp.name)

        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False

    def tearDown(self):
        import hctl_guard
        import _lib as _harness_lib
        hctl_guard.TASK_DIR = self._orig_task_dir
        hctl_guard.MANIFEST = self._orig_manifest
        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False
        os.chdir(self._orig_cwd)
        if self._orig_skip_prewrite is not None:
            os.environ["HARNESS_SKIP_PREWRITE"] = self._orig_skip_prewrite
        if self._orig_skip_guard is not None:
            os.environ["HARNESS_SKIP_HCTL_GUARD"] = self._orig_skip_guard
        if self._orig_skip_stdin is not None:
            os.environ["HARNESS_SKIP_STDIN"] = self._orig_skip_stdin
        self.tmp.cleanup()

    def _write_task(self, task_id, *, routing_compiled="true", context_read_count="1",
                    context_last_read_at="2026-01-01T00:00:00Z", plan_verdict="PASS",
                    status="plan_passed", updated="2026-01-01T00:00:00Z"):
        task_dir = os.path.join(self.task_base, task_id)
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
            f.write(
                f"task_id: {task_id}\n"
                f"status: {status}\n"
                f"routing_compiled: {routing_compiled}\n"
                f"context_read_count: {context_read_count}\n"
                f"context_last_read_at: {context_last_read_at}\n"
                f"plan_verdict: {plan_verdict}\n"
                f"updated: {updated}\n"
            )
        return task_dir

    def _write_plan(self, task_dir):
        with open(os.path.join(task_dir, "PLAN.md"), "w", encoding="utf-8") as f:
            f.write("# Plan\n")

    def _hook_payload(self, path):
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}})

    def _run_main(self, hook_data):
        import hctl_guard
        import _lib as _harness_lib
        import io

        orig_stdin = sys.stdin
        sys.stdin = io.StringIO(hook_data)
        _harness_lib._HOOK_INPUT = None
        _harness_lib._HOOK_INPUT_READ = False
        try:
            hctl_guard.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0
        finally:
            sys.stdin = orig_stdin
            _harness_lib._HOOK_INPUT = None
            _harness_lib._HOOK_INPUT_READ = False

    def test_no_active_task_blocks(self):
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_routing_not_compiled_blocks(self):
        task_dir = self._write_task("TASK__a", routing_compiled="false")
        self._write_plan(task_dir)
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_missing_context_blocks(self):
        task_dir = self._write_task("TASK__a", context_read_count="0", context_last_read_at="null")
        self._write_plan(task_dir)
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_missing_plan_blocks(self):
        self._write_task("TASK__a")
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_non_pass_plan_blocks(self):
        task_dir = self._write_task("TASK__a", plan_verdict="pending")
        self._write_plan(task_dir)
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_ready_task_allows_package_json(self):
        task_dir = self._write_task("TASK__a")
        self._write_plan(task_dir)
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 0)

    def test_most_recent_active_task_wins(self):
        older = self._write_task("TASK__old", updated="2026-01-01T00:00:00Z")
        self._write_plan(older)
        newer = self._write_task(
            "TASK__new",
            routing_compiled="false",
            updated="2026-01-02T00:00:00Z",
        )
        self._write_plan(newer)
        code = self._run_main(self._hook_payload("package.json"))
        self.assertEqual(code, 2)

    def test_task_local_artifact_is_exempt(self):
        code = self._run_main(self._hook_payload("doc/harness/tasks/TASK__x/TASK_STATE.yaml"))
        self.assertEqual(code, 0)

    def test_escape_hatch_bypasses(self):
        os.environ["HARNESS_SKIP_HCTL_GUARD"] = "1"
        try:
            code = self._run_main(self._hook_payload("package.json"))
            self.assertEqual(code, 0)
        finally:
            os.environ.pop("HARNESS_SKIP_HCTL_GUARD", None)


if __name__ == "__main__":
    unittest.main()
