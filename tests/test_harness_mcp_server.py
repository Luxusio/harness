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


spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
assert spec and spec.loader
harness_server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = harness_server
spec.loader.exec_module(harness_server)
import _lib as harness_lib  # type: ignore


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


def _record_receipt_fixture(task_dir, receipt):
    source = harness_lib._receipt_short(receipt.get("source") or "claude_hook", 100)
    agent_id = harness_lib._receipt_short(receipt.get("agent_id") or receipt.get("id"), 300)
    agent_type = harness_lib._receipt_short(receipt.get("agent_type"), 300)
    lens = harness_lib._infer_receipt_lens(agent_type, receipt.get("lens"))
    event = harness_lib._receipt_short(receipt.get("event"), 20).lower()
    verdict = harness_lib._receipt_short(receipt.get("verdict") or "", 40).upper()
    raw_summary = str(receipt.get("summary") or "")
    if event == "completed":
        verdict, summary = harness_lib.normalize_receipt_completion(lens, raw_summary, verdict)
    else:
        verdict, summary = "", ""
    control = harness_lib.read_task_control(task_dir)
    entry = {
        "ts": harness_lib._receipt_now_iso(), "event": event, "source": source,
        "task_run_id": str(control["run_id"]),
        "runtime_id": str(receipt.get("runtime_id") or "").strip(),
        "agent_id": agent_id, "agent_type": agent_type, "lens": lens,
        "verdict": verdict, "summary": summary,
    }
    harness_lib._validate_receipt_runtime_id(source, entry["runtime_id"])
    assert harness_lib._receipt_entry_semantics_valid(entry)
    if harness_lib.task_control_status(task_dir, control) in {"closed", "blocked", "invalid"}:
        raise RuntimeError("receipt stream is terminal")
    with (Path(task_dir) / harness_lib.RECEIPTS_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


class HarnessMcpServerTests(unittest.TestCase):
    def setUp(self):
        self._receipt_auth = mock.patch.object(
            harness_server, "record_subagent_receipt", side_effect=_record_receipt_fixture,
        )
        self._receipt_auth.start()

    def tearDown(self):
        self._receipt_auth.stop()

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
                "close_receipt_fingerprint": None,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nSmall plan.\n", encoding="utf-8")
        return str(task_dir)

    def _call_in_repo(self, repo_root: str, name: str, args: dict) -> dict:
        with mock.patch.object(harness_server, "find_repo_root", return_value=repo_root):
            return harness_server.call_tool(name, args)

    def _write_control_fixture(self, task_dir: str, control: dict) -> None:
        Path(task_dir, "TASK.json").write_text(
            json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    def _close_fixture(self, task_dir: str) -> None:
        control = harness_server.read_task_control(task_dir)
        control["close_receipt_fingerprint"] = harness_server.receipt_stream_fingerprint(task_dir)
        self._write_control_fixture(task_dir, control)

    def _write_marker_fixture(self, repo_root: str, task_dir: str, session_id: str = "") -> None:
        _write_marker_fixture(repo_root, task_dir, session_id)

    def _write_subagent_receipt(
        self,
        task_dir: str,
        *,
        agent_id: str = "agent-1",
        agent_type: str = "harness:qa-cli",
        source: str = "claude_hook",
    ) -> None:
        if not harness_lib.read_task_control(task_dir):
            raise AssertionError("test task requires valid TASK.json")
        for payload in (
            {
                "agent_id": "review-1", "agent_type": "harness:review-code",
                "lens": "review-code", "event": "started", "source": source,
                "runtime_id": "claude:test-session:review-1",
            },
            {
                "agent_id": "review-1", "agent_type": "harness:review-code",
                "lens": "review-code",
                "event": "completed", "verdict": "PASS",
                "summary": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0",
                "source": source,
                "runtime_id": "claude:test-session:review-1",
            },
            {
                "agent_id": agent_id, "agent_type": agent_type,
                "lens": "qa-cli", "event": "started", "source": source,
                "runtime_id": f"claude:test-session:{agent_id}",
            },
            {
                "agent_id": agent_id, "agent_type": agent_type, "event": "completed",
                "lens": "qa-cli", "verdict": "PASS", "summary": "VERDICT: PASS",
                "source": source,
                "runtime_id": f"claude:test-session:{agent_id}",
            },
        ):
            harness_server.record_subagent_receipt(task_dir, payload)

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
                "close_receipt_fingerprint": None,
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
                "close_receipt_fingerprint": None,
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

    def test_lifecycle_handlers_never_enter_git_snapshot_helpers(self):
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
                self._write_subagent_receipt(str(task_dir))
                context = harness_server.handle_task_context(
                    {"task_id": "TASK__no-git-handlers"}
                )
                verified = harness_server.handle_task_verify(
                    {"task_id": "TASK__no-git-handlers"}
                )
                closed = harness_server.handle_task_close(
                    {"task_id": "TASK__no-git-handlers"}
                )
                os.chdir(prior_cwd)
            self.assertNotIn("isError", context)
            self.assertNotIn("isError", verified)
            self.assertNotIn("isError", closed)
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            with self.assertRaisesRegex(RuntimeError, "receipt stream is terminal"):
                harness_server.record_subagent_receipt(
                    str(task_dir),
                    {
                        "agent_id": "late-agent", "agent_type": "harness:qa-cli",
                        "lens": "qa-cli", "event": "started",
                        "source": "claude_hook", "runtime_id": "claude:test-session:late-agent",
                    },
                )

    def test_context_verify_and_close_each_read_one_receipt_snapshot(self):
        handlers = (
            harness_server.handle_task_context,
            harness_server.handle_task_verify,
            harness_server.handle_task_close,
        )
        for index, handler in enumerate(handlers):
            with self.subTest(handler=handler.__name__), tempfile.TemporaryDirectory() as tmp:
                manifest = Path(tmp) / "doc/harness/manifest.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
                (Path(tmp) / ".git").mkdir()
                task_id = f"TASK__one-snapshot-{index}"
                task_dir = Path(tmp) / "doc/harness/tasks" / task_id
                prior_cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    harness_server.handle_task_start({"task_id": task_id})
                    (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
                    self._write_subagent_receipt(str(task_dir))
                    with mock.patch.object(
                        harness_lib,
                        "_receipt_snapshot_unlocked",
                        wraps=harness_lib._receipt_snapshot_unlocked,
                    ) as snapshot:
                        result = handler({"task_id": task_id})
                finally:
                    os.chdir(prior_cwd)
                self.assertNotIn("isError", result)
                self.assertEqual(snapshot.call_count, 1)

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
            harness_server.record_subagent_receipt(task_dir, {
                "agent_id": "qa-1", "agent_type": "harness:qa-cli",
                "lens": "qa-cli", "event": "started",
                "source": "claude_hook", "runtime_id": "claude:test-session:qa-1",
            })
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_completed_qa_fail_controls_runtime_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__qa-fail")
            for payload in (
                {
                    "agent_id": "review-1", "agent_type": "harness:review-code",
                    "lens": "review-code", "event": "started",
                    "source": "claude_hook", "runtime_id": "claude:test-session:review-1",
                },
                {
                    "agent_id": "review-1", "agent_type": "harness:review-code",
                    "lens": "review-code",
                    "event": "completed", "verdict": "PASS",
                    "summary": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0",
                    "source": "claude_hook", "runtime_id": "claude:test-session:review-1",
                },
                {
                    "agent_id": "qa-1", "agent_type": "harness:qa-cli",
                    "lens": "qa-cli", "event": "started",
                    "source": "claude_hook", "runtime_id": "claude:test-session:qa-1",
                },
            ):
                harness_server.record_subagent_receipt(task_dir, payload)
            harness_server.record_subagent_receipt(
                task_dir,
                {
                    "agent_id": "qa-1",
                    "agent_type": "harness:qa-cli",
                    "lens": "qa-cli",
                    "event": "completed",
                    "verdict": "FAIL",
                    "summary": "VERDICT: FAIL",
                    "source": "claude_hook",
                    "runtime_id": "claude:test-session:qa-1",
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
                    "event": "started",
                    "summary": "rerun started",
                    "source": "claude_hook", "runtime_id": "claude:test-session:qa-new",
                },
            )
            self.assertEqual(harness_server.receipt_runtime_verdict(task_dir), "PENDING")

    def test_unknown_tool_returns_error_payload(self):
        result = harness_server.call_tool("does_not_exist", {})
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])

    def test_git_binding_error_returns_structured_recovery_details(self):
        original = harness_server.TOOLS["task_context"]["handler"]

        def fail(_args):
            raise harness_lib.GitBindingError(
                "REGISTERED_WORKTREE_BINDING_MISMATCH",
                "registered source failed validation",
                path="services/front",
                invariant="admin_gitdir_backreference",
                next_action="Repair the worktree and retry.",
            )

        harness_server.TOOLS["task_context"]["handler"] = fail
        try:
            result = harness_server.call_tool("task_context", {"task_id": "TASK__x"})
        finally:
            harness_server.TOOLS["task_context"]["handler"] = original

        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error_code"],
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
        )
        self.assertEqual(result["structuredContent"]["path"], "services/front")
        self.assertEqual(
            result["structuredContent"]["invariant"], "admin_gitdir_backreference"
        )

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
                        "status": "closed",
                    },
                )
                self.assertNotIn("isError", add)

                task_dir = Path(tmp) / "doc/harness/tasks/TASK__login-bugs"
                task_dir.mkdir(parents=True, exist_ok=True)
                self._write_control_fixture(str(task_dir), {
                        "run_id": harness_lib.new_uuid7(),
                        "execution_mode": "standard",
                        "required_lenses": ["review-code", "qa-cli"],
                        "close_receipt_fingerprint": None,
                    })
                self._write_subagent_receipt(str(task_dir))
                self._close_fixture(str(task_dir))

                nxt = harness_server.call_tool("goal_next_task", {})
                self.assertIsNone(nxt["structuredContent"]["task"])

                finish = harness_server.call_tool("goal_finish", {"status": "complete"})
                self.assertEqual(finish["structuredContent"]["goal"]["status"], "complete")
                self.assertTrue((Path(tmp) / "doc" / "harness" / "goals" / "current.json").is_file())
            finally:
                harness_server.find_repo_root = original_find_repo_root

    def test_goal_completion_rejects_unfinished_child_and_restart_reactivates(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__goal-child"))
            self._call_in_repo(tmp, "goal_start", {
                "objective": "finish safely", "goal_id": "GOAL__finish-safely",
            })
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__goal-child", "status": "active",
            })

            blocked = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(blocked.get("isError"))
            self.assertIn("TASK__goal-child", blocked["structuredContent"]["error"])

            control = harness_server.read_task_control(str(task_dir))
            control["close_receipt_fingerprint"] = "sha256:" + "f" * 64
            self._write_control_fixture(str(task_dir), control)
            missing_receipt = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(missing_receipt.get("isError"))
            control["close_receipt_fingerprint"] = None
            self._write_control_fixture(str(task_dir), control)
            self._write_subagent_receipt(str(task_dir))
            self._close_fixture(str(task_dir))
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__goal-child", "status": "closed",
            })
            finished = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertEqual(finished["structuredContent"]["goal"]["status"], "complete")

            restarted = self._call_in_repo(tmp, "goal_start", {
                "objective": "finish safely", "goal_id": "GOAL__finish-safely",
            })
            goal = restarted["structuredContent"]["goal"]
            self.assertEqual(goal["status"], "active")
            self.assertNotIn("finished_at", goal)

    def test_goal_completion_rejects_hand_labeled_closed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(self._make_task(tmp, "TASK__closed-child"))
            self._call_in_repo(tmp, "goal_start", {"objective": "finish multiple children"})
            self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__closed-child", "status": "closed",
            })
            control = harness_server.read_task_control(str(task_dir))
            control["close_receipt_fingerprint"] = "sha256:" + "f" * 64
            self._write_control_fixture(str(task_dir), control)

            finished = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})

            self.assertTrue(finished.get("isError"))
            self.assertIn("TASK__closed-child", finished["structuredContent"]["error"])

    def test_goal_completion_requires_at_least_one_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {"objective": "empty goal"})
            result = self._call_in_repo(tmp, "goal_finish", {"status": "complete"})
            self.assertTrue(result.get("isError"))
            self.assertIn("no child tasks", result["structuredContent"]["error"])

    def test_terminal_goal_rejects_child_mutation_and_repeat_finish_until_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {
                "objective": "terminal guard", "goal_id": "GOAL__terminal-guard",
            })
            blocked = self._call_in_repo(tmp, "goal_finish", {"status": "blocked"})
            self.assertEqual(blocked["structuredContent"]["goal"]["status"], "blocked")

            add = self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__must-restart", "status": "queued",
            })
            self.assertTrue(add.get("isError"))
            self.assertIn("call goal_start explicitly", add["structuredContent"]["error"])

            finish = self._call_in_repo(tmp, "goal_finish", {"status": "blocked"})
            self.assertTrue(finish.get("isError"))
            self.assertIn("call goal_start explicitly", finish["structuredContent"]["error"])

            restarted = self._call_in_repo(tmp, "goal_start", {
                "objective": "terminal guard", "goal_id": "GOAL__terminal-guard",
            })
            self.assertEqual(restarted["structuredContent"]["goal"]["status"], "active")
            add_after_restart = self._call_in_repo(tmp, "goal_add_task", {
                "task_id": "TASK__must-restart", "status": "queued",
            })
            self.assertNotIn("isError", add_after_restart)

    def test_goal_start_rejects_prefixed_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__/../../../escaped"},
            )
            self.assertTrue(result.get("isError"))
            self.assertIn("goal_id", result["structuredContent"]["error"])
            self.assertFalse((Path(tmp) / "escaped.json").exists())

    def test_goal_start_rejects_symlinked_storage_roots(self):
        for symlink_parent in (False, True):
            with self.subTest(symlink_parent=symlink_parent), tempfile.TemporaryDirectory() as tmp:
                outside = Path(tmp) / "outside-goals"
                outside.mkdir()
                if symlink_parent:
                    (Path(tmp) / "doc").symlink_to(outside, target_is_directory=True)
                else:
                    harness_dir = Path(tmp) / "doc" / "harness"
                    harness_dir.mkdir(parents=True)
                    (harness_dir / "goals").symlink_to(outside, target_is_directory=True)
                result = self._call_in_repo(
                    tmp,
                    "goal_start",
                    {"objective": "safe objective", "goal_id": "GOAL__safe"},
                )
                self.assertTrue(result.get("isError"))
                self.assertEqual(result["structuredContent"]["field"], "goal_storage_root")
                self.assertEqual(result["structuredContent"]["rejected_value"], repr("doc/harness/goals"))
                self.assertIn("non-symlink", result["structuredContent"]["expected"])
                self.assertIn("Restore", result["structuredContent"]["next_action"])
                self.assertFalse((outside / "GOAL__safe.json").exists())
                self.assertFalse((outside / "current.json").exists())

    def test_goal_state_replaces_leaf_symlinks_without_touching_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            goals = Path(tmp) / "doc" / "harness" / "goals"
            goals.mkdir(parents=True)
            sentinel = Path(tmp) / "goal-sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            for name in ("GOAL__safe.json", "GOAL__safe.json.tmp", "current.json"):
                (goals / name).symlink_to(sentinel)
            result = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__safe"},
            )
            self.assertNotIn("isError", result)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((goals / "GOAL__safe.json").is_symlink())
            self.assertFalse((goals / "current.json").is_symlink())

    def test_goal_readers_ignore_valid_json_leaf_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            goals = Path(tmp) / "doc" / "harness" / "goals"
            goals.mkdir(parents=True)
            external = Path(tmp) / "external-goal.json"
            external.write_text(
                json.dumps({
                    "goal_id": "GOAL__safe",
                    "objective": "SENSITIVE_VALUE",
                    "status": "active",
                    "source": {"secret": "SENSITIVE_VALUE"},
                    "tasks": [{"task_id": "TASK__foreign"}],
                }),
                encoding="utf-8",
            )

            (goals / "current.json").symlink_to(external)
            context = self._call_in_repo(tmp, "goal_context", {})
            self.assertNotIn("SENSITIVE_VALUE", json.dumps(context))

            (goals / "current.json").unlink()
            (goals / "GOAL__safe.json").symlink_to(external)
            started = self._call_in_repo(
                tmp,
                "goal_start",
                {"objective": "safe objective", "goal_id": "GOAL__safe"},
            )
            self.assertNotIn("isError", started)
            self.assertNotIn("SENSITIVE_VALUE", json.dumps(started))
            self.assertFalse((goals / "GOAL__safe.json").is_symlink())

    def test_goal_add_task_rejects_outside_task_dir_before_persisting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call_in_repo(tmp, "goal_start", {"objective": "safe objective"})
            current = Path(tmp) / "doc" / "harness" / "goals" / "current.json"
            before = current.read_bytes()
            result = self._call_in_repo(
                tmp,
                "goal_add_task",
                {"task_id": "TASK__safe", "task_dir": str(Path(tmp) / "outside")},
            )
            self.assertTrue(result.get("isError"))
            self.assertEqual(current.read_bytes(), before)

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
                "close_receipt_fingerprint": None,
            }) + "\n", encoding="utf-8")
            before = state.read_bytes()

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__old-control"},
            )

            payload = result["structuredContent"]
            self.assertTrue(result.get("isError"))
            self.assertIn("unsupported task-control schema", payload["error"])
            self.assertIn("new task_id", payload["next_action"])
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
            self.assertEqual(set(structured), {"task_dir", "task_context"})
            context = structured["task_context"]
            self.assertEqual(context["task_id"], "TASK__mcp")
            self.assertNotIn("subagent_receipts", context)
            self.assertNotIn("review_receipts", context)
            self.assertNotIn("subagent_receipts", structured)
            self.assertNotIn("review_receipts", structured)
            self.assertEqual(
                context["report_path"],
                "doc/harness/tasks/TASK__mcp/RECEIPTS.jsonl",
            )

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

    def test_record_subagent_receipt_is_not_exposed_and_task_verify_is_compact(self):
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
                receipt_path = Path(task_dir) / "RECEIPTS.jsonl"
                receipt_exists = receipt_path.is_file()
                receipt = next(
                    (
                        item for item in (
                            json.loads(line)
                            for line in receipt_path.read_text(encoding="utf-8").splitlines()
                        )
                        if item.get("agent_id") == "agent-123"
                    ),
                    {},
                ) if receipt_exists else {}
            finally:
                harness_server.canonical_task_dir = original_ctd
        self.assertTrue(result.get("isError"))
        self.assertIn("Unknown tool", result["structuredContent"]["error"])
        self.assertTrue(receipt_exists)
        self.assertEqual(receipt["agent_id"], "agent-123")
        self.assertEqual(receipt["lens"], "qa-cli")
        self.assertNotIn("subagent_receipts", verify["structuredContent"])
        self.assertNotIn("review_receipts", verify["structuredContent"])
        self.assertNotIn("review_report_path", verify["structuredContent"])
        self.assertEqual(set(verify["structuredContent"]), {
            "task_dir", "runtime_verdict", "next_action", "missing_for_close",
            "report_path", "review_verdict", "required_review_lenses",
            "required_qa_lenses",
        })
        self.assertEqual(verify["structuredContent"]["required_qa_lenses"], ["qa-cli"])
        self.assertEqual(
            verify["structuredContent"]["report_path"],
            "doc/harness/tasks/TASK__subagentreceipt/RECEIPTS.jsonl",
        )

    def test_micro_execution_mode_allows_no_plan_but_still_requires_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "doc" / "harness" / "tasks" / "TASK__micro"
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
            self.assertEqual(
                harness_server.task_control_status(
                    task_dir, harness_server.read_task_control(task_dir)
                ), "blocked",
            )

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
                result = harness_server.call_tool("task_blocked", {
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
            self.assertIn("task_start", result["structuredContent"].get("next_action", ""))
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
            self.assertEqual(context["status"], "open")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertFalse(result["structuredContent"]["task_created"])
            self.assertTrue(result["structuredContent"]["resumed"])
            self.assertFalse((Path(task_dir) / "BLOCKED.md").exists())
            self.assertEqual(
                harness_server.task_control_status(
                    task_dir, harness_server.read_task_control(task_dir)
                ), "open",
            )

    def test_task_start_reopens_closed_task_and_clears_close_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-closed")
            self._close_fixture(task_dir)
            original_ctd = harness_server.canonical_task_dir
            original_root = harness_server.find_repo_root
            harness_server.canonical_task_dir = lambda task_id=None, slug=None, repo_root=None, **kw: task_dir
            harness_server.find_repo_root = lambda *a, **kw: tmp
            try:
                result = harness_server.call_tool(
                    "task_start", {"task_id": "TASK__resume-closed"}
                )
            finally:
                harness_server.canonical_task_dir = original_ctd
                harness_server.find_repo_root = original_root

            self.assertNotIn("isError", result)
            context = result["structuredContent"]["task_context"]
            self.assertEqual(context["status"], "open")
            self.assertEqual(context["runtime_verdict"], "PENDING")
            self.assertIsNone(harness_server.read_task_control(task_dir)["close_receipt_fingerprint"])

    def test_task_start_open_resume_rotates_generation_and_discards_old_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-open")
            old_run_id = harness_server.read_task_control(task_dir)["run_id"]
            self._write_subagent_receipt(task_dir)
            self.assertEqual(
                harness_server.receipt_runtime_verdict(
                    task_dir, harness_server.read_task_control(task_dir)
                ),
                "PASS",
            )

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__resume-open"},
            )

            payload = result["structuredContent"]
            resumed = harness_server.read_task_control(task_dir)
            self.assertNotIn("isError", result)
            self.assertTrue(payload["resumed"])
            self.assertNotEqual(payload["run_id"], old_run_id)
            self.assertEqual(payload["run_id"], resumed["run_id"])
            self.assertEqual(payload["task_context"]["runtime_verdict"], "PENDING")
            self.assertFalse((Path(task_dir) / "RECEIPTS.jsonl").exists())

    def test_task_start_resume_discards_unsupported_legacy_receipt_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__resume-legacy-receipts")
            old_run_id = harness_server.read_task_control(task_dir)["run_id"]
            legacy = {
                "receipt_id": "legacy-receipt", "ts": "2026-08-12T00:00:00Z",
                "event": "completed", "source": "legacy", "task_run_id": old_run_id,
                "agent_id": "legacy-agent", "agent_type": "harness:qa-cli",
                "lens": "qa-cli", "verdict": "PASS", "summary": "VERDICT: PASS",
                "transcript_path": "/tmp/legacy", "transcript_sha256": "0" * 64,
                "runtime_session_id": "legacy-session",
                "runtime_thread_id": "legacy-thread", "runtime_event_id": "legacy-event",
            }
            receipts = Path(task_dir) / "RECEIPTS.jsonl"
            receipts.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

            result = self._call_in_repo(
                tmp, "task_start", {"task_id": "TASK__resume-legacy-receipts"},
            )

            self.assertNotIn("isError", result)
            self.assertTrue(result["structuredContent"]["resumed"])
            self.assertNotEqual(
                harness_server.read_task_control(task_dir)["run_id"], old_run_id,
            )
            self.assertFalse(receipts.exists())

    def test_verify_and_close_reject_duplicate_receipt_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_id = "TASK__duplicate-receipt-keys"
            task_dir = self._make_task(tmp, task_id)
            run_id = harness_server.read_task_control(task_dir)["run_id"]
            (Path(task_dir) / "RECEIPTS.jsonl").write_text(
                '{"ts":"2026-08-12T00:00:00Z","event":"started",'
                '"event":"completed","source":"test_fixture",'
                f'"task_run_id":"{run_id}","runtime_id":"test:dup",'
                '"agent_id":"dup","agent_type":"qa-cli","lens":"qa-cli",'
                '"verdict":"PASS","summary":"VERDICT: PASS\\nDETAIL_SHA256:'
                + "0" * 64 + '"}\n',
                encoding="utf-8",
            )

            for tool in ("task_verify", "task_close"):
                with self.subTest(tool=tool):
                    result = self._call_in_repo(tmp, tool, {"task_id": task_id})
                    self.assertTrue(result["isError"])
                    self.assertIn(
                        "receipt storage integrity unavailable",
                        result["content"][0]["text"],
                    )

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

            with harness_server.receipt_stream_transaction(task_dir):
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
            self.assertNotEqual(
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
            self.assertIsInstance(payload["task_context"], dict)
            self.assertFalse(payload["task_context"]["context_complete"])
            self.assertFalse(payload["task_context"]["source_write_allowed"])
            self.assertIn("Do not call task_start again", payload["next_action"])
            self.assertEqual(payload["warnings"][0]["code"], "TASK_CONTEXT_DEFERRED")
            self.assertFalse((task_dir / "TASK_BASELINE.json").exists())
            self.assertTrue((task_dir / "TASK.json").is_file())

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

    def test_resumed_task_context_warning_reports_ready_without_new_creation(self):
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
                "close_receipt_fingerprint": None,
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
                result = harness_server.handle_task_start(
                    {"task_id": "TASK__resumed-warning"}
                )

            payload = result["structuredContent"]
            self.assertEqual(payload["start_status"], "ready_with_warnings")
            self.assertFalse(payload["task_created"])
            self.assertTrue(payload["resumed"])
            self.assertEqual(
                payload["warnings"][0]["message"],
                "Task ready; full routing context was deferred to keep task_start responsive.",
            )

    def test_failed_existing_resume_restores_state_and_receipts(self):
        for terminal_status in ("open", "closed", "blocked"):
            with self.subTest(status=terminal_status), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp) / f"doc/harness/tasks/TASK__resume-{terminal_status}"
                task_dir.mkdir(parents=True)
                self._write_control_fixture(str(task_dir), {
                    "run_id": harness_lib.new_uuid7(),
                    "execution_mode": "standard",
                    "required_lenses": ["review-code", "qa-cli"],
                    "close_receipt_fingerprint": None,
                })
                if terminal_status in {"open", "closed"}:
                    self._write_subagent_receipt(str(task_dir))
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
                                {"task_id": f"TASK__resume-{terminal_status}"}
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
                "close_receipt_fingerprint": None,
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
                "close_receipt_fingerprint": None,
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
                with self.assertRaisesRegex(OSError, "resume marker interrupted"):
                    harness_server.handle_task_start(
                        {"task_id": "TASK__active-resume"}
                    )
                os.chdir(prior_cwd)
            self.assertEqual(
                Path(harness_server.resolve_active_task_dir(tmp)).resolve(),
                task_dir.resolve(),
            )

    def test_failed_new_start_restores_different_preexisting_marker(self):
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
                "close_receipt_fingerprint": None,
            })
            self._write_marker_fixture(tmp, str(task_a))
            real_replace = harness_lib.os.replace

            def fail_task_b_marker(src, dst, *args, **kwargs):
                if str(dst).endswith(".json") and ".active_sessions" in str(dst):
                    raise OSError("task B marker interrupted")
                return real_replace(src, dst, *args, **kwargs)

            prior_cwd = os.getcwd()
            with mock.patch.object(harness_lib.os, "replace", side_effect=fail_task_b_marker):
                os.chdir(tmp)
                with self.assertRaisesRegex(OSError, "task B marker interrupted"):
                    harness_server.handle_task_start({"task_id": "TASK__failed-b"})
                os.chdir(prior_cwd)
            self.assertEqual(
                Path(harness_server.resolve_active_task_dir(tmp)).resolve(),
                task_a.resolve(),
            )
            self.assertFalse((task_b / "TASK_STATE.yaml").exists())

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

    def test_goal_state_two_file_write_rolls_back_on_second_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            goal = harness_server.start_harness_goal(tmp, "transactional goal")
            goal_id = goal["goal_id"]
            goal_path = Path(tmp) / f"doc/harness/goals/{goal_id}.json"
            current_path = Path(tmp) / "doc/harness/goals/current.json"
            before_goal = goal_path.read_text(encoding="utf-8")
            before_current = current_path.read_text(encoding="utf-8")
            task_dir = Path(tmp) / "doc/harness/tasks/TASK__goal-child"
            task_dir.mkdir(parents=True)
            goal_globals = harness_server.add_goal_task.__globals__
            original_write = goal_globals["_atomic_text_write"]
            failed = False

            def fail_current_once(path, text):
                nonlocal failed
                if Path(path) == current_path and not failed:
                    failed = True
                    raise OSError("current goal publication unavailable")
                return original_write(path, text)

            with mock.patch.dict(
                goal_globals, {"_atomic_text_write": fail_current_once}
            ):
                with self.assertRaisesRegex(OSError, "current goal publication"):
                    harness_server.add_goal_task(
                        tmp, "TASK__goal-child", task_dir=str(task_dir), status="closed"
                    )
            self.assertEqual(goal_path.read_text(encoding="utf-8"), before_goal)
            self.assertEqual(current_path.read_text(encoding="utf-8"), before_current)

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
            self._write_subagent_receipt(task_dir)
            closed = self._call_in_repo(
                tmp, "task_close", {"task_id": "TASK__terminal-mutators"},
            )
            self.assertNotIn("isError", closed)
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

    def test_plan_and_blocked_mutators_use_receipt_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._make_task(tmp, "TASK__serialized-mutators")
            original_transaction = harness_server.receipt_stream_transaction
            entered = []

            from contextlib import contextmanager

            @contextmanager
            def observed_transaction(path):
                entered.append(Path(path).name)
                with original_transaction(path):
                    yield

            with mock.patch.object(
                harness_server, "receipt_stream_transaction",
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

class HarnessMcpServerPR2CloseGate(unittest.TestCase):
    """Receipt and runtime-stale gates in task_close / task_verify."""

    def setUp(self):
        self._receipt_auth = mock.patch.object(
            harness_server, "record_subagent_receipt", side_effect=_record_receipt_fixture,
        )
        self._receipt_auth.start()

    def tearDown(self):
        self._receipt_auth.stop()

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
        (task_dir / "TASK.json").write_text(
            json.dumps({
                "run_id": harness_lib.new_uuid7(),
                "execution_mode": "standard",
                "required_lenses": ["review-code", "qa-cli"],
                "close_receipt_fingerprint": None,
            }, indent=2, sort_keys=True) + "\n", encoding="utf-8",
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
        if write_receipt:
            review_types = {
                "review-code": "harness:code-reviewer",
                "review-security": "harness:security-reviewer",
            }
            for lens in harness_server.required_review_lenses(task_dir):
                for status, verdict in (("started", ""), ("completed", "PASS")):
                    harness_server.record_subagent_receipt(task_dir, {
                        "source": "claude_hook",
                        "runtime_id": f"claude:test-session:{lens}-{task_id}",
                        "event": status,
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
                    "source": "claude_hook",
                    "runtime_id": f"claude:test-session:agent-{task_id}",
                    "event": status,
                    "agent_id": f"agent-{task_id}",
                    "agent_type": "harness:qa-cli",
                    "verdict": verdict,
                    "summary": f"VERDICT: {verdict}" if verdict else "qa started",
                })
        if checks_yaml is not None:
            (task_dir / "CHECKS.yaml").write_text(checks_yaml, encoding="utf-8")
        return str(task_dir)

    def _patch(self, task_dir: str):
        """Patch canonical_task_dir to isolate the lifecycle fixture."""
        self._orig_ctd = harness_server.canonical_task_dir
        harness_server.canonical_task_dir = lambda task_id=None, **kw: task_dir

    def _unpatch(self):
        harness_server.canonical_task_dir = self._orig_ctd

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

    def test_close_ignores_legacy_checks_artifact(self):
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
            future = os.path.getmtime(Path(td) / "RECEIPTS.jsonl") + 10
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

    def test_close_passes_without_legacy_checks_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(tmp, "TASK__pr2-003", checks_yaml=None)
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-003"})
            finally:
                self._unpatch()
        self.assertNotIn("isError", result)
        self.assertTrue(result["structuredContent"]["closed"])

    # ---- AC-006: task_verify derives PASS from subagent receipt ----
    def test_verify_reports_receipt_pass_without_legacy_state_outputs(self):
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
        s = result["structuredContent"]
        self.assertEqual(s["runtime_verdict"], "PASS")
        self.assertNotIn("stale", s)
        self.assertNotIn("stale_path", s)
        self.assertNotIn("touched_paths", s)

    def test_close_marks_active_goal_child_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp,
                "TASK__goal-close-sync",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            repo = Path(tmp)
            canonical = repo / "doc/harness/tasks/TASK__goal-close-sync"
            canonical.parent.mkdir(parents=True)
            Path(td).rename(canonical)
            td = str(canonical)
            harness_server.start_harness_goal(
                tmp, "close child", goal_id="GOAL__close-child",
            )
            harness_server.add_goal_task(
                tmp, "TASK__goal-close-sync", status="active",
            )
            clean = {"missing_for_close": [], "next_action": "close"}
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                mock.patch.object(harness_server, "emit_compact_context", return_value=clean),
            ):
                result = harness_server.handle_task_close({"task_id": "TASK__goal-close-sync"})

            self.assertNotIn("isError", result)
            current = json.loads(
                (repo / "doc/harness/goals/current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(current["tasks"][0]["status"], "closed")
            self.assertEqual(
                harness_server.task_control_status(
                    str(canonical), harness_server.read_task_control(str(canonical))
                ), "closed",
            )
            self.assertFalse((canonical / "TASK_CLOSE_RECEIPT.json").exists())

    def test_goal_completion_preserves_closed_child_after_later_child_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks_root = repo / "doc/harness/tasks"
            tasks_root.mkdir(parents=True)
            task_dirs = {}
            for task_id in ("TASK__first-child", "TASK__second-child"):
                prepared = Path(self._prepare_task(
                    tmp,
                    task_id,
                    checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                ))
                canonical = tasks_root / task_id
                prepared.rename(canonical)
                task_dirs[task_id] = str(canonical)

            harness_server.start_harness_goal(tmp, "two children", goal_id="GOAL__two-children")
            for task_id in task_dirs:
                harness_server.add_goal_task(tmp, task_id, status="active")

            clean = {"missing_for_close": [], "next_action": "close"}
            for index, (task_id, task_dir) in enumerate(task_dirs.items()):
                with (
                    mock.patch.object(harness_server, "canonical_task_dir", return_value=task_dir),
                    mock.patch.object(harness_server, "find_repo_root", return_value=tmp),
                    mock.patch.object(harness_server, "emit_compact_context", return_value=clean),
                ):
                    closed = harness_server.handle_task_close({"task_id": task_id})
                self.assertNotIn("isError", closed)
                if index == 0:
                    (repo / "later-child.py").write_text("changed later\n", encoding="utf-8")

            finished = harness_server.finish_harness_goal(tmp, status="complete")
            self.assertEqual(finished["status"], "complete")

    def test_close_rolls_back_when_control_publication_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-attestation-failure",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            before = harness_server.read_task_control(td)
            self._patch(td)
            try:
                with mock.patch.object(
                    harness_server, "publish_task_close",
                    side_effect=OSError("control publication unavailable"),
                ):
                    result = harness_server.call_tool(
                        "task_close",
                        {"task_id": "TASK__close-attestation-failure"},
                    )
            finally:
                self._unpatch()

            self.assertTrue(result.get("isError"))
            restored = harness_server.read_task_control(td)
            self.assertEqual(restored, before)
            self.assertFalse(Path(td, "TASK_CLOSE_RECEIPT.json").exists())

    def test_close_rolls_back_on_marker_cleanup_io_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__close-marker-io",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
            )
            before = harness_server.read_task_control(td)
            _write_marker_fixture(tmp, td)
            self._patch(td)
            try:
                with mock.patch.object(
                    harness_server,
                    "clear_active_marker",
                    side_effect=OSError("marker cleanup unavailable"),
                ):
                    result = harness_server.call_tool(
                        "task_close", {"task_id": "TASK__close-marker-io"}
                    )
            finally:
                self._unpatch()
            self.assertTrue(result.get("isError"))
            restored = harness_server.read_task_control(td)
            self.assertEqual(restored, before)
            self.assertFalse(Path(td, "TASK_CLOSE_RECEIPT.json").exists())
            legacy_marker = Path(tmp, "doc/harness/tasks/.active")
            self.assertEqual(
                Path(legacy_marker.read_text(encoding="utf-8").strip()).resolve(),
                Path(td).resolve(),
            )

    def test_close_rolls_back_goal_sync_and_marker_cleanup_failures(self):
        for failure_kind in ("goal", "marker"):
            with self.subTest(failure=failure_kind), tempfile.TemporaryDirectory() as tmp:
                td = self._prepare_task(
                    tmp, f"TASK__close-{failure_kind}-failure",
                    checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n',
                )
                before = harness_server.read_task_control(td)
                _write_marker_fixture(tmp, td)
                self._patch(td)
                try:
                    patches = [
                        mock.patch.object(
                            harness_server,
                            "read_current_goal",
                            return_value={
                                "status": "active",
                                "tasks": [{"task_id": os.path.basename(td)}],
                            },
                        )
                    ]
                    if failure_kind == "goal":
                        patches.append(mock.patch.object(
                            harness_server, "add_goal_task",
                            side_effect=OSError("goal sync unavailable"),
                        ))
                    else:
                        patches.append(mock.patch.object(
                            harness_server, "clear_active_marker", return_value=None,
                        ))
                    with patches[0], patches[1]:
                        result = harness_server.call_tool(
                            "task_close",
                            {"task_id": f"TASK__close-{failure_kind}-failure"},
                        )
                finally:
                    self._unpatch()
                self.assertTrue(result.get("isError"))
                restored = harness_server.read_task_control(td)
                self.assertEqual(restored, before)
                self.assertFalse(Path(td, "TASK_CLOSE_RECEIPT.json").exists())
                legacy_marker = Path(tmp, "doc/harness/tasks/.active")
                self.assertEqual(
                    Path(legacy_marker.read_text(encoding="utf-8").strip()).resolve(),
                    Path(td).resolve(),
                )

    def test_close_evaluates_each_success_gate_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__single-pass-close",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
            )
            clean = {
                "missing_for_close": [],
                "next_action": "close",
            }
            with (
                mock.patch.object(harness_server, "canonical_task_dir", return_value=td),
                mock.patch.object(harness_server, "emit_compact_context", return_value=clean) as context,
                mock.patch.object(
                    harness_server,
                    "receipt_stream_fingerprint",
                    return_value="sha256:" + "b" * 64,
                ) as receipts,
            ):
                result = harness_server.handle_task_close(
                    {"task_id": "TASK__single-pass-close"}
                )

            self.assertNotIn("isError", result)
            for gate in (context, receipts):
                self.assertEqual(gate.call_count, 1)

if __name__ == "__main__":
    unittest.main()
