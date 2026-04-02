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
            "task_update_from_git_diff",
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

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_verify_run_rejects_invalid_mode(self):
        result = harness_server.call_tool("verify_run", {"mode": "nope"})
        self.assertTrue(result.get("isError"))
        self.assertIn("mode must be one of", result["structuredContent"]["error"])


if __name__ == "__main__":
    unittest.main()
