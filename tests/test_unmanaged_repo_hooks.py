"""Hooks should silently no-op when the repo has not run /harness:setup.

This covers the user-facing case where the plugin is installed globally, but the
current project is not harness-managed because doc/harness/manifest.yaml does
not exist.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugin" / "scripts"


class UnmanagedRepoHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run_hook(self, script_name: str, payload=None):
        if payload is None:
            payload = {}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script_name)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=self.cwd,
            env={**os.environ, "HARNESS_SKIP_STDIN": ""},
        )

    def _make_task_dir(self, task_id: str = "TASK__demo") -> Path:
        task_dir = Path(self.cwd) / "doc" / "harness" / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            "task_id: {task_id}\nstatus: implemented\nplan_verdict: PASS\nruntime_verdict: PASS\ndocument_verdict: skipped\n".format(
                task_id=task_id
            ),
            encoding="utf-8",
        )
        return task_dir

    def test_session_context_silent_without_manifest(self):
        result = self._run_hook("session_context.py")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_prompt_memory_silent_without_manifest(self):
        result = self._run_hook(
            "prompt_memory.py",
            {"prompt": "fix src/app.py and update the handler"},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_mcp_bash_guard_ignored_without_manifest(self):
        result = self._run_hook(
            "mcp_bash_guard.py",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "python3 plugin/scripts/hctl.py start --task-dir doc/harness/tasks/TASK__x"
                },
            },
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_tool_routing_silent_without_manifest(self):
        result = self._run_hook(
            "tool_routing.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "npm run lint"},
                "tool_response": {"stderr": "command not found"},
            },
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_task_created_gate_does_not_create_artifacts_without_manifest(self):
        result = self._run_hook(
            "task_created_gate.py",
            {"task_id": "TASK__demo", "description": "Investigate the issue"},
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse((Path(self.cwd) / "doc" / "harness" / "tasks" / "TASK__demo").exists())

    def test_task_hooks_silent_without_manifest_even_with_task_dir(self):
        self._make_task_dir("TASK__demo")

        completed = self._run_hook(
            "task_completed_gate.py",
            {"task_id": "TASK__demo"},
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")

        subagent = self._run_hook(
            "subagent_stop_gate.py",
            {"task_id": "TASK__demo", "agent_name": "harness:developer"},
        )
        self.assertEqual(subagent.returncode, 0)
        self.assertEqual(subagent.stdout, "")
        self.assertEqual(subagent.stderr, "")


if __name__ == "__main__":
    unittest.main()
