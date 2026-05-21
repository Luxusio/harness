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
    "write_critic_qa",
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
        with mock.patch.object(harness_server.sys, "stdin", stdin), mock.patch.object(harness_server.sys, "stdout", stdout):
            server.handle_request(server._read())
            stdout.flush()

        raw = stdout_bytes.getvalue()
        self.assertTrue(raw.startswith(b"Content-Length: "), raw)
        response_body = raw.split(b"\r\n\r\n", 1)[1]
        response = json.loads(response_body.decode())
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "harness")

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

    def test_write_critic_qa_can_auto_promote_open_acs_on_pass(self):
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
        self.assertEqual(result["structuredContent"]["promoted_acs"], ["AC-001", "AC-002"])
        self.assertEqual(body.count("status: passed"), 2)
        self.assertIn("evidence: CRITIC__qa.md qa-cli PASS", body)

    def test_write_critic_qa_auto_promote_skips_failed_deferred_and_non_pass(self):
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
            finally:
                harness_server.canonical_task_dir = original_ctd
            body = (Path(task_dir) / "CHECKS.yaml").read_text(encoding="utf-8")
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"]["promoted_acs"], [])
        self.assertIn("status: open", body)
        self.assertIn("status: failed", body)
        self.assertIn("status: deferred", body)

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
                      touched_paths: list[str] | None = None) -> str:
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
            (task_dir / "HANDOFF.md").write_text("# handoff\n", encoding="utf-8")
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
            handoff.write_text("# handoff after qa\n", encoding="utf-8")
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
