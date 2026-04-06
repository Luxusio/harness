"""Tests for the plugin-local harness MCP server."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
assert spec and spec.loader
harness_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_server)


class HarnessMcpServerTests(unittest.TestCase):
    def _make_task(self, base_dir: str, task_id: str) -> str:
        task_dir = Path(base_dir) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            "\n".join(
                [
                    f"task_id: {task_id}",
                    "status: planned",
                    "lane: refactor",
                    "risk_tags: []",
                    "browser_required: false",
                    "runtime_verdict_fail_count: 0",
                    "qa_required: true",
                    "doc_sync_required: true",
                    "parallelism: 1",
                    "workflow_locked: true",
                    "maintenance_task: false",
                    "routing_compiled: false",
                    "routing_source: pending",
                    "risk_level: pending",
                    "execution_mode: standard",
                    "orchestration_mode: solo",
                    "plan_verdict: PASS",
                    "runtime_verdict: pending",
                    "updated: 2026-01-01T00:00:00Z",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
        return str(task_dir)

    def test_tool_registry_contains_expected_tools(self):
        tools = {tool["name"] for tool in harness_server.list_tools()}
        expected = {
            "task_start",
            "task_context",
            "team_bootstrap",
            "team_dispatch",
            "team_launch",
            "team_relaunch",
            "task_update_from_git_diff",
            "record_agent_run",
            "task_verify",
            "task_close",
            "verify_run",
            "write_critic_plan",
            "write_critic_runtime",
            "write_critic_document",
            "write_handoff",
            "write_doc_sync",
            "calibration_mine",
            "observability_detect",
            "observability_status",
            "observability_hint",
            "observability_policy",
        }
        self.assertTrue(expected.issubset(tools))

    def test_task_context_returns_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__mcp")
            result = harness_server.call_tool("task_context", {"task_dir": task_dir})
            self.assertNotIn("isError", result)
            structured = result["structuredContent"]
            self.assertEqual(structured["task_context"]["task_id"], "TASK__mcp")
            self.assertIn("must_read", structured["task_context"])
            self.assertIn("planning_mode", structured["task_context"])

    def test_task_context_forwards_personalization_args(self):
        captured = {}

        def fake_get_task_context(task_dir, *, team_worker=None, agent_name=None):
            captured["task_dir"] = task_dir
            captured["team_worker"] = team_worker
            captured["agent_name"] = agent_name
            return {"task_id": "TASK__mcp", "must_read": [], "planning_mode": "standard"}

        original = harness_server.harness_api.get_task_context
        harness_server.harness_api.get_task_context = fake_get_task_context
        try:
            result = harness_server.call_tool(
                "task_context",
                {
                    "task_dir": "/tmp/TASK__mcp",
                    "team_worker": "reviewer",
                    "agent_name": "harness:writer:reviewer",
                },
            )
        finally:
            harness_server.harness_api.get_task_context = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["task_dir"], "/tmp/TASK__mcp")
        self.assertEqual(captured["team_worker"], "reviewer")
        self.assertEqual(captured["agent_name"], "harness:writer:reviewer")
        self.assertEqual(result["structuredContent"]["fetch"]["method"], "direct")

    def test_task_context_falls_back_to_cli_when_direct_path_errors(self):
        captured = {}

        def fake_get_task_context(*args, **kwargs):
            raise RuntimeError("boom")

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {
                "ok": True,
                "stdout": json.dumps({"task_id": "TASK__mcp", "must_read": [], "planning_mode": "standard"}),
                "stderr": "",
                "returncode": 0,
            }

        original_get = harness_server.harness_api.get_task_context
        original_run = harness_server._run_script
        harness_server.harness_api.get_task_context = fake_get_task_context
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "task_context",
                {
                    "task_dir": "/tmp/TASK__mcp",
                    "team_worker": "reviewer",
                    "agent_name": "harness:writer:reviewer",
                },
            )
        finally:
            harness_server.harness_api.get_task_context = original_get
            harness_server._run_script = original_run

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertIn("--team-worker", captured["argv"])
        self.assertIn("reviewer", captured["argv"])
        self.assertIn("--agent-name", captured["argv"])
        self.assertIn("harness:writer:reviewer", captured["argv"])
        self.assertEqual(result["structuredContent"]["fetch"]["fallback_from"]["method"], "direct")

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_verify_run_rejects_invalid_mode(self):
        result = harness_server.call_tool("verify_run", {"mode": "nope"})
        self.assertTrue(result.get("isError"))
        self.assertIn("mode must be one of", result["structuredContent"]["error"])

    def test_write_doc_sync_forwards_team_worker_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__team_doc_sync")
            captured = {}

            def fake_run_script(script_name, argv, env=None):
                captured["script_name"] = script_name
                captured["argv"] = list(argv)
                captured["env"] = dict(env or {})
                return {"ok": True, "stdout": "ok", "stderr": "", "returncode": 0}

            original = harness_server._run_script
            harness_server._run_script = fake_run_script
            try:
                result = harness_server.call_tool(
                    "write_doc_sync",
                    {
                        "task_dir": task_dir,
                        "what_changed": "aligned docs",
                        "team_worker": "reviewer",
                        "agent_name": "harness:writer@reviewer",
                    },
                )
            finally:
                harness_server._run_script = original

            self.assertNotIn("isError", result)
            self.assertEqual(captured["script_name"], "write_artifact.py")
            self.assertEqual(captured["env"].get("HARNESS_SKIP_PREWRITE"), "1")
            self.assertEqual(captured["env"].get("HARNESS_TEAM_WORKER"), "reviewer")
            self.assertEqual(captured["env"].get("CLAUDE_AGENT_NAME"), "harness:writer@reviewer")

    def test_record_agent_run_forwards_json_cli_call(self):
        captured = {}

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {
                "ok": True,
                "stdout": json.dumps({
                    "ok": True,
                    "task_dir": "/tmp/TASK__mcp",
                    "agent_name": "developer",
                    "count_before": 0,
                    "count_after": 1,
                }),
                "stderr": "",
                "exit_code": 0,
            }

        original = harness_server._run_script
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "record_agent_run",
                {"task_dir": "/tmp/TASK__mcp", "agent_name": "developer", "count": 1},
            )
        finally:
            harness_server._run_script = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertEqual(captured["argv"][:8], [
            "record-agent-run",
            "--task-dir", "/tmp/TASK__mcp",
            "--agent-name", "developer",
            "--count", "1",
            "--json",
        ])
        self.assertEqual(result["structuredContent"]["record_agent_run"]["count_after"], 1)

    def test_team_bootstrap_forwards_write_files_flag(self):
        captured = {}

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {"ok": True, "stdout": json.dumps({"task_id": "TASK__team", "ready": True, "workers": []}), "stderr": "", "returncode": 0}

        original = harness_server._run_script
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "team_bootstrap",
                {"task_dir": "/tmp/TASK__team", "write_files": True},
            )
        finally:
            harness_server._run_script = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertEqual(captured["argv"][:4], ["team-bootstrap", "--task-dir", "/tmp/TASK__team", "--json"])
        self.assertIn("--write-files", captured["argv"])

    def test_team_dispatch_forwards_write_files_flag(self):
        captured = {}

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {"ok": True, "stdout": json.dumps({"task_id": "TASK__team", "ready": True, "workers": []}), "stderr": "", "returncode": 0}

        original = harness_server._run_script
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "team_dispatch",
                {"task_dir": "/tmp/TASK__team", "write_files": True},
            )
        finally:
            harness_server._run_script = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertEqual(captured["argv"][:4], ["team-dispatch", "--task-dir", "/tmp/TASK__team", "--json"])
        self.assertIn("--write-files", captured["argv"])

    def test_team_launch_forwards_execute_and_refresh_flags(self):
        captured = {}

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {"ok": True, "stdout": json.dumps({"task_id": "TASK__team", "ready": True, "target": "implementers", "execution": {"spawned": True}}), "stderr": "", "returncode": 0}

        original = harness_server._run_script
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "team_launch",
                {
                    "task_dir": "/tmp/TASK__team",
                    "write_files": True,
                    "execute": True,
                    "no_auto_refresh": True,
                    "target": "implementers",
                },
            )
        finally:
            harness_server._run_script = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertEqual(captured["argv"][:4], ["team-launch", "--task-dir", "/tmp/TASK__team", "--json"])
        self.assertIn("--write-files", captured["argv"])
        self.assertIn("--execute", captured["argv"])
        self.assertIn("--no-auto-refresh", captured["argv"])
        self.assertIn("implementers", captured["argv"])

    def test_team_relaunch_forwards_worker_phase_and_refresh_flags(self):
        captured = {}

        def fake_run_script(script_name, argv, env=None):
            captured["script_name"] = script_name
            captured["argv"] = list(argv)
            return {"ok": True, "stdout": json.dumps({"task_id": "TASK__team", "ready": True, "worker": "lead", "phase": "synthesis", "execution": {"spawned": True}}), "stderr": "", "returncode": 0}

        original = harness_server._run_script
        harness_server._run_script = fake_run_script
        try:
            result = harness_server.call_tool(
                "team_relaunch",
                {
                    "task_dir": "/tmp/TASK__team",
                    "write_files": True,
                    "execute": True,
                    "no_auto_refresh": True,
                    "worker": "lead",
                    "phase": "synthesis",
                },
            )
        finally:
            harness_server._run_script = original

        self.assertNotIn("isError", result)
        self.assertEqual(captured["script_name"], "hctl.py")
        self.assertEqual(captured["argv"][:4], ["team-relaunch", "--task-dir", "/tmp/TASK__team", "--json"])
        self.assertIn("--write-files", captured["argv"])
        self.assertIn("--execute", captured["argv"])
        self.assertIn("--no-auto-refresh", captured["argv"])
        self.assertIn("--worker", captured["argv"])
        self.assertIn("lead", captured["argv"])
        self.assertIn("--phase", captured["argv"])
        self.assertIn("synthesis", captured["argv"])


if __name__ == "__main__":
    unittest.main()
