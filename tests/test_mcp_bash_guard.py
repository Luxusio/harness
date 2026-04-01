"""Tests for MCP bash guard.

Ensures model-facing harness control-plane CLI invocations are blocked and routed
to MCP tools instead.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "plugin" / "scripts" / "mcp_bash_guard.py"


class McpBashGuardTests(unittest.TestCase):
    def _run_guard(self, command: str):
        payload = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
        })
        result = subprocess.run(
            [sys.executable, str(GUARD)],
            input=payload,
            text=True,
            capture_output=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "HARNESS_SKIP_STDIN": ""},
        )
        return result

    def test_blocks_direct_hctl_start(self):
        result = self._run_guard("python3 plugin/scripts/hctl.py start --task-dir doc/harness/tasks/TASK__x")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__task_start", result.stderr)

    def test_blocks_absolute_plugin_path(self):
        result = self._run_guard(
            "TASK_DIR=/tmp/x && python3 /home/ccc/.claude/plugins/marketplaces/harness/plugin/scripts/hctl.py context --task-dir /tmp/x --json"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__task_context", result.stderr)

    def test_blocks_verify_cli(self):
        result = self._run_guard("python3 plugin/scripts/verify.py suite")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__verify_run", result.stderr)

    def test_blocks_write_artifact_cli(self):
        result = self._run_guard(
            "python3 plugin/scripts/write_artifact.py critic-plan --task-dir doc/harness/tasks/TASK__x --verdict PASS --summary ok"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__write_critic_plan", result.stderr)

    def test_blocks_calibration_cli(self):
        result = self._run_guard("python3 plugin/scripts/calibration_miner.py --dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__calibration_mine", result.stderr)

    def test_blocks_observability_cli(self):
        result = self._run_guard("python3 plugin/scripts/observability.py status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mcp__plugin_harness_harness__observability_status", result.stderr)

    def test_allows_regular_bash(self):
        result = self._run_guard("npm test -- --grep runtime")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
