"""Tests for the plugin-local harness MCP server."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from types import FunctionType
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"


# Reuse an existing instance. `_lib`'s control-writer authority binds the
# `harness_server` code objects it first observes, so a second module object for
# the same file is not recognised as a legitimate caller and every TASK.json
# mutation raises `PermissionError: TASK.json mutation requires the task-control
# MCP`. `tests/test_session_hint_marker_binding.py` already guarded this and its
# comment claims to match this file; it did not. Under xdist `--dist worksteal`
# the two files land in one worker only sometimes, which made the resulting
# failure look like a 25%-per-run flake in `HarnessMcpServerPR2CloseGate`.
if "harness_server" in sys.modules:
    harness_server = sys.modules["harness_server"]
else:
    spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
    assert spec and spec.loader
    harness_server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness_server
    spec.loader.exec_module(harness_server)
import _lib as harness_lib  # type: ignore


def call_execution(server, name, args):
    """Unit-test private execution owners without restoring public aliases."""
    handler = server._EXECUTION.get(name)
    return server._invoke(name, handler, args) if handler else server.call_tool(name, args)


EXPECTED_TOOLS = {"wbs"}


def _write_marker_fixture(repo_root: str, task_dir: str, session_id: str = "") -> None:
    sid = session_id or harness_lib.current_session_id()
    sessions = Path(repo_root) / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    control = harness_server.read_task_control(task_dir)
    payload = {
        "session_id": sid, "task_dir": task_dir,
        "task_id": Path(task_dir).name, "run_id": control["run_id"],
        "updated": harness_lib.now_iso(),
    }
    (sessions / f"{sid}.json").write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8",
    )
    (Path(repo_root) / "doc/harness/tasks/.active").write_text(
        task_dir + "\n", encoding="utf-8",
    )


class HarnessMcpServerTests(unittest.TestCase):
    def _run_git(self, cwd: str, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )

    def _make_task(self, base_dir: str, task_id: str) -> str:
        task_dir = Path(base_dir) / "doc" / "harness" / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        task_dir.joinpath("TASK.json").write_text(
            json.dumps({
                "run_id": harness_lib.new_uuid7(),
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
        return str(task_dir)

    def _call_in_repo(self, repo_root: str, name: str, args: dict) -> dict:
        with mock.patch.object(harness_server, "find_repo_root", return_value=repo_root):
            return call_execution(harness_server, name, args)

    def _write_control_fixture(self, task_dir: str, control: dict) -> None:
        Path(task_dir, "TASK.json").write_text(
            json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    def _close_fixture(self, task_dir: str) -> None:
        control = harness_server.read_task_control(task_dir)
        control["closed"] = True
        self._write_control_fixture(task_dir, control)

    def _write_marker_fixture(self, repo_root: str, task_dir: str, session_id: str = "") -> None:
        _write_marker_fixture(repo_root, task_dir, session_id)

    def _write_historical_receipts(self, task_dir: str) -> None:
        Path(task_dir, "RECEIPTS.jsonl").write_bytes(b"opaque historical receipt bytes\n")

    def test_server_info_is_harness(self):
        self.assertEqual(harness_server.SERVER_INFO["name"], "harness")
        self.assertEqual(harness_server.SERVER_INFO["title"], "harness Control Plane")

    def test_control_writer_rejects_foreign_globals_clone(self):
        foreign_globals = dict(harness_server.handle_task_start.__globals__)
        foreign_globals["__name__"] = "harness_server"
        clone = FunctionType(
            harness_server.handle_task_start.__code__,
            foreign_globals,
            harness_server.handle_task_start.__name__,
            harness_server.handle_task_start.__defaults__,
            harness_server.handle_task_start.__closure__,
        )
        clone.__kwdefaults__ = harness_server.handle_task_start.__kwdefaults__
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            manifest = root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
            prior_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with self.assertRaisesRegex(PermissionError, "task-control"):
                    clone({"task_id": "TASK__foreign-control-clone"})
            finally:
                os.chdir(prior_cwd)
            self.assertFalse(
                (root / "doc/harness/tasks/TASK__foreign-control-clone/TASK.json").exists()
            )

    def test_control_writer_rejects_replaced_helper_confused_deputy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            manifest = root / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
            foreign = root / "doc/harness/tasks/TASK__foreign-helper"
            foreign.mkdir(parents=True)
            control = {
                "run_id": harness_lib.new_uuid7(),
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            }

            def confused_helper(*_args, **_kwargs):
                harness_server.write_task_control(str(foreign), control)
                raise AssertionError("replaced helper reached task-control authority")

            prior_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch.object(
                    harness_server, "ensure_task_scaffold", confused_helper,
                ):
                    with self.assertRaisesRegex(PermissionError, "task-control"):
                        harness_server.handle_task_start(
                            {"task_id": "TASK__confused-deputy-trigger"}
                        )
            finally:
                os.chdir(prior_cwd)
            self.assertFalse((foreign / "TASK.json").exists())

    def test_internal_control_helpers_are_not_authority_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "TASK__internal-helper"
            task.mkdir()
            control = {
                "run_id": harness_lib.new_uuid7(),
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            }
            (task / "TASK.json").write_text(
                json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )
            result = harness_server._publish_write_plan(
                {"plan": "# forged\n"}, str(task), control,
                (control, control, "# forged\n"),
            )
            self.assertTrue(result.get("isError"))
            self.assertFalse((task / "PLAN.md").exists())

    def test_live_control_writer_rebinding_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "canonical module import"):
            harness_lib._bind_control_writer(harness_server.handle_task_start)


    def test_start_and_context_never_enter_git_snapshot_helpers(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("lifecycle Git snapshot helper was called")

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            manifest = Path(tmp) / "doc/harness/manifest.yaml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__no-git-handlers"
            prior_cwd = os.getcwd()
            with mock.patch.object(harness_lib.subprocess, "run", side_effect=forbidden):
                os.chdir(tmp)
                started = harness_server.handle_task_start(
                    {"task_id": "TASK__no-git-handlers"}
                )
                self.assertNotIn("isError", started)
                (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
                self._write_historical_receipts(str(task_dir))
                context = harness_server.handle_task_context(
                    {"task_id": "TASK__no-git-handlers"}
                )
                os.chdir(prior_cwd)
            self.assertNotIn("isError", context)
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())

    def test_tool_registry_matches_expected_tool_surface(self):
        tools = {tool["name"] for tool in harness_server.list_tools()}
        self.assertEqual(tools, EXPECTED_TOOLS)

    def test_each_tool_has_description_and_schema(self):
        for tool in harness_server.list_tools():
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            self.assertTrue(tool["description"], f"{tool['name']} missing description")

        tool = harness_server.TOOLS["wbs"]
        properties = tool["inputSchema"]["properties"]
        self.assertEqual(properties["fresh_run"]["type"], "boolean")
        self.assertEqual(properties["apply"]["default"], False)
        self.assertEqual(set(harness_server.TOOLS), {"wbs"})


    def test_unknown_tool_returns_error_payload(self):
        result = call_execution(harness_server, "does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_git_binding_error_returns_structured_recovery_details(self):
        original = harness_server.TOOLS["wbs"]["handler"]

        def fail(_args):
            raise harness_lib.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_MISMATCH",
                "registered source failed validation",
                path="services/front",
                invariant="admin_gitdir_backreference",
                next_action="Repair the worktree and retry.",
            )

        harness_server.TOOLS["wbs"]["handler"] = fail
        try:
            result = harness_server.call_tool("wbs", {"action": "context", "wbs_id": "x"})
        finally:
            harness_server.TOOLS["wbs"]["handler"] = original

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error_code"],
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
        )
        self.assertEqual(result["structuredContent"]["path"], "services/front")
        self.assertEqual(
            result["structuredContent"]["invariant"], "admin_gitdir_backreference"
        )


    def test_task_start_rejects_traversal_and_outside_paths_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            index_lock = git_dir / "index.lock"
            index_lock.write_bytes(b"")
            escaped = Path(tmp).parent / "escaped-task"
            outside = Path(tmp) / "outside"
            for args in (
                {"task_id": "TASK__/../../../escaped-task"},
                {"task_dir": str(outside)},
                {"task_dir": "doc/harness/tasks/../tasks/TASK__safe"},
                {"task_id": "TASK__safe\n"},
            ):
                result = self._call_in_repo(tmp, "task_start", args)
                self.assertTrue(result.get("isError"), args)
                self.assertIn("canonical", result["structuredContent"]["error"])
                self.assertIn("next_action", result["structuredContent"])
            self.assertFalse(escaped.exists())
            self.assertFalse(outside.exists())
            self.assertTrue(index_lock.exists(), "invalid selectors must not clean repository locks")

    def test_task_start_never_removes_repository_index_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            lock = Path(tmp) / ".git/index.lock"
            lock.write_bytes(b"")

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__preserve-index-lock"},
            )

            self.assertNotIn("isError", result)
            self.assertTrue(lock.exists())

    def test_task_start_rejects_invalid_existing_control_before_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__expected"))
            state = task_dir / "TASK.json"
            state.write_text("{}\n", encoding="utf-8")
            git_dir = Path(tmp) / ".git"
            git_dir.mkdir()
            lock = git_dir / "index.lock"
            lock.write_bytes(b"")
            before = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}

            result = self._call_in_repo(tmp, "task_start", {"task_id": "TASK__expected"})

            self.assertTrue(result.get("isError"))
            self.assertIn("invalid TASK.json", result["structuredContent"]["error"])
            after = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertTrue(lock.exists())
            self.assertFalse((task_dir.parent / ".active").exists())
            self.assertFalse((task_dir.parent / ".active_sessions").exists())

    def test_task_start_rejects_old_six_field_control_with_new_task_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__old-control"
            task_dir.mkdir(parents=True)
            state = task_dir / "TASK.json"
            state.write_text(json.dumps({
                "task_run_id": "a" * 32,
                "started_at": "2026-08-12T00:00:00Z",
                "execution_mode": "standard",
                "review_lenses": ["review-code"],
                "qa_lenses": ["qa-cli"],
                "closed": False,
            }) + "\n", encoding="utf-8")
            before = state.read_bytes()

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__old-control"},
            )

            payload = result["structuredContent"]
            self.assertTrue(result.get("isError"))
            self.assertIn("unsupported task-control schema", payload["error"])
            self.assertIn("new wbs_id", payload["next_action"])
            self.assertNotIn("Correct the named selector", payload["next_action"])
            self.assertEqual(state.read_bytes(), before)

    def test_fresh_task_start_rejects_directory_replacement_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__fresh-race"
            real_atomic_write = harness_lib._atomic_text_write
            displaced = task_dir.with_name("TASK__fresh-race-displaced")
            raced = False

            def replace_task_dir(path, text):
                nonlocal raced
                if not raced and Path(path).name == "TASK.json":
                    raced = True
                    task_dir.rename(displaced)
                    task_dir.symlink_to(displaced, target_is_directory=True)
                return real_atomic_write(path, text)

            with (
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_lib, "_atomic_text_write", side_effect=replace_task_dir,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "integrity"):
                    harness_server.handle_task_start({"task_id": "TASK__fresh-race"})

            self.assertFalse((displaced / "TASK.json").exists())

    def test_task_selectors_accept_canonical_paths_and_reject_mismatch_or_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(self._make_task(tmp, "TASK__safe"))
            relative = task.relative_to(tmp).as_posix()
            self.assertEqual(
                harness_server.canonical_task_dir(task_dir=relative, repo_root=tmp),
                str(task),
            )
            self.assertEqual(
                harness_server.canonical_task_dir(
                    task_id="safe", task_dir=str(task), repo_root=tmp
                ),
                str(task),
            )
            with self.assertRaisesRegex(ValueError, "disagree"):
                harness_server.canonical_task_dir(
                    task_id="TASK__other", task_dir=str(task), repo_root=tmp
                )
            outside = Path(tmp) / "outside-target"
            outside.mkdir()
            alias = task.parent / "TASK__alias"
            alias.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real directory"):
                harness_server.canonical_task_dir(task_dir=str(alias), repo_root=tmp)
            for tool_args in (
                ("task_start", {"task_id": "TASK__alias"}),
                ("write_plan", {"task_id": "TASK__alias", "plan": "# escaped\n"}),
                ("task_blocked", {
                    "task_id": "TASK__alias",
                    "blocked_reason": "x",
                    "unblock_condition": "y",
                }),
            ):
                result = self._call_in_repo(tmp, tool_args[0], tool_args[1])
                self.assertTrue(result.get("isError"), tool_args[0])
                self.assertEqual(result["structuredContent"]["field"], "task_id")
                self.assertEqual(result["structuredContent"]["rejected_value"], repr("TASK__alias"))
                self.assertIn("TASK__", result["structuredContent"]["expected"])
                self.assertIn("next_action", result["structuredContent"])
            self.assertEqual(list(outside.iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp:
            harness_dir = Path(tmp) / "doc" / "harness"
            harness_dir.mkdir(parents=True)
            outside = Path(tmp) / "outside-task-root"
            outside.mkdir()
            (harness_dir / "tasks").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "task root"):
                harness_server.canonical_task_dir(task_id="TASK__safe", repo_root=tmp)

        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside-doc"
            outside.mkdir()
            (Path(tmp) / "doc").symlink_to(outside, target_is_directory=True)
            result = self._call_in_repo(tmp, "task_start", {"task_id": "TASK__escape"})
            self.assertTrue(result.get("isError"))
            self.assertFalse((outside / "harness" / "tasks" / "TASK__escape").exists())

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
        self.assertIn("WBS-first control plane", instructions)
        self.assertIn("wbs", instructions)
        self.assertIn("plain repo-mutating request", instructions)
        self.assertIn("hooks do not create tasks automatically", instructions)
        self.assertIn("Task open preserves", instructions)
        self.assertIn("fresh_run=true", instructions)
        self.assertIn("bare tool names", instructions)
        self.assertIn("Codex callers should use these bare tool names directly", instructions)
        self.assertIn("get_goal", instructions)
        self.assertIn("plan", instructions)
        self.assertNotIn("write_plan_artifact", instructions)
        self.assertNotIn("write_handoff", instructions)
        self.assertNotIn("write_doc_sync", instructions)
        self.assertNotIn("write_req_doc", instructions)
        self.assertNotIn("record_attempt", instructions)
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
        self.assertIn("WBS-first control plane", instructions)
        self.assertIn("wbs", instructions)
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
                # Through `_call_in_repo`: `task_context` binds the calling
                # session's active marker, so a handler left pointed at the real
                # repository writes this fixture's tmp task into it.
                result = self._call_in_repo(tmp, "task_context", {"task_id": "TASK__mcp"})
            finally:
                harness_server.canonical_task_dir = original_ctd
            self.assertNotIn("isError", result)
            structured = result["structuredContent"]
            self.assertEqual(structured["task_id"], "TASK__mcp")
            context = structured["task_context"]
            self.assertEqual(context["task_id"], "TASK__mcp")
            self.assertEqual(context["verification_basis"], "current_wbs_checks")
            for removed in ("watcher_status", "review_verdict", "receipt_recording", "report_path"):
                self.assertNotIn(removed, context)
                self.assertNotIn(removed, structured)


    def test_critic_tools_are_not_exposed(self):
        for tool in ("write_critic_document", "write_critic_qa", "write_critic_ux"):
            result = call_execution(harness_server,
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

    def test_record_ac_evidence_is_not_exposed(self):
        result = call_execution(harness_server,
            "record_ac_evidence",
            {
                "task_id": "TASK__removed",
                "ac_id": "AC-001",
                "evidence": "self-authored claim",
            },
        )
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])


    def test_micro_execution_mode_requires_plan_and_measured_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__micro"
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: str(task_dir)
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                start = call_execution(harness_server,
                    "task_start",
                    {"task_id": "TASK__micro", "execution_mode": "micro"},
                )
                close = call_execution(harness_server, "task_close", {"task_id": "TASK__micro"})
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root
        ctx = start["structuredContent"]["task_context"]
        self.assertFalse(ctx["source_write_allowed"])
        self.assertEqual(ctx["routing"]["orchestration_mode"], "unassigned")
        self.assertIn("PLAN.md", ctx["why_source_write_blocked"])
        self.assertEqual(ctx["routing"]["execution_mode"], "micro")
        self.assertIn("PLAN.md", ctx["missing_for_close"])
        self.assertIn("PLAN.md", ctx["next_action"])
        self.assertTrue(close.get("isError"))
        self.assertTrue(close["structuredContent"]["missing_for_close"])

    def test_task_blocked_records_pause_state_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__blocked")
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = call_execution(harness_server,
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
            self.assertIn("wbs(action='open')", body)
            self.assertIn("fresh_run", body)
            self.assertIn("preserv", body.lower())
            payload = result["structuredContent"]
            self.assertIn("wbs(action='open')", payload["next_action"])
            self.assertIn("fresh_run", payload["next_action"])
            self.assertIn("preserv", payload["next_action"].lower())
            self.assertEqual(
                harness_server.task_control_status(
                    task_dir, harness_server.read_task_control(task_dir)
                ), "blocked",
            )

    def test_task_blocked_argument_errors_name_the_actual_field_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__blocked-arguments"))
            state_before = (task_dir / "TASK.json").read_bytes()
            cases = (
                ({"unblock_condition": "resume"}, "blocked_reason", "missing", "<missing>"),
                (
                    {"blocked_reason": " \n\t ", "unblock_condition": "resume"},
                    "blocked_reason", "blank", "<blank string>",
                ),
                (
                    {"blocked_reason": ["SECRET_REASON_TEXT"], "unblock_condition": "resume"},
                    "blocked_reason", "wrong_type", "<non-string: list>",
                ),
                ({"blocked_reason": "reason"}, "unblock_condition", "missing", "<missing>"),
                (
                    {"blocked_reason": "reason", "unblock_condition": " \n\t "},
                    "unblock_condition", "blank", "<blank string>",
                ),
                (
                    {"blocked_reason": "reason", "unblock_condition": ["SECRET_CONDITION_TEXT"]},
                    "unblock_condition", "wrong_type", "<non-string: list>",
                ),
            )
            for extra, field, reason, rejected in cases:
                with self.subTest(field=field, reason=reason):
                    result = self._call_in_repo(
                        tmp,
                        "task_blocked",
                        {"task_id": task_dir.name, **extra},
                    )
                    payload = result["structuredContent"]
                    self.assertTrue(result.get("isError"))
                    self.assertEqual(payload["error_code"], "INVALID_ARGUMENT")
                    self.assertEqual(payload["field"], field)
                    self.assertEqual(payload["reason"], reason)
                    self.assertEqual(payload["rejected_value"], rejected)
                    self.assertIn(field, payload["next_action"])
                    self.assertNotIn("SECRET_REASON_TEXT", json.dumps(result))
                    self.assertNotIn("SECRET_CONDITION_TEXT", json.dumps(result))
                    self.assertFalse((task_dir / "BLOCKED.md").exists())
                    self.assertEqual((task_dir / "TASK.json").read_bytes(), state_before)

    def test_task_blocked_preserves_long_unicode_fields_through_public_transports(self):
        reason = "  REASON_START\r\n" + ("막힌 이유🙂 {\"reason\": true}\r둘째 줄 'quote'\\slash\n" * 320) + "REASON_END\t "
        unblock = "\rUNBLOCK_START\n" + ("해제 조건🚦 [unblock]\r\n다른 줄 \"double\"\\path\r" * 340) + "UNBLOCK_END  "
        self.assertGreater(len(reason.encode("utf-8")), 12 * 1024)
        self.assertGreater(len(unblock.encode("utf-8")), 12 * 1024)

        for mode in ("direct", "newline", "framed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(self._make_task(tmp, f"TASK__long-{mode}"))
                args = {
                    "task_id": task_dir.name,
                    "blocked_reason": reason,
                    "unblock_condition": unblock,
                }
                if mode == "direct":
                    result = self._call_in_repo(tmp, "task_blocked", args)
                else:
                    request = {
                        "jsonrpc": "2.0",
                        "id": 41,
                        "method": "tools/call",
                        "params": {"name": "wbs", "arguments": {"action": "block", "wbs_id": args["task_id"], **{k: v for k, v in args.items() if k != "task_id"}}},
                    }
                    following = {
                        "jsonrpc": "2.0", "id": 42, "method": "ping", "params": {},
                    }

                    def encoded(payload):
                        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

                    if mode == "framed":
                        def frame(payload):
                            body = encoded(payload)
                            return (
                                b"Content-Length: " + str(len(body)).encode("ascii")
                                + b"\r\n\r\n" + body
                            )
                        raw_input = frame(request) + frame(following)
                    else:
                        raw_input = encoded(request) + b"\n" + encoded(following) + b"\n"

                    stdin = io.TextIOWrapper(io.BytesIO(raw_input), encoding="utf-8")
                    stdout_bytes = io.BytesIO()
                    stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
                    server = harness_server.McpServer()
                    with (
                        mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                        mock.patch.object(harness_server.sys, "stdin", stdin),
                        mock.patch.object(harness_server.sys, "stdout", stdout),
                    ):
                        server.handle_request(server._read())
                        server.handle_request(server._read())
                        stdout.flush()

                    raw_output = stdout_bytes.getvalue()
                    if mode == "framed":
                        parts = raw_output.split(b"Content-Length: ")[1:]
                        responses = [
                            json.loads(part.split(b"\r\n\r\n", 1)[1].decode("utf-8"))
                            for part in parts
                        ]
                    else:
                        responses = [json.loads(line) for line in raw_output.splitlines()]
                    self.assertEqual([item["id"] for item in responses], [41, 42])
                    result = responses[0]["result"]

                self.assertNotIn("isError", result)
                body = (task_dir / "BLOCKED.md").read_bytes()
                expected = (
                    f"## Blocked Reason\n{reason}\n\n"
                    f"## Unblock Condition\n{unblock}\n\n## Resume\n"
                ).encode("utf-8")
                self.assertIn(expected, body)
                self.assertEqual(body.count(reason.encode("utf-8")), 1)
                self.assertEqual(body.count(unblock.encode("utf-8")), 1)

    def test_task_blocked_enforces_utf8_byte_limit_before_mutation(self):
        exact = "가" * (122_880 // len("가".encode("utf-8")))
        self.assertEqual(len(exact.encode("utf-8")), 122_880)
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__limit-both-exact"))
            result = self._call_in_repo(tmp, "task_blocked", {
                "task_id": task_dir.name,
                "blocked_reason": exact,
                "unblock_condition": exact,
            })
            self.assertNotIn("isError", result)
            self.assertLess((task_dir / "BLOCKED.md").stat().st_size, 256 * 1024)

        for field in ("blocked_reason", "unblock_condition"):
            with self.subTest(field=field, boundary="exact"), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(self._make_task(tmp, f"TASK__limit-exact-{field}"))
                args = {
                    "task_id": task_dir.name,
                    "blocked_reason": "reason",
                    "unblock_condition": "resume",
                }
                args[field] = exact
                result = self._call_in_repo(tmp, "task_blocked", args)
                self.assertNotIn("isError", result)
                body = (task_dir / "BLOCKED.md").read_text(encoding="utf-8")
                self.assertIn(exact, body)

            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(self._make_task(tmp, f"TASK__limit-{field}"))
                self._write_marker_fixture(tmp, str(task_dir))
                task_before = (task_dir / "TASK.json").read_bytes()
                marker = Path(tmp) / "doc/harness/tasks/.active"
                marker_before = marker.read_bytes()
                args = {
                    "task_id": task_dir.name,
                    "blocked_reason": "reason",
                    "unblock_condition": "resume",
                }
                args[field] = exact + "x"

                result = self._call_in_repo(tmp, "task_blocked", args)

                payload = result["structuredContent"]
                self.assertTrue(result.get("isError"))
                self.assertEqual(payload["error_code"], "ARGUMENT_TOO_LARGE")
                self.assertEqual(payload["field"], field)
                self.assertEqual(payload["reason"], "utf8_byte_limit_exceeded")
                self.assertEqual(payload["actual_bytes"], 122_881)
                self.assertEqual(payload["max_bytes"], 122_880)
                self.assertEqual(
                    payload["rejected_value"], "<string: 122881 UTF-8 bytes>",
                )
                self.assertFalse((task_dir / "BLOCKED.md").exists())
                self.assertEqual((task_dir / "TASK.json").read_bytes(), task_before)
                self.assertEqual(marker.read_bytes(), marker_before)

    def test_task_blocked_invalid_unicode_names_the_field_over_direct_and_framed_calls(self):
        for mode in ("direct", "framed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(self._make_task(tmp, f"TASK__invalid-unicode-{mode}"))
                self._write_marker_fixture(tmp, str(task_dir))
                task_before = (task_dir / "TASK.json").read_bytes()
                marker = Path(tmp) / "doc/harness/tasks/.active"
                marker_before = marker.read_bytes()
                args = {
                    "task_id": task_dir.name,
                    "blocked_reason": "reason",
                    "unblock_condition": "bad\ud800SECRET_SURROGATE",
                }

                if mode == "direct":
                    result = self._call_in_repo(tmp, "task_blocked", args)
                else:
                    request = {
                        "jsonrpc": "2.0",
                        "id": 73,
                        "method": "tools/call",
                        "params": {"name": "wbs", "arguments": {"action": "block", "wbs_id": args["task_id"], **{k: v for k, v in args.items() if k != "task_id"}}},
                    }
                    body = json.dumps(request).encode("utf-8")
                    raw_input = (
                        b"Content-Length: " + str(len(body)).encode("ascii")
                        + b"\r\n\r\n" + body
                    )
                    stdin = io.TextIOWrapper(io.BytesIO(raw_input), encoding="utf-8")
                    stdout_bytes = io.BytesIO()
                    stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
                    server = harness_server.McpServer()
                    with (
                        mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                        mock.patch.object(harness_server.sys, "stdin", stdin),
                        mock.patch.object(harness_server.sys, "stdout", stdout),
                    ):
                        server.handle_request(server._read())
                        stdout.flush()
                    response_body = stdout_bytes.getvalue().split(b"\r\n\r\n", 1)[1]
                    result = json.loads(response_body.decode("utf-8"))["result"]

                payload = result["structuredContent"]
                self.assertTrue(result.get("isError"))
                self.assertEqual(payload["error_code"], "INVALID_ARGUMENT")
                self.assertEqual(payload["field"], "unblock_condition")
                self.assertEqual(payload["reason"], "invalid_utf8")
                self.assertEqual(payload["rejected_value"], "<string: invalid UTF-8>")
                self.assertNotIn("SECRET_SURROGATE", json.dumps(result))
                self.assertFalse((task_dir / "BLOCKED.md").exists())
                self.assertEqual((task_dir / "TASK.json").read_bytes(), task_before)
                self.assertEqual(marker.read_bytes(), marker_before)

    def test_task_selector_errors_report_only_forms_the_field_accepts(self):
        for task_id, canonical in (
            ("bare-safe", "TASK__bare-safe"),
            ("TASK__canonical-safe", "TASK__canonical-safe"),
        ):
            with self.subTest(task_id=task_id), tempfile.TemporaryDirectory() as tmp:
                self._make_task(tmp, canonical)
                accepted = self._call_in_repo(tmp, "task_blocked", {
                    "task_id": task_id,
                    "blocked_reason": "reason",
                    "unblock_condition": "resume",
                })
                self.assertNotIn("isError", accepted)

        with tempfile.TemporaryDirectory() as tmp:
            result = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "doc/harness/tasks/TASK__not-an-id",
                    "blocked_reason": "reason",
                    "unblock_condition": "resume",
                },
            )
        payload = result["structuredContent"]
        self.assertTrue(result.get("isError"))
        self.assertEqual(payload["field"], "task_id")
        self.assertIn("TASK__<safe-id> or <safe-id>", payload["expected"])
        self.assertIn("1-180 ASCII", payload["expected"])
        self.assertNotIn("doc/harness/tasks", payload["expected"])

    def test_local_argument_errors_do_not_fall_back_to_task_id(self):
        cases = (
            (
                {"task_id": "TASK__bad-fresh", "fresh_run": "true"},
                "fresh_run", "wrong_type", "'true'",
            ),
            (
                {"task_id": "TASK__bad-mode", "execution_mode": "large"},
                "execution_mode", "invalid_choice", "'large'",
            ),
            (
                {"task_id": "TASK__bad-mode-type", "execution_mode": []},
                "execution_mode", "wrong_type", "<non-string: list>",
            ),
        )
        for args, field, reason, rejected in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                result = self._call_in_repo(tmp, "task_start", args)
                payload = result["structuredContent"]
                self.assertTrue(result.get("isError"))
                self.assertEqual(payload["error_code"], "INVALID_ARGUMENT")
                self.assertEqual(payload["field"], field)
                self.assertEqual(payload["reason"], reason)
                self.assertEqual(payload["rejected_value"], rejected)
                self.assertEqual(payload["expected"], harness_server._FIELD_EXPECTED[field])
                self.assertIn(field, payload["next_action"])

    def test_selector_expected_text_is_field_specific(self):
        cases = (
            (
                "task_start",
                {"task_dir": "outside/TASK__safe"},
                "task_dir",
                "doc/harness/tasks/TASK__<safe-id>",
            ),
            ("task_start", {"slug": "bad/path"}, "slug", "1-180 ASCII"),
        )
        for tool, args, field, expected_fragment in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                result = self._call_in_repo(tmp, tool, args)
                payload = result["structuredContent"]
                self.assertTrue(result.get("isError"))
                self.assertEqual(payload["field"], field)
                self.assertIn(expected_fragment, payload["expected"])

    def test_task_blocked_rolls_back_when_marker_cleanup_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__blocked-marker-failure")
            self._write_marker_fixture(tmp, task_dir)
            with (
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=task_dir,
                ),
                mock.patch.object(
                    harness_server, "clear_active_marker",
                    side_effect=OSError("marker cleanup unavailable"),
                ),
            ):
                result = call_execution(harness_server, "task_blocked", {
                    "task_id": "TASK__blocked-marker-failure",
                    "blocked_reason": "pause",
                    "unblock_condition": "resume",
                })
            self.assertTrue(result.get("isError"))
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())
            self.assertEqual(
                Path(harness_server.resolve_active_task_dir(tmp)).resolve(),
                Path(task_dir).resolve(),
            )

    def test_task_start_rejects_terminal_leaf_symlinks_without_touching_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__leaf-safe"
            task_dir.mkdir(parents=True)
            sentinel = Path(tmp) / "task-sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            request = Path(tmp) / "request.txt"
            request.write_text("request body", encoding="utf-8")
            (task_dir / "REQUEST.md").symlink_to(sentinel)
            (task_dir / "BLOCKED.md").symlink_to(sentinel)
            active = task_dir.parent / ".active"
            active.symlink_to(sentinel)

            start = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__leaf-safe", "request_file": str(request)},
            )
            self.assertTrue(start.get("isError"))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((task_dir / "REQUEST.md").is_symlink())
            self.assertTrue((task_dir / "BLOCKED.md").is_symlink())

    def test_task_start_existing_task_refuses_unsafe_blocker_for_default_and_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__unsafe-blocker"))
            external = Path(tmp) / "external-blocker.md"
            external.write_bytes(b"do not touch\n")
            blocker = task_dir / "BLOCKED.md"
            blocker.symlink_to(external)
            control_before = (task_dir / "TASK.json").read_bytes()

            for args in (
                {"task_id": task_dir.name},
                {"task_id": task_dir.name, "fresh_run": True},
            ):
                with self.subTest(args=args):
                    result = self._call_in_repo(tmp, "task_start", args)
                    self.assertTrue(result.get("isError"))
                    self.assertIn("fresh_run is not repair authority", result["structuredContent"]["next_action"])
                    self.assertEqual((task_dir / "TASK.json").read_bytes(), control_before)
                    self.assertEqual(external.read_bytes(), b"do not touch\n")
                    self.assertTrue(blocker.is_symlink())

    def test_task_blocked_missing_task_leaves_no_orphan_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__typo"
            result = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__typo",
                    "blocked_reason": "missing",
                    "unblock_condition": "start it first",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("wbs(action='open')", result["structuredContent"].get("next_action", ""))
            self.assertFalse(task_dir.exists())

    def test_task_blocked_rejects_symlinked_state_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__unsafe-state"))
            state = task_dir / "TASK.json"
            external = Path(tmp) / "external-state.json"
            external.write_text(state.read_text(encoding="utf-8"), encoding="utf-8")
            state.unlink()
            state.symlink_to(external)
            before = external.read_bytes()
            result = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__unsafe-state",
                    "blocked_reason": "environment",
                    "unblock_condition": "restore environment",
                },
            )
            self.assertTrue(result.get("isError"))
            self.assertEqual(external.read_bytes(), before)
            self.assertTrue(state.is_symlink())

    def test_task_mutators_reject_invalid_control_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__expected"))
            state = task_dir / "TASK.json"
            state.write_text("{}\n", encoding="utf-8")
            before = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}

            plan = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_id": "TASK__expected", "plan": "# Replacement\n"},
            )
            blocked = self._call_in_repo(
                tmp,
                "task_blocked",
                {
                    "task_id": "TASK__expected",
                    "blocked_reason": "environment",
                    "unblock_condition": "restore environment",
                },
            )
            self.assertTrue(plan.get("isError"))
            self.assertTrue(blocked.get("isError"))
            self.assertIn("invalid TASK.json", plan["structuredContent"]["error"])
            self.assertIn("invalid TASK.json", blocked["structuredContent"]["error"])
            after = {path.name: path.read_bytes() for path in task_dir.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((task_dir / "BLOCKED.md").exists())

    def test_task_start_explicitly_resumes_blocked_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-blocked")
            self._write_historical_receipts(task_dir)
            (Path(task_dir) / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
            control_before = Path(task_dir, "TASK.json").read_bytes()
            receipts_before = Path(task_dir, "RECEIPTS.jsonl").read_bytes()
            run_before = harness_server.read_task_control(task_dir)["run_id"]
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = call_execution(harness_server,
                    "task_start", {"task_id": "TASK__resume-blocked"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertNotIn("isError", result)
            context = result["structuredContent"]["task_context"]
            self.assertEqual(context["status"], "open")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertFalse(result["structuredContent"]["task_created"])
            self.assertTrue(result["structuredContent"]["resumed"])
            self.assertEqual(result["structuredContent"]["run_action"], "preserved")
            self.assertTrue(result["structuredContent"]["evidence_preserved"])
            self.assertEqual(result["structuredContent"]["run_id"], run_before)
            self.assertEqual(Path(task_dir, "TASK.json").read_bytes(), control_before)
            self.assertEqual(Path(task_dir, "RECEIPTS.jsonl").read_bytes(), receipts_before)
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())
            self.assertEqual(
                harness_server.task_control_status(
                    task_dir, harness_server.read_task_control(task_dir)
                ), "open",
            )

    def test_task_start_requires_fresh_run_for_closed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-closed")
            self._close_fixture(task_dir)
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = call_execution(harness_server,
                    "task_start", {"task_id": "TASK__resume-closed"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertTrue(result.get("isError"))
            self.assertIn("fresh_run", result["structuredContent"]["next_action"])
            self.assertTrue(
                harness_server.read_task_control(task_dir)["closed"]
            )

    def test_task_start_fresh_run_reopens_closed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__fresh-closed")
            self._write_historical_receipts(task_dir)
            self._close_fixture(task_dir)
            old_run_id = harness_server.read_task_control(task_dir)["run_id"]

            result = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__fresh-closed", "fresh_run": True},
            )

            payload = result["structuredContent"]
            self.assertNotIn("isError", result)
            self.assertEqual(payload["run_action"], "reset")
            self.assertEqual(payload["previous_run_id"], old_run_id)
            self.assertNotEqual(payload["run_id"], old_run_id)
            self.assertFalse(
                harness_server.read_task_control(task_dir)["closed"]
            )
            self.assertTrue(Path(task_dir, "RECEIPTS.jsonl").exists())


    def test_task_start_rejects_mutating_existing_options_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__preserve-options"))
            self._write_historical_receipts(str(task_dir))
            request = Path(tmp, "replacement.md")
            request.write_text("replacement\n", encoding="utf-8")

            for extra in (
                {"execution_mode": "micro"},
                {"request_file": str(request)},
                {"request_file": str(request), "fresh_run": True},
            ):
                with self.subTest(extra=extra):
                    before = {
                        path.name: path.read_bytes()
                        for path in task_dir.iterdir() if path.is_file()
                    }
                    result = self._call_in_repo(
                        tmp,
                        "task_start",
                        {"task_id": task_dir.name, **extra},
                    )
                    self.assertTrue(result.get("isError"))
                    self.assertTrue(result["structuredContent"]["next_action"])
                    after = {
                        path.name: path.read_bytes()
                        for path in task_dir.iterdir() if path.is_file()
                    }
                    self.assertEqual(after, before)

    def test_task_start_requires_a_real_boolean_fresh_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__strict-fresh-run")
            before = Path(task_dir, "TASK.json").read_bytes()

            result = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__strict-fresh-run", "fresh_run": "true"},
            )

            self.assertTrue(result.get("isError"))
            self.assertIn("must be a boolean", result["structuredContent"]["error"])
            self.assertEqual(Path(task_dir, "TASK.json").read_bytes(), before)

    def test_task_start_absent_task_refuses_competing_open_focus_without_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            focused = Path(self._make_task(tmp, "TASK__focused-open"))
            self._write_marker_fixture(tmp, str(focused), session_id="default")
            marker_paths = [
                Path(tmp) / "doc/harness/tasks/.active",
                Path(tmp) / "doc/harness/tasks/.active_sessions/default.json",
            ]
            marker_before = {path: path.read_bytes() for path in marker_paths}
            candidate = Path(tmp) / "doc/harness/tasks/TASK__must-stay-absent"

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": candidate.name},
            )

            self.assertTrue(result.get("isError"))
            self.assertEqual(result["structuredContent"]["status"], "absent")
            self.assertFalse(candidate.exists())
            self.assertEqual(
                {path: path.read_bytes() for path in marker_paths}, marker_before,
            )

    def test_task_start_open_resume_preserves_generation_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-open")
            old_run_id = harness_server.read_task_control(task_dir)["run_id"]
            self._write_historical_receipts(task_dir)
            control_before = Path(task_dir, "TASK.json").read_bytes()
            receipts_before = Path(task_dir, "RECEIPTS.jsonl").read_bytes()


            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__resume-open"},
            )

            payload = result["structuredContent"]
            resumed = harness_server.read_task_control(task_dir)
            self.assertNotIn("isError", result)
            self.assertTrue(payload["resumed"])
            self.assertEqual(payload["run_id"], old_run_id)
            self.assertEqual(payload["run_id"], resumed["run_id"])
            self.assertEqual(payload["run_action"], "preserved")
            self.assertTrue(payload["evidence_preserved"])
            self.assertEqual(payload["task_context"]["runtime_verdict"], "PENDING")
            self.assertEqual(Path(task_dir, "TASK.json").read_bytes(), control_before)
            self.assertEqual(Path(task_dir, "RECEIPTS.jsonl").read_bytes(), receipts_before)


    def test_task_start_fresh_run_rotates_generation_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__fresh-open")
            old_run_id = harness_server.read_task_control(task_dir)["run_id"]
            self._write_historical_receipts(task_dir)

            result = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__fresh-open", "fresh_run": True},
            )

            payload = result["structuredContent"]
            self.assertNotIn("isError", result)
            self.assertEqual(payload["run_action"], "reset")
            self.assertFalse(payload["evidence_preserved"])
            self.assertEqual(payload["previous_run_id"], old_run_id)
            self.assertNotEqual(payload["run_id"], old_run_id)
            self.assertTrue((Path(task_dir) / "RECEIPTS.jsonl").exists())

    def test_task_start_fresh_run_rotates_blocked_task_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__fresh-blocked"))
            self._write_historical_receipts(str(task_dir))
            (task_dir / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
            old_run_id = harness_server.read_task_control(str(task_dir))["run_id"]

            result = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": task_dir.name, "fresh_run": True},
            )

            payload = result["structuredContent"]
            self.assertNotIn("isError", result)
            self.assertEqual(payload["run_action"], "reset")
            self.assertEqual(payload["previous_run_id"], old_run_id)
            self.assertNotEqual(payload["run_id"], old_run_id)
            self.assertTrue((task_dir / "RECEIPTS.jsonl").exists())
            self.assertFalse((task_dir / "BLOCKED.md").exists())

    def test_task_start_blocked_preserve_restores_blocker_when_marker_publish_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__preserve-marker-failure"))
            blocked = task_dir / "BLOCKED.md"
            blocked.write_bytes(b"# BLOCKED\nexact blocker\n")
            control_before = (task_dir / "TASK.json").read_bytes()
            blocker_before = blocked.read_bytes()
            real_replace = harness_lib.os.replace
            marker_failed = False

            def fail_first_marker_replace(src, dst, *args, **kwargs):
                nonlocal marker_failed
                if not marker_failed and ".active_sessions" in str(dst):
                    marker_failed = True
                    raise OSError("marker unavailable")
                return real_replace(src, dst, *args, **kwargs)

            with mock.patch.object(
                harness_lib.os, "replace", side_effect=fail_first_marker_replace,
            ):
                result = self._call_in_repo(
                    tmp, "task_start", {"task_id": task_dir.name},
                )

            self.assertTrue(result.get("isError"))
            self.assertIn("marker unavailable", result["structuredContent"]["error"])
            self.assertEqual((task_dir / "TASK.json").read_bytes(), control_before)
            self.assertEqual(blocked.read_bytes(), blocker_before)

    def test_task_start_blocked_preserve_restores_marker_when_unlink_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__preserve-unlink-failure"))
            blocked = task_dir / "BLOCKED.md"
            blocked.write_bytes(b"# BLOCKED\nexact blocker\n")
            marker_before = harness_server.active_marker_snapshot(tmp, session_id="default")
            real_unlink = harness_server.os.unlink

            def fail_blocker_unlink(path, *args, **kwargs):
                if Path(path) == blocked:
                    raise OSError("blocker unlink unavailable")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(harness_server.os, "unlink", side_effect=fail_blocker_unlink):
                result = self._call_in_repo(
                    tmp, "task_start", {"task_id": task_dir.name},
                )

            self.assertTrue(result.get("isError"))
            self.assertIn("blocker unlink unavailable", result["structuredContent"]["error"])
            self.assertEqual(blocked.read_bytes(), b"# BLOCKED\nexact blocker\n")
            self.assertEqual(
                harness_server.active_marker_snapshot(tmp, session_id="default"), marker_before,
            )

    def test_task_start_blocked_preserve_attempts_both_rollback_legs(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__preserve-rollback-legs"))
            (task_dir / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
            marker_restore = mock.Mock()

            with (
                mock.patch.object(
                    harness_server, "active_task_binding_matches", return_value=False,
                ),
                mock.patch.object(
                    harness_server, "_restore_text_snapshots",
                    side_effect=OSError("blocker restore unavailable"),
                ),
                mock.patch.object(
                    harness_server, "restore_active_marker_snapshot", marker_restore,
                ),
            ):
                result = self._call_in_repo(
                    tmp, "task_start", {"task_id": task_dir.name},
                )

            self.assertTrue(result.get("isError"))
            self.assertIn("rollback was incomplete", result["structuredContent"]["error"])
            marker_restore.assert_called_once()


    def test_task_start_resume_waits_for_task_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-serialized")
            original_run = harness_server.read_task_control(task_dir)["run_id"]
            finished = threading.Event()
            result = {}

            def resume():
                result.update(self._call_in_repo(
                    tmp, "task_start", {"task_id": "TASK__resume-serialized"},
                ))
                finished.set()

            with harness_server.task_control_transaction(task_dir):
                worker = threading.Thread(target=resume)
                worker.start()
                time.sleep(0.1)
                self.assertFalse(finished.is_set())
                self.assertEqual(
                    harness_server.read_task_control(task_dir)["run_id"], original_run,
                )
            worker.join(5)
            self.assertTrue(finished.is_set())
            self.assertNotIn("isError", result)
            self.assertEqual(
                harness_server.read_task_control(task_dir)["run_id"], original_run,
            )

    def test_task_start_returns_ready_with_warnings_after_committed_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__context-warning"

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    side_effect=RuntimeError("context scan exceeded request budget"),
                ),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__context-warning"}
                )

            self.assertNotIn("isError", result)
            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertTrue(payload["task_created"])
            self.assertFalse(payload["resumed"])
            self.assertEqual(payload["run_action"], "created")
            self.assertFalse(payload["evidence_preserved"])
            self.assertIsNone(payload["previous_run_id"])
            self.assertIsInstance(payload["task_context"], dict)
            self.assertFalse(payload["task_context"]["context_complete"])
            self.assertFalse(payload["task_context"]["source_write_allowed"])
            self.assertIn("Do not call wbs(action='open') again", payload["next_action"])
            self.assertEqual(payload["warnings"][0]["code"], "TASK_CONTEXT_DEFERRED")
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            self.assertTrue((task_dir / "TASK.json").is_file())

    def test_task_start_fresh_run_on_absent_task_reports_creation_not_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._call_in_repo(
                tmp,
                "task_start",
                {"task_id": "TASK__fresh-absent", "fresh_run": True},
            )

            payload = result["structuredContent"]
            self.assertNotIn("isError", result)
            self.assertEqual(payload["run_action"], "created")
            self.assertFalse(payload["evidence_preserved"])
            self.assertIsNone(payload["previous_run_id"])

    def test_task_start_defers_error_shaped_compact_context_after_committed_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__context-error-result"

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    return_value={"error": "context scan unavailable"},
                ),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__context-error-result"}
                )

            self.assertNotIn("isError", result)
            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertEqual(payload["warnings"][0]["code"], "TASK_CONTEXT_DEFERRED")
            self.assertIn("context scan unavailable", payload["warnings"][0]["detail"])
            self.assertFalse(payload["task_context"]["source_write_allowed"])

    def test_preserved_resume_context_failure_refuses_and_restores_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__resumed-warning"
            task_dir.mkdir(parents=True)
            self._write_control_fixture(str(task_dir), {
                "run_id": harness_lib.new_uuid7(), "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            })

            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(
                    harness_server,
                    "emit_compact_context",
                    side_effect=RuntimeError("context delayed"),
                ),
            ):
                before_control = (task_dir / "TASK.json").read_bytes()
                with self.assertRaisesRegex(RuntimeError, "context delayed"):
                    harness_server.handle_task_start({"task_id": "TASK__resumed-warning"})
                self.assertEqual((task_dir / "TASK.json").read_bytes(), before_control)
                self.assertFalse((Path(tmp) / "doc/harness/tasks/.active").exists())

    def test_failed_existing_resume_restores_state_and_receipts(self):
        for terminal_status in ("open", "closed", "blocked"):
            with self.subTest(status=terminal_status), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / f"doc/harness/tasks/TASK__resume-{terminal_status}"
                task_dir.mkdir(parents=True)
                self._write_control_fixture(str(task_dir), {
                    "run_id": harness_lib.new_uuid7(),
                    "execution_mode": "standard",
                    "required_lenses": ["review-code", "qa-cli"],
                    "closed": False,
                })
                if terminal_status in {"open", "closed"}:
                    self._write_historical_receipts(str(task_dir))
                if terminal_status == "closed":
                    self._close_fixture(str(task_dir))
                elif terminal_status == "blocked":
                    (task_dir / "BLOCKED.md").write_text("# BLOCKED\n", encoding="utf-8")
                state = harness_server.read_task_control(str(task_dir))
                receipt = task_dir / "RECEIPTS.jsonl"
                prior_receipt = receipt.read_text(encoding="utf-8") if receipt.exists() else None
                (Path(tmp) / ".git").mkdir()
                prior_cwd = os.getcwd()
                real_replace = harness_lib.os.replace

                def fail_marker_replace(src, dst, *args, **kwargs):
                    if str(dst).endswith(".json") and ".active_sessions" in str(dst):
                        raise OSError("marker unavailable")
                    return real_replace(src, dst, *args, **kwargs)

                with mock.patch.object(harness_lib.os, "replace", side_effect=fail_marker_replace):
                    os.chdir(tmp)
                    try:
                        with self.assertRaisesRegex(OSError, "marker unavailable"):
                            harness_server.handle_task_start(
                                {
                                    "task_id": f"TASK__resume-{terminal_status}",
                                    "fresh_run": True,
                                }
                            )
                    finally:
                        os.chdir(prior_cwd)
                restored = harness_server.read_task_control(str(task_dir))
                self.assertEqual(restored, state)
                self.assertEqual(harness_server.task_control_status(str(task_dir), restored), terminal_status)
                if prior_receipt is None:
                    self.assertFalse(receipt.exists())
                else:
                    self.assertEqual(receipt.read_text(encoding="utf-8"), prior_receipt)

    def test_invalid_execution_mode_does_not_create_or_reopen_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            new_task = Path(tmp) / "doc/harness/tasks/TASK__invalid-mode-new"
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(new_task)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
            ):
                with self.assertRaisesRegex(ValueError, "execution_mode"):
                    harness_server.handle_task_start({
                        "task_id": "TASK__invalid-mode-new",
                        "execution_mode": "bogus",
                    })
            self.assertFalse((new_task / "TASK.json").exists())

            terminal_task = Path(tmp) / "doc/harness/tasks/TASK__invalid-mode-closed"
            terminal_task.mkdir(parents=True)
            original = {
                "run_id": harness_lib.new_uuid7(),
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            }
            self._write_control_fixture(str(terminal_task), original)
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(terminal_task)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
            ):
                with self.assertRaisesRegex(ValueError, "execution_mode"):
                    harness_server.handle_task_start({
                        "task_id": "TASK__invalid-mode-closed",
                        "execution_mode": "bogus",
                    })
            self.assertEqual(
                harness_server.read_task_control(str(terminal_task)), original
            )

    def test_old_only_pack_starts_fresh_without_migrating_legacy_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__resume-cleanup"
            task_dir.mkdir(parents=True)
            obsolete = (
                "TASK_STATE.yaml", "TASK_RUN.json", "PLAN.meta.json",
                "TASK_CLOSE_RECEIPT.json", "INSTALL_RECEIPT.json",
                "AUDIT_TRAIL.md", "ENVIRONMENT_SNAPSHOT.md", ".receipts.lock",
            )
            for name in obsolete:
                (task_dir / name).write_text("legacy\n", encoding="utf-8")
            with (
                mock.patch.object(
                    harness_server, "canonical_task_dir", return_value=str(task_dir)
                ),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
            ):
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__resume-cleanup"}
                )
            self.assertNotIn("isError", result)
            self.assertTrue(harness_server.read_task_control(str(task_dir)))
            for name in obsolete:
                self.assertEqual((task_dir / name).read_text(), "legacy\n")

    def test_failed_resume_preserves_preexisting_active_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__active-resume"
            task_dir.mkdir(parents=True)
            self._write_control_fixture(str(task_dir), {
                "run_id": harness_lib.new_uuid7(), "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            })
            self._write_marker_fixture(tmp, str(task_dir))
            real_replace = harness_lib.os.replace

            def fail_marker(src, dst, *args, **kwargs):
                if str(dst).endswith(".json") and ".active_sessions" in str(dst):
                    raise OSError("resume marker interrupted")
                return real_replace(src, dst, *args, **kwargs)

            prior_cwd = os.getcwd()
            with mock.patch.object(harness_lib.os, "replace", side_effect=fail_marker):
                os.chdir(tmp)
                try:
                    with self.assertRaisesRegex(RuntimeError, "rollback was incomplete"):
                        harness_server.handle_task_start(
                            {"task_id": "TASK__active-resume"}
                        )
                finally:
                    os.chdir(prior_cwd)
            self.assertEqual(
                Path(harness_server.resolve_active_task_dir(tmp)).resolve(),
                task_dir.resolve(),
            )

    def test_new_start_refuses_different_preexisting_open_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_a = Path(tmp) / "doc/harness/tasks/TASK__active-a"
            task_b = Path(tmp) / "doc/harness/tasks/TASK__failed-b"
            task_a.mkdir(parents=True)
            self._write_control_fixture(str(task_a), {
                "run_id": harness_lib.new_uuid7(), "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "closed": False,
            })
            self._write_marker_fixture(tmp, str(task_a))
            prior_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                result = harness_server.handle_task_start({"task_id": "TASK__failed-b"})
            finally:
                os.chdir(prior_cwd)
            self.assertTrue(result.get("isError"))
            self.assertEqual(result["structuredContent"]["status"], "absent")
            self.assertEqual(
                Path(harness_server.resolve_active_task_dir(tmp)).resolve(),
                task_a.resolve(),
            )
            self.assertFalse(task_b.exists())

    def test_active_marker_snapshot_cannot_be_restored_by_arbitrary_caller(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp) / "doc/harness/tasks"
            sessions = tasks / ".active_sessions"
            sessions.mkdir(parents=True)
            outside = Path(tmp) / "outside-marker"
            outside.write_text("preserve\n", encoding="utf-8")
            marker = tasks / ".active"
            marker.symlink_to(outside)
            snapshot = harness_server.active_marker_snapshot(tmp)
            marker.unlink()
            marker.write_text("replacement\n", encoding="utf-8")
            with self.assertRaisesRegex(PermissionError, "task-control runtime"):
                harness_server.restore_active_marker_snapshot(snapshot)
            self.assertFalse(marker.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "replacement\n")
            self.assertEqual(outside.read_text(encoding="utf-8"), "preserve\n")


    def test_new_task_rolls_back_when_active_marker_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._run_git(tmp, "init", "-q")
            self._run_git(tmp, "config", "user.email", "a@b")
            self._run_git(tmp, "config", "user.name", "a")
            (Path(tmp) / "README.md").write_text("# repo\n", encoding="utf-8")
            self._run_git(tmp, "add", "README.md")
            self._run_git(tmp, "commit", "-qm", "init")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__marker-failure"
            real_replace = harness_lib.os.replace

            def fail_marker(src, dst, *args, **kwargs):
                if str(dst).endswith(".json") and ".active_sessions" in str(dst):
                    raise OSError("marker unavailable")
                return real_replace(src, dst, *args, **kwargs)

            prior_cwd = os.getcwd()
            with mock.patch.object(harness_lib.os, "replace", side_effect=fail_marker):
                os.chdir(tmp)
                with self.assertRaisesRegex(OSError, "marker unavailable"):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__marker-failure"}
                    )
                os.chdir(prior_cwd)
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            self.assertFalse((task_dir / "TASK_STATE.yaml").exists())

    def test_write_plan_updates_required_lenses_without_audit_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__planmcp")
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {
                    "task_dir": task_dir,
                    "plan": "# MCP Plan\n",
                    "required_lenses": ["qa-api", "review-security", "review-code"],
                },
            )
            self.assertNotIn("isError", result)
            self.assertEqual(
                result["structuredContent"]["written"],
                ["PLAN.md", "TASK.json"],
            )
            bytes_written = result["structuredContent"]["bytes_written"]
            self.assertGreater(bytes_written["PLAN.md"], 0)
            self.assertGreater(bytes_written["TASK.json"], 0)
            self.assertFalse((Path(task_dir) / "CHECKS.yaml").exists())
            self.assertEqual((Path(task_dir) / "PLAN.md").read_text(encoding="utf-8"), "# MCP Plan\n")
            control = json.loads((Path(task_dir) / "TASK.json").read_text(encoding="utf-8"))
            self.assertEqual(
                control["required_lenses"],
                ["review-code", "review-security", "qa-api"],
            )
            self.assertFalse((Path(task_dir) / "PLAN.meta.json").exists())
            self.assertFalse((Path(task_dir) / "AUDIT_TRAIL.md").exists())

    def test_terminal_task_rejects_plan_and_blocked_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__terminal-mutators")
            self._write_historical_receipts(task_dir)
            self._close_fixture(task_dir)
            plan_path = Path(task_dir) / "PLAN.md"
            control_path = Path(task_dir) / "TASK.json"
            original_plan = plan_path.read_bytes()
            original_control = control_path.read_bytes()

            plan = self._call_in_repo(tmp, "write_plan", {
                "task_id": "TASK__terminal-mutators",
                "plan": "# changed\n",
                "required_lenses": ["review-code", "review-security", "qa-cli"],
            })
            blocked = self._call_in_repo(tmp, "task_blocked", {
                "task_id": "TASK__terminal-mutators",
                "blocked_reason": "late",
                "unblock_condition": "restart",
            })

            self.assertTrue(plan.get("isError"))
            self.assertTrue(blocked.get("isError"))
            self.assertIn("not open", plan["structuredContent"]["error"])
            self.assertIn("not open", blocked["structuredContent"]["error"])
            self.assertEqual(plan_path.read_bytes(), original_plan)
            self.assertEqual(control_path.read_bytes(), original_control)
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())

    def test_plan_and_blocked_mutators_use_task_control_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__serialized-mutators")
            original_transaction = harness_server.task_control_transaction
            entered = []

            from contextlib import contextmanager

            @contextmanager
            def observed_transaction(path):
                entered.append(Path(path).name)
                with original_transaction(path):
                    yield

            with mock.patch.object(
                harness_server, "task_control_transaction",
                side_effect=observed_transaction,
            ):
                plan = self._call_in_repo(tmp, "write_plan", {
                    "task_id": "TASK__serialized-mutators", "plan": "# plan\n",
                })
                blocked = self._call_in_repo(tmp, "task_blocked", {
                    "task_id": "TASK__serialized-mutators",
                    "blocked_reason": "pause", "unblock_condition": "resume",
                })
            self.assertNotIn("isError", plan)
            self.assertNotIn("isError", blocked)
            self.assertEqual(entered, [
                "TASK__serialized-mutators", "TASK__serialized-mutators",
            ])

    def test_write_plan_rejects_empty_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyplan")
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": " \n\t"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("empty PLAN.md", result["structuredContent"]["error"])

    def test_write_plan_rejects_removed_audit_argument_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__emptyaudit")
            before = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            result = self._call_in_repo(
                tmp,
                "write_plan",
                {"task_dir": task_dir, "plan": "# Replacement\n", "audit": "removed"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("unsupported", result["structuredContent"]["error"])
            after = {p.name: p.read_bytes() for p in Path(task_dir).iterdir() if p.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((Path(task_dir) / "AUDIT_TRAIL.md").exists())


if __name__ == "__main__":
    unittest.main()
