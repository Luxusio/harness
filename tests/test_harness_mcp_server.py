"""Tests for the plugin-local harness MCP server."""

import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
assert spec and spec.loader
harness_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness_server)


EXPECTED_TOOLS = {
    "goal_start",
    "goal_context",
    "goal_add_task",
    "goal_next_task",
    "goal_finish",
    "task_start",
    "task_context",
    "task_verify",
    "task_close",
    "task_blocked",
    "write_plan",
}


class HarnessMcpServerTests(unittest.TestCase):
    def _make_task(self, base_dir: str, task_id: str) -> str:
        task_dir = Path(base_dir) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            "\n".join(
                [
                    f"task_id: {task_id}",
                    "status: created",
                    "runtime_verdict: pending",
                    "touched_paths: []",
                    "plan_session_state: closed",
                    "closed_at: null",
                    "updated: 2026-01-01T00:00:00Z",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
        return str(task_dir)

    def _write_subagent_receipt(
        self,
        task_dir: str,
        *,
        agent_id: str = "agent-1",
        agent_type: str = "harness:qa-cli",
        source: str = "subagent_start_hook",
    ) -> None:
        receipt = {
            "receipt_id": f"subagent-{agent_id}",
            "ts": "2026-01-01T00:00:01Z",
            "kind": "subagent",
            "source": source,
            "status": "completed",
            "task_id": Path(task_dir).name,
            "agent_id": agent_id,
            "agent_type": agent_type,
            "lens": "qa-cli",
            "verdict": "PASS",
            "summary": "VERDICT: PASS",
            "transcript_path": "",
            "transcript_sha256": "",
            "prompt_hash": "",
        }
        (Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl").write_text(
            json.dumps(receipt, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_server_info_is_harness(self):
        self.assertEqual(harness_server.SERVER_INFO["name"], "harness")
        self.assertEqual(harness_server.SERVER_INFO["title"], "harness Control Plane")

    def test_tool_registry_matches_expected_tool_surface(self):
        tools = {tool["name"] for tool in harness_server.list_tools()}
        self.assertEqual(tools, EXPECTED_TOOLS)

    def test_each_tool_has_description_and_schema(self):
        for tool in harness_server.list_tools():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertTrue(tool["description"], f"{tool['name']} missing description")

    def test_start_only_receipt_does_not_produce_runtime_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__start-only")
            receipt = {
                "kind": "subagent",
                "status": "started",
                "task_id": "TASK__start-only",
                "agent_id": "qa-1",
                "agent_type": "harness:qa-cli",
                "lens": "qa-cli",
                "verdict": "",
                "ts": "2099-01-01T00:00:00Z",
            }
            (Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl").write_text(
                json.dumps(receipt) + "\n", encoding="utf-8"
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_completed_qa_fail_controls_runtime_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qa-fail")
            harness_server.record_subagent_receipt(
                task_dir,
                {
                    "agent_id": "qa-1",
                    "agent_type": "harness:qa-cli",
                    "status": "completed",
                    "verdict": "FAIL",
                    "summary": "VERDICT: FAIL",
                },
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "FAIL")

    def test_new_qa_start_invalidates_older_completed_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qa-restarted")
            self._write_subagent_receipt(task_dir, agent_id="qa-old")
            harness_server.record_subagent_receipt(
                task_dir,
                {
                    "agent_id": "qa-new",
                    "agent_type": "harness:qa-cli",
                    "status": "started",
                    "summary": "rerun started",
                },
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_goal_tools_manage_active_goal_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_find_repo_root = harness_server.find_repo_root
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                start = harness_server.call_tool(
                    "goal_start",
                    {
                        "objective": "Fix every bug on the login page",
                        "source": {"runtime": "codex"},
                    },
                )
                self.assertNotIn("isError", start)
                goal = start["structuredContent"]["goal"]
                self.assertEqual(goal["status"], "active")
                self.assertNotIn("strategy", goal)

                add = harness_server.call_tool(
                    "goal_add_task",
                    {
                        "task_id": "TASK__login-bugs",
                        "title": "Audit and fix login bugs",
                        "status": "queued",
                    },
                )
                self.assertNotIn("isError", add)

                nxt = harness_server.call_tool("goal_next_task", {})
                self.assertEqual(nxt["structuredContent"]["task"]["task_id"], "TASK__login-bugs")

                finish = harness_server.call_tool("goal_finish", {"status": "complete"})
                self.assertEqual(finish["structuredContent"]["goal"]["status"], "complete")
                self.assertTrue((Path(tmp) / "doc" / "harness" / "goals" / "current.json").is_file())
            finally:
                harness_server.find_repo_root = original_find_repo_root

    def test_stdio_transport_accepts_content_length_frames(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        body = json.dumps(request).encode()
        stdin = io.TextIOWrapper(
            io.BytesIO(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body),
            encoding="utf-8",
        )
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()
            server.close()

        raw = stdout_bytes.getvalue()
        self.assertTrue(raw.startswith(b"Content-Length: "), raw)
        response_body = raw.split(b"\r\n\r\n", 1)[1]
        response = json.loads(response_body.decode())
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "harness")

    def test_initialize_instructions_match_current_codex_mcp_contract(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "codex-cli"}},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        server.close()
        instructions = response["result"]["instructions"]
        self.assertIn("Goal-first control plane", instructions)
        self.assertIn("goal_start", instructions)
        self.assertIn("plain repo-mutating request", instructions)
        self.assertIn("hooks do not create tasks automatically", instructions)
        self.assertIn("bare tool names", instructions)
        self.assertIn("Codex callers should use these bare tool names directly", instructions)
        self.assertIn("get_goal", instructions)
        self.assertIn("write_plan", instructions)
        self.assertNotIn("write_plan_artifact", instructions)
        self.assertNotIn("write_handoff", instructions)
        self.assertNotIn("write_doc_sync", instructions)
        self.assertNotIn("write_req_doc", instructions)
        self.assertNotIn("record_attempt", instructions)
        self.assertNotIn("7 tools", instructions)
        self.assertIn("mcp__plugin_harness_harness__", instructions)
        self.assertIn("do not use Claude display prefixes", instructions)
        self.assertNotIn("write_critic_runtime", instructions)

    def test_codex_initialize_hosts_and_closes_watcher_manager(self):
        manager = mock.Mock()
        manager.start.return_value = manager
        server = harness_server.McpServer()
        with (
            mock.patch.object(harness_server, "_WatcherManager", return_value=manager) as factory,
            mock.patch.object(harness_server, "find_repo_root", return_value="/trusted/repo"),
            mock.patch.object(server, "_reply"),
        ):
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "codex-cli"}},
            })
            server.close()
        factory.assert_called_once_with("/trusted/repo")
        manager.start.assert_called_once_with()
        manager.stop.assert_called_once_with()

    def test_watcher_manager_failure_does_not_break_codex_initialize(self):
        server = harness_server.McpServer()
        with (
            mock.patch.object(harness_server, "_WatcherManager", side_effect=RuntimeError("boom")),
            mock.patch.object(server, "_reply") as reply,
        ):
            server.handle_request({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"clientInfo": {"name": "codex-cli"}},
            })
        self.assertIsNone(server.watcher_manager)
        reply.assert_called_once()

    def test_initialize_instructions_match_current_claude_mcp_contract(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "claude-code"}},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with (
            mock.patch.dict(harness_server.os.environ, {"HARNESS_RUNTIME": ""}),
            mock.patch.object(harness_server.sys, "stdin", stdin),
            mock.patch.object(harness_server.sys, "stdout", stdout),
        ):
            server.handle_request(server._read())
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        instructions = response["result"]["instructions"]
        self.assertIn("Goal-first control plane", instructions)
        self.assertIn("goal_context", instructions)
        self.assertIn("plain repo-mutating request", instructions)
        self.assertIn("Protocol tool names are bare", instructions)
        self.assertIn("Claude Code may display callable tools with a runtime prefix", instructions)
        self.assertNotIn("7 tools", instructions)
        self.assertNotIn("write_critic_runtime", instructions)

    def test_stdio_transport_accepts_lowercase_content_length_with_extra_headers(self):
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "ping",
            "params": {},
        }
        body = json.dumps(request).encode()
        frame = (
            b"content-length: "
            + str(len(body)).encode()
            + b"\r\nx-test-header: ignored\r\n\r\n"
            + body
        )
        stdin = io.TextIOWrapper(io.BytesIO(frame), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request(server._read())
            stdout.flush()

        raw = stdout_bytes.getvalue()
        self.assertTrue(raw.startswith(b"Content-Length: "), raw)
        response = json.loads(raw.split(b"\r\n\r\n", 1)[1].decode())
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_stdio_transport_reads_multiple_content_length_frames_from_one_stream(self):
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        tools_list = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        def frame(payload: dict) -> bytes:
            body = json.dumps(payload).encode()
            return b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

        stdin = io.TextIOWrapper(io.BytesIO(frame(initialize) + frame(tools_list)), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            first = server._read()
            second = server._read()
            server.handle_request(first)
            server.handle_request(second)
            stdout.flush()

        raw = stdout_bytes.getvalue()
        parts = raw.split(b"Content-Length: ")
        self.assertEqual(len(parts), 3, raw)
        responses = []
        for part in parts[1:]:
            _, body = part.split(b"\r\n\r\n", 1)
            responses.append(json.loads(body.decode()))
        self.assertEqual([response["id"] for response in responses], [1, 2])
        tool_names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        self.assertEqual(tool_names, EXPECTED_TOOLS)

    def test_stdio_transport_keeps_json_line_responses_for_json_line_requests(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode() + b"\n"), encoding="utf-8")
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request(server._read())
            stdout.flush()

        raw = stdout_bytes.getvalue()
        self.assertFalse(raw.startswith(b"Content-Length:"), raw)
        response = json.loads(raw.decode())
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "harness")

    def test_stdio_transport_returns_none_for_header_without_content_length(self):
        stdin = io.TextIOWrapper(io.BytesIO(b"X-Test: ignored\r\n\r\n{}"), encoding="utf-8")

        server = harness_server.McpServer()
        with mock.patch.object(harness_server.sys, "stdin", stdin):
            self.assertIsNone(server._read())
        self.assertTrue(server.framed_stdio)

    def test_initialized_notification_sets_state_without_response(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            stdout.flush()

        self.assertTrue(server.initialized)
        self.assertEqual(stdout_bytes.getvalue(), b"")

    def test_tools_call_requires_string_name(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()
        server.framed_stdio = False

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": 123}})
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        self.assertEqual(response["id"], 9)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Tool name must be a string", response["error"]["message"])

    def test_unknown_method_returns_jsonrpc_method_not_found(self):
        stdout_bytes = io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        server = harness_server.McpServer()
        server.framed_stdio = False

        with mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request({"jsonrpc": "2.0", "id": 10, "method": "unknown/method"})
            stdout.flush()

        response = json.loads(stdout_bytes.getvalue().decode())
        self.assertEqual(response["id"], 10)
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("Method not found", response["error"]["message"])

    def test_task_context_returns_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__mcp")
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool("task_context", {"task_id": "TASK__mcp"})
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertNotIn("isError", result)
            structured = result["structuredContent"]
            self.assertEqual(structured["task_context"]["task_id"], "TASK__mcp")

    def test_critic_tools_are_not_exposed(self):
        for tool in ("write_critic_document", "write_critic_qa", "write_critic_ux"):
            result = harness_server.call_tool(
                tool,
                {
                    "task_id": "TASK__removed",
                    "verdict": "PASS",
                    "summary": "self-authored pass",
                    "transcript": "not a receipt",
                },
            )
            self.assertTrue(result.get("isError"), tool)
            self.assertIn("Unknown tool", result["structuredContent"]["error"], tool)

    def test_task_verify_reconcile_skips_without_subagent_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qapromote")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "one"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "two"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qapromote", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["ac_reconcile"]["promoted_acs"], [])
        self.assertIn("QA completion", result["structuredContent"]["ac_reconcile"]["reason"])
        self.assertEqual(body.count("status: open"), 2)

    def test_task_verify_reconcile_promotes_open_acs_from_subagent_start_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qareconcile")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "one"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "two"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            self._write_subagent_receipt(task_dir)
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qareconcile", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", verify)
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001", "AC-002"])
        self.assertEqual(body.count("status: passed"), 2)
        self.assertIn("evidence: SUBAGENT_RECEIPTS.jsonl task_verify PASS", body)

    def test_task_verify_reconcile_skips_failed_deferred_and_without_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qaskip")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "open"\n  status: open\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-002\n  title: "failed"\n  status: failed\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n'
                '- id: AC-003\n  title: "deferred"\n  status: deferred\n  kind: functional\n'
                '  last_updated: 2026-01-01T00:00:00Z\n  evidence: ""\n',
                encoding="utf-8",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaskip", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], [])
        self.assertIn("QA completion", verify["structuredContent"]["ac_reconcile"]["reason"])
        self.assertIn("status: open", body)
        self.assertIn("status: failed", body)
        self.assertIn("status: deferred", body)

    def test_task_verify_reconcile_promotes_plan_writer_indented_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qaindent")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                "version: 1\nchecks:\n"
                "  - id: AC-001\n"
                "    description: one\n"
                "    status: open\n"
                "    evidence: []\n",
                encoding="utf-8",
            )
            self._write_subagent_receipt(task_dir)
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaindent", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001"])
        self.assertIn("status: passed", body)

    def test_record_ac_evidence_is_not_exposed(self):
        result = harness_server.call_tool(
            "record_ac_evidence",
            {
                "task_id": "TASK__removed",
                "ac_id": "AC-001",
                "evidence": "self-authored claim",
            },
        )
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_record_subagent_receipt_is_not_exposed_and_task_verify_surfaces_hook_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__subagentreceipt")
            self._write_subagent_receipt(
                task_dir,
                agent_id="agent-123",
                agent_type="harness:qa-cli",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "record_subagent_receipt",
                    {
                        "task_id": "TASK__subagentreceipt",
                        "source": "spawn_agent",
                        "agent_id": "agent-123",
                        "agent_type": "harness:qa-cli",
                        "verdict": "PASS",
                        "summary": "qa-cli passed focused checks",
                    },
                )
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__subagentreceipt"},
                )
                receipt_path = Path(task_dir) / "SUBAGENT_RECEIPTS.jsonl"
                receipt_exists = receipt_path.is_file()
                receipt = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[0]) if receipt_exists else {}
            finally:
                harness_server.canonical_task_dir = original_ctd
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])
        self.assertTrue(receipt_exists)
        self.assertEqual(receipt["agent_id"], "agent-123")
        self.assertEqual(receipt["lens"], "qa-cli")
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["count"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_lens"]["qa-cli"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_agent_type"]["harness:qa-cli"], 1)
        self.assertEqual(verify["structuredContent"]["subagent_receipts"]["by_source"]["subagent_start_hook"], 1)

    def test_micro_execution_mode_allows_no_plan_but_still_requires_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "TASK__micro"
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: str(task_dir)
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                start = harness_server.call_tool(
                    "task_start",
                    {"task_id": "TASK__micro", "execution_mode": "micro"},
                )
                close = harness_server.call_tool("task_close", {"task_id": "TASK__micro"})
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
        ctx = start["structuredContent"]["task_context"]
        self.assertTrue(ctx["source_write_allowed"])
        self.assertEqual(ctx["routing"]["execution_mode"], "micro")
        self.assertNotIn("PLAN.md", ctx["missing_for_close"])
        self.assertIn("subagent", ctx["next_action"])
        self.assertTrue(close.get("isError"))
        self.assertIn("completed QA verdict: qa-cli", close["structuredContent"]["missing_for_close"])

    def test_task_blocked_records_pause_state_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__blocked")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_blocked",
                    {
                        "task_id": "TASK__blocked",
                        "blocked_reason": "CI service is unavailable on this host.",
                        "unblock_condition": "Run CI where the service exists.",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
            self.assertNotIn("isError", result)
            self.assertEqual(result["structuredContent"]["status"], "blocked")
            body = (Path(task_dir) / "BLOCKED.md").read_text(encoding="utf-8")
            self.assertIn("CI service is unavailable", body)
            state = (Path(task_dir) / "TASK_STATE.yaml").read_text(encoding="utf-8")
            self.assertIn("status: blocked", state)
            self.assertIn("runtime_verdict: BLOCKED_ENV", state)

    def test_task_start_explicitly_resumes_blocked_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-blocked")
            state_path = Path(task_dir) / "TASK_STATE.yaml"
            state = state_path.read_text(encoding="utf-8")
            state_path.write_text(
                state.replace("status: created", "status: blocked").replace(
                    "runtime_verdict: pending", "runtime_verdict: BLOCKED_ENV"
                ),
                encoding="utf-8",
            )
            (Path(task_dir) / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_start", {"task_id": "TASK__resume-blocked"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertNotIn("isError", result)
            context = result["structuredContent"]["task_context"]
            self.assertEqual(context["status"], "created")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())

    def test_write_plan_writes_plan_meta_checks_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__planmcp")
            result = harness_server.call_tool(
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# MCP Plan\n",
                    "checks": "- id: AC-001\n  title: x\n  status: open\n",
                    "audit": "| 1 | p | d | c | p | r | - |\n",
                    "meta": {"routing": "light"},
                },
            )
            self.assertNotIn("isError", result)
            self.assertEqual(
                result["structuredContent"]["written"],
                ["PLAN.md", "PLAN.meta.json", "CHECKS.yaml", "AUDIT_TRAIL.md"],
            )
            bytes_written = result["structuredContent"]["bytes_written"]
            self.assertGreater(bytes_written["PLAN.md"], 0)
            self.assertGreater(bytes_written["PLAN.meta.json"], 0)
            self.assertGreater(bytes_written["CHECKS.yaml"], 0)
            self.assertGreater(bytes_written["AUDIT_TRAIL.md"], 0)
            self.assertEqual((Path(task_dir) / "PLAN.md").read_text(encoding="utf-8"), "# MCP Plan\n")
            meta = json.loads((Path(task_dir) / "PLAN.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["author_role"], "plan-skill")
            self.assertEqual(meta["plan_meta"]["routing"], "light")
            self.assertIn("AC-001", (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8"))
            self.assertIn("| 1 |", (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8"))

    def test_write_plan_rejects_empty_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyplan")
            result = harness_server.call_tool(
                "write_plan",
                {"task_dir": task_dir, "plan": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty PLAN.md", result["structuredContent"]["error"])

    def test_write_plan_rejects_empty_optional_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptychecks")
            result = harness_server.call_tool(
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "checks": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty CHECKS.yaml", result["structuredContent"]["error"])

    def test_write_plan_rejects_empty_optional_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyaudit")
            result = harness_server.call_tool(
                "write_plan",
                {"task_dir": task_dir, "plan": "# Plan\n", "audit": "\n"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty AUDIT_TRAIL.md", result["structuredContent"]["error"])

    def test_write_plan_appends_audit_header_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__auditmcp")
            for row in ("| 1 | p | d | c | p | r | - |\n", "| 2 | p | d2 | c | p | r | - |\n"):
                result = harness_server.call_tool(
                    "write_plan",
                    {"task_dir": task_dir, "plan": "# Plan\n", "audit": row},
                )
                self.assertNotIn("isError", result)
            body = (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("| # | phase | decision | classification | principle | rationale | rejected_option |"), 1)
            self.assertIn("| 1 |", body)
            self.assertIn("| 2 |", body)


class HarnessMcpServerPR2CloseGate(unittest.TestCase):
    """AC-001..AC-006: CHECKS gate + runtime-stale gate in task_close / task_verify."""

    def _prepare_task(self, base: str, task_id: str, *, checks_yaml: str | None,
                      write_receipt: bool = True, write_handoff: bool = True,
                      touched_paths: list[str] | None = None,
                      handoff_body: str | None = None) -> str:
        repo = Path(base)
        git_dir = repo / ".git"
        if git_dir.is_dir() and not (git_dir / "HEAD").exists():
            git_dir.rmdir()
        if not git_dir.exists():
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "mcp@test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "MCP Test"], cwd=repo, check=True)
        (repo / ".gitignore").write_text("TASK__*/\n", encoding="utf-8")
        for rel in (
            "plugin/skills/run/self-improvement.md",
            "plugin/scripts/_lib.py",
            "plugin/scripts/health.py",
            "plugin/CLAUDE.md",
            "README.md",
        ):
            p = Path(base) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("# fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo)
        if staged.returncode != 0:
            subprocess.run(["git", "commit", "-qm", "fixture baseline"], cwd=repo, check=True)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        task_dir = Path(base) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        tp = touched_paths or []
        tp_yaml = "[]" if not tp else "\n" + "\n".join(f"  - {p}" for p in tp)
        (task_dir / "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            f"status: created\n"
            f"runtime_verdict: pending\n"
            f"touched_paths: {tp_yaml}\n"
            f"plan_session_state: closed\n"
            f"closed_at: null\n"
            f"updated: 2026-04-19T15:00:00Z\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        (task_dir / "TASK_BASELINE.json").write_text(
            json.dumps({"version": 1, "head_sha": head_sha, "dirty_paths": {}}) + "\n",
            encoding="utf-8",
        )
        if write_handoff:
            default_handoff = "# handoff\n\n## Commit-backed Learnings\n\nStatus: none\n"
            body = handoff_body or default_handoff
            if "Self-Healing Candidates" not in body:
                body = body.rstrip() + "\n\n## Self-Healing Candidates\n\nStatus: none\n"
            (task_dir / "HANDOFF.md").write_text(
                body,
                encoding="utf-8",
            )
        if write_receipt:
            review_types = {
                "review-code": "harness:code-reviewer",
                "review-security": "harness:security-reviewer",
            }
            for lens in harness_server.required_review_lenses(task_dir):
                for status, verdict in (("started", ""), ("completed", "PASS")):
                    harness_server.record_subagent_receipt(task_dir, {
                        "source": "subagent_start_hook" if status == "started" else "subagent_stop_hook",
                        "status": status,
                        "agent_id": f"{lens}-{task_id}",
                        "agent_type": review_types[lens],
                        "verdict": verdict,
                        "summary": (
                            "VERDICT: PASS\n"
                            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
                            if verdict else "review started"
                        ),
                    })
            for status, verdict in (("started", ""), ("completed", "PASS")):
                harness_server.record_subagent_receipt(task_dir, {
                    "source": "subagent_start_hook" if status == "started" else "subagent_stop_hook",
                    "status": status,
                    "agent_id": f"agent-{task_id}",
                    "agent_type": "harness:qa-cli",
                    "verdict": verdict,
                    "summary": f"VERDICT: {verdict}" if verdict else "qa started",
                })
        if checks_yaml is not None:
            (task_dir / "CHECKS.yaml").write_text(checks_yaml, encoding="utf-8")
        return str(task_dir)

    def _patch(self, task_dir: str):
        """Patch canonical_task_dir + sync_from_git_diff to isolate from git state."""
        self._orig_ctd = harness_server.canonical_task_dir
        self._orig_sync = harness_server.sync_from_git_diff
        harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
        harness_server.sync_from_git_diff = lambda td: []

    def _unpatch(self):
        harness_server.canonical_task_dir = self._orig_ctd
        harness_server.sync_from_git_diff = self._orig_sync

    def _patch_repo_root_for_context(self, repo_root: str):
        self._orig_context_find_repo_root = harness_server.emit_compact_context.__globals__["find_repo_root"]
        self._orig_context_git_changed_paths = harness_server.emit_compact_context.__globals__["_git_changed_paths"]
        harness_server.emit_compact_context.__globals__["find_repo_root"] = (
            lambda *args, **kw: repo_root
        )

    def _set_context_git_changed_paths(self, paths: list[str]):
        harness_server.emit_compact_context.__globals__["_git_changed_paths"] = (
            lambda repo_root, *args, **kwargs: (
                {path: "sha256:test" for path in paths}
                if kwargs.get("with_fingerprints")
                else set(paths)
            )
        )

    def _unpatch_repo_root_for_context(self):
        harness_server.emit_compact_context.__globals__["find_repo_root"] = (
            self._orig_context_find_repo_root
        )
        harness_server.emit_compact_context.__globals__["_git_changed_paths"] = (
            self._orig_context_git_changed_paths
        )

    def test_context_surfaces_feedback_ids_without_handoff_close_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__feedback-next-action-ids",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "USER_FEEDBACK.jsonl").write_text(
                json.dumps({"id": "ufe-needed", "prompt_excerpt": "remember this"}) + "\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_context", {"task_id": "TASK__feedback-next-action-ids"}
                )
            finally:
                self._unpatch()
        ctx = result["structuredContent"]["task_context"]
        self.assertEqual(ctx["unresolved_feedback_ids"], ["ufe-needed"])
        self.assertNotIn("User feedback disposition", ctx["missing_for_close"])
        self.assertNotIn("ufe-needed", ctx["next_action"])

    def test_conversation_open_item_blocks_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__conversation-open",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "CONVERSATION.md").write_text(
                "# Conversation\n\n"
                "<!-- harness:conversation-log v1 -->\n\n"
                "## 2026-06-23T00:00:00Z - User\n"
                "사용자가 새 요구사항을 말했다.\n"
                "<!-- item: type=requirement status=open key=reader-back-stack -->\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__conversation-open"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        ctx = result["structuredContent"]["task_context"]
        self.assertIn("CONVERSATION.md open items", ctx["missing_for_close"])
        self.assertEqual(ctx["conversation_open_items"][0]["key"], "reader-back-stack")
        self.assertIn("CONVERSATION.md open item markers", ctx["next_action"])

    def test_conversation_captured_item_does_not_block_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__conversation-captured",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            Path(td, "CONVERSATION.md").write_text(
                "# Conversation\n\n"
                "<!-- harness:conversation-log v1 -->\n\n"
                "<!-- item: type=requirement status=captured key=reader-back-stack ref=doc/ui/REQ__reader.md -->\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__conversation-captured"}
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-001: failed AC blocks close ----
    def test_close_rejects_failed_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-001",
                checks_yaml=(
                    '- id: AC-001\n  title: "done"\n  status: passed\n  kind: functional\n'
                    '- id: AC-002\n  title: "not done"\n  status: failed\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-001"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        err = result["structuredContent"]
        self.assertIn("CHECKS gate", err["error"])
        blockers = err["blocking_acs"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["id"], "AC-002")
        self.assertEqual(blockers[0]["status"], "failed")

    def test_close_rejects_open_ac(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-001b",
                checks_yaml=(
                    '- id: AC-001\n  title: "ac1"\n  status: open\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-001b"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertEqual(result["structuredContent"]["blocking_acs"][0]["status"], "open")

    # ---- AC-002: all-passed closes cleanly ----
    def test_close_passes_with_all_acs_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-002",
                checks_yaml=(
                    '- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n'
                    '- id: AC-002\n  title: "y"\n  status: deferred\n  kind: functional\n'
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-002"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_uses_git_diff_fallback_for_missing_req_when_touched_paths_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: browser\nqa:\n  browser_qa_supported: true\n",
                encoding="utf-8",
            )
            td = self._prepare_task(
                tmp,
                "TASK__req-git-fallback",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=[],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            self._set_context_git_changed_paths(["src/mobile/Reader.tsx"])
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__req-git-fallback"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn(
            "REQ durable doc for UI observable behavior",
            result["structuredContent"]["missing_for_close"],
        )

    def test_close_requires_req_for_user_feedback_observable_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text("type: browser\n", encoding="utf-8")
            td = self._prepare_task(
                tmp,
                "TASK__req-feedback",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=[],
            )
            (Path(td) / "USER_FEEDBACK.md").write_text(
                "Native Android APK/emulator back-stack behavior for the reader "
                "must be verified; browser mobile is not enough.\n",
                encoding="utf-8",
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            self._set_context_git_changed_paths([])
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__req-feedback"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn(
            "REQ durable doc for observable behavior or user feedback",
            result["structuredContent"]["missing_for_close"],
        )

    def test_close_uses_subagent_receipt_not_ux_critic_file_for_cli_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-required",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__ux-cli-required"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_accepts_required_ux_cli_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-pass",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__ux-cli-pass"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_ignores_absent_or_stale_ux_critic_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: cli\nqa:\n  ux_review_supported: true\n",
                encoding="utf-8",
            )
            source = Path(tmp) / "src/cli/main.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('hi')\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__ux-cli-stale",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["src/cli/main.py"],
            )
            critic = Path(td) / "CRITIC__ux.md"
            critic.write_text("stale legacy critic\n", encoding="utf-8")
            future = os.path.getmtime(Path(td) / "SUBAGENT_RECEIPTS.jsonl") + 10
            os.utime(critic, (future, future))
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__ux-cli-stale"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_does_not_require_ux_for_non_applicable_library_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir(exist_ok=True)
            (Path(tmp) / "doc/harness").mkdir(parents=True, exist_ok=True)
            (Path(tmp) / "doc/harness/manifest.yaml").write_text(
                "type: library\nqa:\n  ux_review_supported: false\n",
                encoding="utf-8",
            )
            td = self._prepare_task(
                tmp, "TASK__ux-not-applicable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                touched_paths=["plugin/scripts/_lib.py"],
            )
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__ux-not-applicable"}
                )
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-003: missing CHECKS.yaml warn-passes + logs ----
    def test_close_warn_passes_without_checks_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(tmp, "TASK__pr2-003", checks_yaml=None)
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-003"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-004: completed QA must be newer than touched source ----
    def test_close_rejects_receipt_when_touched_path_is_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-004",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            receipt_path = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
            receipts[-1]["ts"] = "2000-01-01T00:00:01Z"
            receipt_path.write_text(
                "".join(json.dumps(item) + "\n" for item in receipts), encoding="utf-8"
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-004"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn("stale", result["content"][0]["text"])

    # ---- AC-006: task_verify derives PASS from subagent receipt ----
    def test_verify_reports_receipt_pass_without_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_verify", {"task_id": "TASK__pr2-006"})
            finally:
                self._unpatch()
            # Read state while tempdir still exists
            state = (Path(td) / "TASK_STATE.yaml").read_text(encoding="utf-8")
        s = result["structuredContent"]
        self.assertFalse(s["stale"])
        self.assertEqual(s["stale_path"], "")
        self.assertEqual(s["runtime_verdict"], "PASS")
        self.assertIn("runtime_verdict: PASS", state)

    def test_stale_skip_list_ignores_pyc(self):
        """Stale check must not trip on Python cache files."""
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006b",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/__pycache__/health.cpython-311.pyc"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006b"})
            finally:
                self._unpatch()
        # pyc skip path — should close cleanly (not stale)
        self.assertNotIn("isError", result,
                         f"__pycache__ pyc path should be skipped, not treated as stale: {result}")

    def test_stale_check_ignores_task_artifacts_after_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-artifact",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["doc/harness/tasks/TASK__pr2-artifact/HANDOFF.md"],
            )
            handoff = Path(td) / "HANDOFF.md"
            handoff.write_text(
                "# handoff after qa\n\n"
                "## Commit-backed Learnings\n\n"
                "Status: none\n\n"
                "## Self-Healing Candidates\n\n"
                "Status: none\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-artifact"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)

    def test_stale_check_ignores_deleted_touched_path(self):
        """Deleted files in touched_paths must not stale a fresh QA verdict forever."""
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006c",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/deleted_install_helper.py"],
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006c"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result,
                         f"deleted touched path should not be permanently stale: {result}")

    def test_close_refreshes_snapshot_and_blocks_if_final_gate_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            changed = {"missing_for_close": ["fresh review receipt"], "next_action": "verify"}
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=[initial, changed]) as emit,
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
                mock.patch.object(harness_server, "refresh_review_snapshot") as refresh,
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__close-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])
        self.assertEqual(emit.call_count, 2)
        self.assertEqual(refresh.call_count, 2)

    def test_close_blocks_when_initial_git_head_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__head-unavailable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "_git_head_for_receipt", return_value=""),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__head-unavailable"})

        self.assertTrue(result.get("isError"))
        self.assertIn("Git HEAD unavailable", result["content"][0]["text"])

    def test_close_blocks_when_initial_git_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__initial-git-failure",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_changed_path_fingerprints",
                    side_effect=RuntimeError("snapshot unavailable"),
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__initial-git-failure"})

        self.assertTrue(result.get("isError"))
        self.assertIn("Git changed-path snapshot unavailable", result["content"][0]["text"])

    def test_close_blocks_when_final_git_head_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__final-head-unavailable",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "_git_head_for_receipt", side_effect=["a" * 40, ""]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__final-head-unavailable"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_blocks_when_final_git_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__final-git-failure",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_changed_path_fingerprints",
                    side_effect=[set(), RuntimeError("snapshot unavailable")],
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__final-git-failure"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final Git changed-path snapshot unavailable", result["content"][0]["text"])

    def test_close_blocks_when_changed_path_fingerprint_map_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__snapshot-map-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(
                    harness_server, "_changed_path_fingerprints",
                    side_effect=[{"src/a.py": "sha256:old"}, {"src/a.py": "sha256:new"}],
                ),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__snapshot-map-race"})

        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["snapshot_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_handlers_compute_git_path_snapshot_once_per_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__snapshot-count",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            globals_ = harness_server.emit_compact_context.__globals__
            original = globals_["_uncached_git_changed_paths"]
            calls = 0

            def counted(repo_root):
                nonlocal calls
                calls += 1
                return original(repo_root)

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.dict(globals_, {"_uncached_git_changed_paths": counted}),
            ):
                context = harness_server.handle_task_context({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", context)
                self.assertEqual(calls, 1)

                calls = 0
                verified = harness_server.handle_task_verify({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", verified)
                self.assertEqual(calls, 1)

                calls = 0
                closed = harness_server.handle_task_close({"task_id": "TASK__snapshot-count"})
                self.assertNotIn("isError", closed)
                self.assertEqual(calls, 3)

    def test_close_real_source_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__source-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            source = Path(tmp) / "plugin/scripts/health.py"
            original_refresh = harness_server.refresh_review_snapshot

            def mutate_then_refresh():
                source.write_text("# changed during close\n", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=mutate_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__source-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_new_untracked_source_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__untracked-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            source = Path(tmp) / "plugin/scripts/new_during_close.py"
            original_refresh = harness_server.refresh_review_snapshot

            def create_then_refresh():
                source.write_text("VALUE = 1\n", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=create_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__untracked-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_head_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            readme = Path(tmp) / "README.md"
            original_refresh = harness_server.refresh_review_snapshot
            mutated = False

            def commit_then_refresh():
                nonlocal mutated
                if not mutated:
                    readme.write_text("# committed during close\n", encoding="utf-8")
                    subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True)
                    subprocess.run(["git", "commit", "-qm", "race commit"], cwd=tmp, check=True)
                    mutated = True
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=commit_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__head-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_head_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            readme = Path(tmp) / "README.md"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_commit(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    readme.write_text("# committed during final context\n", encoding="utf-8")
                    subprocess.run(["git", "add", "README.md"], cwd=tmp, check=True)
                    subprocess.run(["git", "commit", "-qm", "late race commit"], cwd=tmp, check=True)
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_commit),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-head-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_uncommitted_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-source-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            source = Path(tmp) / "plugin/scripts/health.py"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_mutate(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    source.write_text("# uncommitted during final context\n", encoding="utf-8")
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_mutate),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-source-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["snapshot_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_live_receipt_change_during_refresh_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__receipt-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            receipts = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            original_refresh = harness_server.refresh_review_snapshot

            def remove_receipts_then_refresh():
                receipts.write_text("", encoding="utf-8")
                original_refresh()

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "refresh_review_snapshot", side_effect=remove_receipts_then_refresh),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__receipt-race"})

        self.assertTrue(result.get("isError"))
        self.assertIn("final freshness changed", result["content"][0]["text"])

    def test_close_receipt_change_during_final_context_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__late-receipt-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            receipts = Path(td) / "SUBAGENT_RECEIPTS.jsonl"
            original_emit = harness_server.emit_compact_context
            calls = 0

            def emit_then_remove_receipts(task_dir):
                nonlocal calls
                calls += 1
                result = original_emit(task_dir)
                if calls == 2:
                    receipts.write_text("", encoding="utf-8")
                return result

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_remove_receipts),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__late-receipt-race"})

        self.assertEqual(calls, 2)
        self.assertTrue(result.get("isError"))
        self.assertTrue(result["structuredContent"]["receipt_stream_changed"])
        self.assertIn("final freshness changed", result["content"][0]["text"])


class HarnessTouchedPathSubmoduleTests(unittest.TestCase):
    def _git(self, cwd: str, *args: str):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True,
            capture_output=True, text=True,
        )

    def test_sync_from_git_diff_includes_initialized_submodule_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "init sub")

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            task_dir = parent / "doc" / "harness" / "tasks" / "TASK__submodule"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK_STATE.yaml").write_text(
                "task_id: TASK__submodule\n"
                "status: created\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-01-01T00:00:00Z\n",
                encoding="utf-8",
            )
            (parent / "services" / "api space" / "api.py").write_text("v2\n", encoding="utf-8")

            touched = harness_server.sync_from_git_diff(str(task_dir))
            self.assertIn("services/api space/api.py", touched)

    def test_close_blocks_clean_submodule_checkout_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(str(sub_src), "checkout", "-q", first)

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__submodule-head-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_checkout(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(str(parent / "services/api space"), "checkout", "-q", second)
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_checkout),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__submodule-head-race"})

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])

    def test_close_blocks_staged_gitlink_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            self._git(str(sub_src), "init", "-q")
            self._git(str(sub_src), "config", "user.email", "t@example.com")
            self._git(str(sub_src), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(str(sub_src), "checkout", "-q", first)

            self._git(str(parent), "init", "-q")
            self._git(str(parent), "config", "user.email", "t@example.com")
            self._git(str(parent), "config", "user.name", "T")
            self._git(
                str(parent), "-c", "protocol.file.allow=always",
                "submodule", "add", "-q", str(sub_src), "services/api space",
            )
            self._git(str(parent), "commit", "-qm", "add submodule")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__submodule-index-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_stage_gitlink(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(
                        str(parent), "update-index", "--cacheinfo",
                        f"160000,{second},services/api space",
                    )
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_stage_gitlink),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__submodule-index-race"})

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])

    def test_close_blocks_uninitialized_gitlink_index_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub_src = Path(tmp) / "sub-src"
            parent = Path(tmp) / "parent"
            sub_src.mkdir()
            parent.mkdir()
            for repo in (sub_src, parent):
                self._git(str(repo), "init", "-q")
                self._git(str(repo), "config", "user.email", "t@example.com")
                self._git(str(repo), "config", "user.name", "T")
            (sub_src / "api.py").write_text("v1\n", encoding="utf-8")
            self._git(str(sub_src), "add", "api.py")
            self._git(str(sub_src), "commit", "-qm", "v1")
            first = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            (sub_src / "api.py").write_text("v2\n", encoding="utf-8")
            self._git(str(sub_src), "commit", "-qam", "v2")
            second = self._git(str(sub_src), "rev-parse", "HEAD").stdout.strip()
            self._git(
                str(parent), "update-index", "--add", "--cacheinfo",
                f"160000,{first},ghost-sub",
            )
            self._git(str(parent), "commit", "-qm", "add uninitialized gitlink")

            gate = HarnessMcpServerPR2CloseGate()
            td = gate._prepare_task(
                str(parent), "TASK__uninitialized-gitlink-race",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            initial = {"missing_for_close": [], "next_action": "close"}
            calls = 0

            def emit_then_stage_gitlink(task_dir):
                nonlocal calls
                calls += 1
                if calls == 2:
                    self._git(
                        str(parent), "update-index", "--add", "--cacheinfo",
                        f"160000,{second},ghost-sub",
                    )
                return initial

            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "sync_from_git_diff", return_value=[]),
                mock.patch.object(harness_server, "emit_compact_context", side_effect=emit_then_stage_gitlink),
                mock.patch.object(harness_server, "_runtime_is_stale", side_effect=[(False, ""), (False, "")]),
                mock.patch.object(harness_server, "_checks_gate_status", side_effect=[("passed", []), ("passed", [])]),
            ):
                result = harness_server.handle_task_close(
                    {"task_id": "TASK__uninitialized-gitlink-race"},
                )

            self.assertTrue(result.get("isError"))
            self.assertTrue(result["structuredContent"]["snapshot_changed"])


if __name__ == "__main__":
    unittest.main()
