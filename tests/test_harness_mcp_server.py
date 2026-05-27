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
    "task_start",
    "task_context",
    "task_verify",
    "task_close",
    "task_blocked",
    "record_ac_evidence",
    "record_attempt",
    "write_critic_qa",
    "write_critic_ux",
    "write_req_doc",
    "write_plan_artifact",
    "write_critic_document",
    "write_handoff",
    "write_doc_sync",
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

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

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
        instructions = response["result"]["instructions"]
        self.assertIn("14 tools", instructions)
        self.assertIn("bare tool names", instructions)
        self.assertIn("Codex callers should use these bare tool names directly", instructions)
        self.assertIn("write_plan_artifact", instructions)
        self.assertNotIn("7 tools", instructions)
        self.assertIn("mcp__plugin_harness_harness__", instructions)
        self.assertIn("do not use Claude display prefixes", instructions)
        self.assertNotIn("write_critic_runtime", instructions)

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
        self.assertIn("14 tools", instructions)
        self.assertIn("Protocol tool names are bare", instructions)
        self.assertIn("Claude Code may display callable tools with a runtime prefix", instructions)
        self.assertIn("mcp__plugin_harness_harness__write_critic_qa", instructions)
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

    def test_write_critic_document_writes_artifact_without_runtime_verdict_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__doccritic")
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "write_critic_document",
                    {
                        "task_id": "TASK__doccritic",
                        "verdict": "PASS",
                        "summary": "REQ is specific and synced.",
                        "transcript": "Checked DOC_SYNC and changed REQ.",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertNotIn("isError", result)
            artifact = Path(task_dir) / "CRITIC__document.md"
            self.assertTrue(artifact.is_file())
            body = artifact.read_text(encoding="utf-8")
            self.assertIn("## Verdict\nPASS", body)
            self.assertIn("REQ is specific and synced.", body)
            state = (Path(task_dir) / "TASK_STATE.yaml").read_text(encoding="utf-8")
            self.assertIn("runtime_verdict: pending", state)

    def test_write_critic_document_rejects_blocked_env(self):
        result = harness_server.call_tool(
            "write_critic_document",
            {
                "task_id": "TASK__doccritic",
                "verdict": "BLOCKED_ENV",
                "summary": "x",
                "transcript": "x",
            },
        )
        self.assertTrue(result.get("isError"))
        self.assertIn("must be PASS or FAIL", result["structuredContent"]["error"])

    def test_write_req_doc_creates_durable_req_without_runtime_verdict_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__reqdoc")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            harness_server.find_repo_root = lambda *args, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "write_req_doc",
                    {
                        "task_id": "TASK__reqdoc",
                        "area": "ui",
                        "slug": "mobile-reader-navigation",
                        "intent": "Mobile reader navigation should match native expectations.",
                        "observable_behaviors": "Android back returns to the previous reader screen instead of exiting unexpectedly.",
                        "verification_cues": "Verify on Android APK or emulator and browser mobile separately.",
                        "non_goals": "Do not redesign the full reader.",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
            req_path = Path(tmp) / "doc/ui/REQ__mobile-reader-navigation.md"
            req_exists = req_path.is_file()
            body = req_path.read_text(encoding="utf-8") if req_exists else ""
            state = (Path(task_dir) / "TASK_STATE.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["req_path"], "doc/ui/REQ__mobile-reader-navigation.md")
        self.assertTrue(req_exists)
        self.assertIn("## Observable Behavior", body)
        self.assertIn("Android back returns", body)
        self.assertIn("## Verification Cues", body)
        self.assertIn("runtime_verdict: pending", state)

    def test_write_critic_qa_auto_promote_flag_is_evidence_only_noop(self):
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
                    "write_critic_qa",
                    {
                        "task_id": "TASK__qapromote",
                        "lens": "cli",
                        "verdict": "PASS",
                        "summary": "ok",
                        "transcript": "focused tests passed",
                        "auto_promote_open_acs": True,
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["promoted_acs"], [])
        self.assertIn("deprecated", result["structuredContent"]["ac_reconcile_next_action"])
        self.assertEqual(body.count("status: open"), 2)

    def test_task_verify_reconcile_promotes_open_acs_from_qa_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qareconcile")
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
                qa = harness_server.call_tool(
                    "write_critic_qa",
                    {
                        "task_id": "TASK__qareconcile",
                        "lens": "cli",
                        "verdict": "PASS",
                        "summary": "ok",
                        "transcript": "focused tests passed",
                    },
                )
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qareconcile", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", qa)
        self.assertNotIn("isError", verify)
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001", "AC-002"])
        self.assertEqual(body.count("status: passed"), 2)
        self.assertIn("evidence: CRITIC__qa.md task_verify PASS", body)

    def test_task_verify_reconcile_skips_failed_deferred_and_non_pass(self):
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
                result = harness_server.call_tool(
                    "write_critic_qa",
                    {
                        "task_id": "TASK__qaskip",
                        "lens": "cli",
                        "verdict": "FAIL",
                        "summary": "not ok",
                        "transcript": "failure",
                        "auto_promote_open_acs": True,
                    },
                )
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaskip", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["promoted_acs"], [])
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], [])
        self.assertIn("not PASS", verify["structuredContent"]["ac_reconcile"]["reason"])
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
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "write_critic_qa",
                    {
                        "task_id": "TASK__qaindent",
                        "lens": "cli",
                        "verdict": "PASS",
                        "summary": "ok",
                        "transcript": "focused tests passed",
                    },
                )
                verify = harness_server.call_tool(
                    "task_verify",
                    {"task_id": "TASK__qaindent", "reconcile_acs": True},
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertEqual(result["structuredContent"]["promoted_acs"], [])
        self.assertEqual(verify["structuredContent"]["ac_reconcile"]["promoted_acs"], ["AC-001"])
        self.assertIn("status: passed", body)

    def test_write_critic_ux_merges_lenses_without_runtime_verdict_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__uxmerge")
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                first = harness_server.call_tool(
                    "write_critic_ux",
                    {
                        "task_id": "TASK__uxmerge",
                        "lens": "browser",
                        "verdict": "PASS",
                        "summary": "browser UX shippable",
                        "transcript": "interactive-browser evidence",
                    },
                )
                second = harness_server.call_tool(
                    "write_critic_ux",
                    {
                        "task_id": "TASK__uxmerge",
                        "lens": "cli",
                        "verdict": "FAIL",
                        "summary": "CLI errors are not actionable",
                        "transcript": "invalid input gives no next step",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            critic = (Path(task_dir) / "CRITIC__ux.md").read_text(encoding="utf-8")
            state = (Path(task_dir) / "TASK_STATE.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", first)
        self.assertNotIn("isError", second)
        self.assertEqual(second["structuredContent"]["ux_verdict"], "FAIL")
        self.assertIn("## ux-browser verdict: PASS", critic)
        self.assertIn("## ux-cli verdict: FAIL", critic)
        self.assertIn("runtime_verdict: pending", state)

    def test_write_critic_ux_rejects_unknown_lens(self):
        result = harness_server.call_tool(
            "write_critic_ux",
            {
                "task_id": "TASK__uxbad",
                "lens": "mobile",
                "verdict": "PASS",
                "summary": "x",
                "transcript": "x",
            },
        )
        self.assertTrue(result.get("isError"))
        self.assertIn("invalid lens", result["structuredContent"]["error"])

    def test_record_ac_evidence_appends_log_without_promoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__acevidence")
            (Path(task_dir) / "CHECKS.yaml").write_text(
                '- id: AC-001\n  title: "one"\n  status: open\n  kind: functional\n',
                encoding="utf-8",
            )
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "record_ac_evidence",
                    {
                        "task_id": "TASK__acevidence",
                        "ac_id": "AC-001",
                        "source": "focused-test",
                        "evidence": "uv run pytest tests/test_x.py::test_y",
                    },
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertIn("status: open", body)
        self.assertIn("evidence_log:", body)
        self.assertIn("focused-test", body)

    def test_record_attempt_creates_attempt_dir_and_context_surfaces_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__attempt")
            original_ctd = harness_server.canonical_task_dir
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            try:
                result = harness_server.call_tool(
                    "record_attempt",
                    {
                        "task_id": "TASK__attempt",
                        "kind": "qa-cli",
                        "verdict": "FAIL",
                        "summary": "unit test failed",
                        "transcript": "traceback",
                    },
                )
                ctx = harness_server.call_tool("task_context", {"task_id": "TASK__attempt"})
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertNotIn("isError", result)
            attempt = result["structuredContent"]["attempt"]
            self.assertEqual(attempt["id"], "attempt-001")
            self.assertTrue((Path(task_dir) / "attempts" / "attempt-001" / "attempt.json").is_file())
            self.assertEqual(ctx["structuredContent"]["task_context"]["attempt_count"], 1)
            self.assertEqual(ctx["structuredContent"]["task_context"]["latest_attempt"]["summary"], "unit test failed")

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
        self.assertTrue(close.get("isError"))
        self.assertIn("HANDOFF.md", close["structuredContent"]["missing_for_close"])
        self.assertIn("runtime_verdict PASS", close["structuredContent"]["missing_for_close"])

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

    def test_write_plan_artifact_writes_plan_meta_and_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__planmcp")
            result = harness_server.call_tool(
                "write_plan_artifact",
                {
                    "task_dir": task_dir,
                    "artifact": "plan",
                    "content": "# MCP Plan\n",
                    "checks_content": "- id: AC-001\n  title: x\n  status: open\n",
                    "meta": {"routing": "light"},
                },
            )
            self.assertNotIn("isError", result)
            self.assertEqual(
                result["structuredContent"]["written"],
                ["PLAN.md", "PLAN.meta.json", "CHECKS.yaml"],
            )
            bytes_written = result["structuredContent"]["bytes_written"]
            self.assertGreater(bytes_written["PLAN.md"], 0)
            self.assertGreater(bytes_written["PLAN.meta.json"], 0)
            self.assertGreater(bytes_written["CHECKS.yaml"], 0)
            self.assertEqual((Path(task_dir) / "PLAN.md").read_text(encoding="utf-8"), "# MCP Plan\n")
            meta = json.loads((Path(task_dir) / "PLAN.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["author_role"], "plan-skill")
            self.assertEqual(meta["plan_meta"]["routing"], "light")
            self.assertIn("AC-001", (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8"))

    def test_write_plan_artifact_checks_requires_content_not_checks_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__checksparam")
            result = harness_server.call_tool(
                "write_plan_artifact",
                {
                    "task_dir": task_dir,
                    "artifact": "checks",
                    "checks_content": "- id: AC-001\n  title: x\n  status: open\n",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("use content", result["structuredContent"]["next_action"])
            self.assertFalse((Path(task_dir) / "CHECKS.yaml").exists())

    def test_write_plan_artifact_rejects_empty_checks_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptychecks")
            result = harness_server.call_tool(
                "write_plan_artifact",
                {"task_dir": task_dir, "artifact": "checks", "content": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty CHECKS.yaml", result["structuredContent"]["error"])
            self.assertFalse((Path(task_dir) / "CHECKS.yaml").exists())

    def test_write_plan_artifact_checks_reports_bytes_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__checksbytes")
            body = "- id: AC-001\n  title: x\n  status: open\n"
            result = harness_server.call_tool(
                "write_plan_artifact",
                {"task_dir": task_dir, "artifact": "checks", "content": body},
            )
            self.assertNotIn("isError", result)
            self.assertEqual(result["structuredContent"]["written"], ["CHECKS.yaml"])
            self.assertEqual(result["structuredContent"]["bytes_written"]["CHECKS.yaml"], len(body.encode("utf-8")))
            self.assertEqual((Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8"), body)

    def test_write_plan_artifact_rejects_empty_plan_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptywrites")
            plan_result = harness_server.call_tool(
                "write_plan_artifact",
                {"task_dir": task_dir, "artifact": "plan", "content": ""},
            )
            audit_result = harness_server.call_tool(
                "write_plan_artifact",
                {"task_dir": task_dir, "artifact": "audit", "content": "\n"},
            )
            self.assertTrue(plan_result.get("isError"))
            self.assertTrue(audit_result.get("isError"))
            self.assertIn("empty PLAN.md", plan_result["structuredContent"]["error"])
            self.assertIn("empty AUDIT_TRAIL.md", audit_result["structuredContent"]["error"])

    def test_write_plan_artifact_appends_audit_header_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__auditmcp")
            for row in ("| 1 | p | d | c | p | r | - |\n", "| 2 | p | d2 | c | p | r | - |\n"):
                result = harness_server.call_tool(
                    "write_plan_artifact",
                    {"task_dir": task_dir, "artifact": "audit", "content": row},
                )
                self.assertNotIn("isError", result)
            body = (Path(task_dir) / "AUDIT_TRAIL.md").read_text(encoding="utf-8")
            self.assertEqual(body.count("| # | phase | decision | classification | principle | rationale | rejected_option |"), 1)
            self.assertIn("| 1 |", body)
            self.assertIn("| 2 |", body)


class HarnessMcpServerPR2CloseGate(unittest.TestCase):
    """AC-001..AC-006: CHECKS gate + runtime-stale gate in task_close / task_verify."""

    def _prepare_task(self, base: str, task_id: str, *, checks_yaml: str | None,
                      write_critic: bool = True, write_handoff: bool = True,
                      touched_paths: list[str] | None = None,
                      handoff_body: str | None = None) -> str:
        (Path(base) / ".git").mkdir(exist_ok=True)
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
        task_dir = Path(base) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        tp = touched_paths or []
        tp_yaml = "[]" if not tp else "\n" + "\n".join(f"  - {p}" for p in tp)
        (task_dir / "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            f"status: created\n"
            f"runtime_verdict: PASS\n"
            f"touched_paths: {tp_yaml}\n"
            f"plan_session_state: closed\n"
            f"closed_at: null\n"
            f"updated: 2026-04-19T15:00:00Z\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        if write_handoff:
            default_handoff = "# handoff\n\n## Commit-backed Learnings\n\nStatus: none\n"
            body = handoff_body or default_handoff
            if "Self-Healing Candidates" not in body:
                body = body.rstrip() + "\n\n## Self-Healing Candidates\n\nStatus: none\n"
            (task_dir / "HANDOFF.md").write_text(
                body,
                encoding="utf-8",
            )
        if write_critic:
            (task_dir / "CRITIC__qa.md").write_text("# critic\nverdict: PASS\n", encoding="utf-8")
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
            lambda repo_root: set(paths)
        )

    def _unpatch_repo_root_for_context(self):
        harness_server.emit_compact_context.__globals__["find_repo_root"] = (
            self._orig_context_find_repo_root
        )
        harness_server.emit_compact_context.__globals__["_git_changed_paths"] = (
            self._orig_context_git_changed_paths
        )

    def test_close_blocks_unresolved_user_feedback_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__feedback-open",
                checks_yaml='- id: AC-001\n  title: "done"\n  status: passed\n  kind: functional\n',
            )
            (Path(td) / "USER_FEEDBACK.jsonl").write_text(
                json.dumps({
                    "id": "ufe-open",
                    "task_id": "TASK__feedback-open",
                    "prompt_excerpt": "이 방향으로 바꿔줘",
                    "source": "user_prompt_hook",
                }) + "\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__feedback-open"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        payload = result["structuredContent"]
        self.assertIn("User feedback disposition in HANDOFF.md", payload["missing_for_close"])
        self.assertEqual(payload["task_context"]["unresolved_feedback_count"], 1)
        self.assertEqual(payload["task_context"]["unresolved_feedback_ids"], ["ufe-open"])
        self.assertIn("USER_FEEDBACK.jsonl", payload["task_context"]["next_action"])

    def test_close_accepts_terminal_user_feedback_disposition(self):
        handoff = (
            "# handoff\n\n"
            "## User Feedback Disposition\n\n"
            "- event: ufe-done status: handled-local reason: reflected before QA.\n\n"
            "## Commit-backed Learnings\n\nStatus: none\n\n"
            "## Self-Healing Candidates\n\nStatus: none\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__feedback-done",
                checks_yaml='- id: AC-001\n  title: "done"\n  status: passed\n  kind: functional\n',
                handoff_body=handoff,
            )
            (Path(td) / "USER_FEEDBACK.jsonl").write_text(
                json.dumps({
                    "id": "ufe-done",
                    "task_id": "TASK__feedback-done",
                    "prompt_excerpt": "이 방향으로 바꿔줘",
                    "source": "user_prompt_hook",
                }) + "\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__feedback-done"})
            finally:
                self._unpatch()
        self.assertFalse(result.get("isError"), result)
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

    def test_close_requires_ux_cli_when_manifest_and_cli_surface_match(self):
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
        self.assertTrue(result.get("isError"))
        self.assertIn("ux-cli PASS in CRITIC__ux.md", result["structuredContent"]["missing_for_close"])

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
            (Path(td) / "CRITIC__ux.md").write_text(
                "# CRITIC — ux\n\n## ux-cli verdict: PASS\n\n### summary\nok\n",
                encoding="utf-8",
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

    def test_close_rejects_stale_required_ux_cli_pass(self):
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
            (Path(td) / "CRITIC__ux.md").write_text(
                "# CRITIC — ux\n\n## ux-cli verdict: PASS\n\n### summary\nok\n",
                encoding="utf-8",
            )
            future = os.path.getmtime(Path(td) / "CRITIC__ux.md") + 10
            os.utime(source, (future, future))
            self._patch(td)
            self._patch_repo_root_for_context(tmp)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__ux-cli-stale"})
            finally:
                self._unpatch_repo_root_for_context()
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn("ux-cli PASS in CRITIC__ux.md", result["structuredContent"]["missing_for_close"])

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

    def test_close_rejects_handoff_without_commit_backed_learnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-missing",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body="# handoff\n\nNo shared learning section.\n",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-missing"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_missing_commit_learning_still_reports_stale_and_blocking_acs(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-missing-with-diagnostics",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: open\n  kind: functional\n',
                handoff_body="# handoff\n\nNo shared learning section.\n",
                touched_paths=["plugin/scripts/health.py"],
            )
            critic = _os.path.join(td, "CRITIC__qa.md")
            _os.utime(critic, (100, 100))
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-missing-with-diagnostics"},
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        data = result["structuredContent"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", data["missing_for_close"])
        self.assertTrue(data["stale"])
        self.assertEqual(data["stale_path"], "plugin/scripts/health.py")
        self.assertEqual(data["blocking_acs"][0]["id"], "AC-001")

    def test_close_rejects_commit_backed_status_outside_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-status-outside",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "## Previous Section\n\n"
                    "Status: captured\n\n"
                    "## Commit-backed Learnings\n\n"
                    "No section-local status.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-status-outside"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-template-only",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "```md\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/skills/run/self-improvement.md — example.\n"
                    "```\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-template-only"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_tilde_fence_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-tilde-template-only",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "~~~md\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/skills/run/self-improvement.md — example.\n"
                    "~~~\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-tilde-template-only"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_indented_fence_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-indented-fence-template-only",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "   ```md\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/skills/run/self-improvement.md — example.\n"
                    "   ```\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-indented-fence-template-only"},
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_html_comment_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-html-comment-template-only",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "<!--\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/skills/run/self-improvement.md — hidden.\n"
                    "-->\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-html-comment-template-only"},
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_indented_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-indented-template-only",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "    ## Commit-backed Learnings\n\n"
                    "    Status: captured\n\n"
                    "    - captured: plugin/skills/run/self-improvement.md — example.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-indented-template-only"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_captured_without_shared_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-no-path",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: doc/harness/learnings.jsonl — local staging only.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-captured-no-path"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_captured_nonexistent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-nonexistent",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/scripts/does_not_exist.py — missing file.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-captured-nonexistent"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_rejects_commit_backed_captured_ignored_touched_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            ignored = Path(tmp) / "doc" / "common" / "GUIDE__ignored.md"
            ignored.parent.mkdir(parents=True, exist_ok=True)
            ignored.write_text("# ignored\n", encoding="utf-8")
            (Path(tmp) / ".gitignore").write_text("doc/common/GUIDE__ignored.md\n", encoding="utf-8")
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-ignored",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["doc/common/GUIDE__ignored.md"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: doc/common/GUIDE__ignored.md — ignored local file.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-captured-ignored"},
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_accepts_commit_backed_captured_path_when_line_mentions_learnings_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-with-learnings-mention",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/_lib.py"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/scripts/_lib.py — replaces reliance on doc/harness/learnings.jsonl.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-captured-with-learnings-mention"},
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_accepts_commit_backed_captured_general_repo_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-general-artifact",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/CLAUDE.md"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/CLAUDE.md — shared runtime contract.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-captured-general-artifact"},
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_rejects_commit_backed_captured_untouched_existing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-untouched-existing",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=[],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/scripts/_lib.py — existing but unrelated.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-captured-untouched-existing"},
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Commit-backed Learnings section in HANDOFF.md", missing)

    def test_close_accepts_commit_backed_captured_top_level_touched_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured-top-level",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["README.md"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: README.md — shared setup note.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close",
                    {"task_id": "TASK__commit-learning-captured-top-level"},
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_accepts_commit_backed_learnings_captured_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__commit-learning-captured",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/skills/run/self-improvement.md"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: captured\n\n"
                    "- captured: plugin/skills/run/self-improvement.md — shared rule.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__commit-learning-captured"}
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_rejects_handoff_without_self_healing_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__self-healing-missing",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: none\n"
                ),
            )
            handoff = Path(td) / "HANDOFF.md"
            handoff.write_text(
                "# handoff\n\n## Commit-backed Learnings\n\nStatus: none\n",
                encoding="utf-8",
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__self-healing-missing"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Self-Healing Candidates section in HANDOFF.md", missing)

    def test_close_accepts_self_healing_applied_changed_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__self-healing-applied",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/_lib.py"],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: none\n\n"
                    "## Self-Healing Candidates\n\n"
                    "Status: applied\n\n"
                    "- applied: close gate parser drift — plugin/scripts/_lib.py now blocks recurrence.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__self-healing-applied"}
                )
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    def test_close_rejects_self_healing_applied_untouched_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__self-healing-applied-untouched",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=[],
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: none\n\n"
                    "## Self-Healing Candidates\n\n"
                    "Status: applied\n\n"
                    "- applied: claimed fix — plugin/scripts/_lib.py.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__self-healing-applied-untouched"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Self-Healing Candidates section in HANDOFF.md", missing)

    def test_close_rejects_self_healing_deferred_without_user_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__self-healing-deferred-without-user",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                handoff_body=(
                    "# handoff\n\n"
                    "## Commit-backed Learnings\n\n"
                    "Status: none\n\n"
                    "## Self-Healing Candidates\n\n"
                    "Status: deferred\n\n"
                    "- deferred: browser MCP flake — needs separate runtime fixture task.\n"
                ),
            )
            self._patch(td)
            try:
                result = harness_server.call_tool(
                    "task_close", {"task_id": "TASK__self-healing-deferred-without-user"}
                )
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        missing = result["structuredContent"]["missing_for_close"]
        self.assertIn("Self-Healing Candidates section in HANDOFF.md", missing)

    def test_close_accepts_self_healing_deferred_with_user_decision_and_rejected_with_reason(self):
        for status, bullet in (
            ("deferred", (
                "- deferred: browser MCP flake\n"
                "  user_decision: separate task\n"
                "  reason: requires runtime fixture and manifest changes\n"
                "  proposed_artifact: plugin/agents/qa-browser.md\n"
            )),
            ("rejected", "- rejected: one-off typo — not reusable.\n"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                task_id = f"TASK__self-healing-{status}"
                td = self._prepare_task(
                    tmp, task_id,
                    checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                    handoff_body=(
                        "# handoff\n\n"
                        "## Commit-backed Learnings\n\n"
                        "Status: none\n\n"
                        "## Self-Healing Candidates\n\n"
                        f"Status: {status}\n\n"
                        f"{bullet}"
                    ),
                )
                self._patch(td)
                try:
                    result = harness_server.call_tool("task_close", {"task_id": task_id})
                finally:
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

    # ---- AC-004: stale CRITIC__qa refuses close ----
    def test_close_rejects_stale_verdict(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-004",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            # Make CRITIC older than touched path
            critic = _os.path.join(td, "CRITIC__qa.md")
            _os.utime(critic, (100, 100))
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-004"})
            finally:
                self._unpatch()
        self.assertTrue(result.get("isError"))
        self.assertIn("stale", result["structuredContent"]["error"])
        self.assertEqual(result["structuredContent"]["stale_path"], "plugin/scripts/health.py")

    # ---- AC-006: task_verify reports stale + reverts verdict ----
    def test_verify_reports_stale_and_reverts_verdict(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            _os.utime(_os.path.join(td, "CRITIC__qa.md"), (100, 100))
            self._patch(td)
            try:
                result = harness_server.call_tool("task_verify", {"task_id": "TASK__pr2-006"})
            finally:
                self._unpatch()
            # Read state while tempdir still exists
            state = (Path(td) / "TASK_STATE.yaml").read_text(encoding="utf-8")
        s = result["structuredContent"]
        self.assertTrue(s["stale"])
        self.assertEqual(s["stale_path"], "plugin/scripts/health.py")
        self.assertIn("runtime_verdict: pending", state)

    def test_stale_skip_list_ignores_pyc(self):
        """Stale check must not trip on Python cache files."""
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-006b",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/__pycache__/health.cpython-311.pyc"],
            )
            _os.utime(_os.path.join(td, "CRITIC__qa.md"), (100, 100))
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006b"})
            finally:
                self._unpatch()
        # pyc skip path — should close cleanly (not stale)
        self.assertNotIn("isError", result,
                         f"__pycache__ pyc path should be skipped, not treated as stale: {result}")

    def test_stale_check_ignores_task_artifacts_after_qa(self):
        import os as _os
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
            _os.utime(Path(td) / "CRITIC__qa.md", (100, 100))
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
                "submodule", "add", "-q", str(sub_src), "services/api",
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
            (parent / "services" / "api" / "api.py").write_text("v2\n", encoding="utf-8")

            touched = harness_server.sync_from_git_diff(str(task_dir))
            self.assertIn("services/api/api.py", touched)


if __name__ == "__main__":
    unittest.main()
