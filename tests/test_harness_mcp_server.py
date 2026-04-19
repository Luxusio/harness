"""Tests for the plugin-local harness MCP server (7-tool minimal surface)."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

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
    "write_critic_runtime",
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

    def test_tool_registry_matches_seven_tool_surface(self):
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
            (task_dir / "CRITIC__runtime.md").write_text("# critic\nverdict: PASS\n", encoding="utf-8")
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

    # ---- AC-004: stale CRITIC__runtime refuses close ----
    def test_close_rejects_stale_verdict(self):
        import os as _os
        with tempfile.TemporaryDirectory() as tmp:
            td = self._prepare_task(
                tmp, "TASK__pr2-004",
                checks_yaml='- id: AC-001\n  title: "x"\n  status: passed\n  kind: functional\n',
                touched_paths=["plugin/scripts/health.py"],
            )
            # Make CRITIC older than touched path
            critic = _os.path.join(td, "CRITIC__runtime.md")
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
            _os.utime(_os.path.join(td, "CRITIC__runtime.md"), (100, 100))
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
            _os.utime(_os.path.join(td, "CRITIC__runtime.md"), (100, 100))
            self._patch(td)
            try:
                result = harness_server.call_tool("task_close", {"task_id": "TASK__pr2-006b"})
            finally:
                self._unpatch()
        # pyc skip path — should close cleanly (not stale)
        self.assertNotIn("isError", result,
                         f"__pycache__ pyc path should be skipped, not treated as stale: {result}")


if __name__ == "__main__":
    unittest.main()
