"""Receipt watcher readiness must surface before verification is paid for.

Covers doc/common/REQ__process__receipt-watcher-fail-closed.md requirements
1, 2, 3, 5, and 6, plus the hard non-goal that nothing here can author a receipt.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from conftest import REPO_ROOT, SCRIPTS_DIR  # type: ignore

HOOK = os.path.join(SCRIPTS_DIR, "hook_pre_tool_use.py")


def _server():
    """Import the MCP server lazily.

    It lives in plugin/mcp/, not plugin/scripts/, so a module-level import here
    would trip the tests/ top-level third-party import guard.
    """
    sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "mcp"))
    sys.path.insert(0, SCRIPTS_DIR)
    import harness_server  # type: ignore

    return harness_server


class TestWatcherErrorIsRecorded(unittest.TestCase):
    """Requirement 2 — the cause must not be swallowed."""

    def test_failing_watcher_start_records_error_and_does_not_raise(self):
        harness_server = _server()
        class Boom:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("rollout directory missing")

        original_server = harness_server._SERVER
        server = harness_server.McpServer()
        recorded = {}
        original_manager = harness_server._WatcherManager
        original_record = harness_server._record_watcher_error
        harness_server._WatcherManager = Boom
        harness_server._record_watcher_error = lambda msg, root="": recorded.update(msg=msg)
        try:
            server._start_codex_watchers()  # must not raise
        finally:
            harness_server._WatcherManager = original_manager
            harness_server._record_watcher_error = original_record
            # McpServer.__init__ rebound the module global; do not leave this
            # test's failed server visible to the rest of the suite.
            harness_server._SERVER = original_server

        self.assertIsNone(server.watcher_manager)
        self.assertIn("rollout directory missing", server.last_watcher_error)
        self.assertIn("RuntimeError", server.last_watcher_error)
        self.assertIn("rollout directory missing", recorded.get("msg", ""))


class TestWatcherStatusShape(unittest.TestCase):
    """Requirement 2 — readiness is queryable, and unknown stays unknown."""

    def test_undeterminable_fields_are_null_not_guessed(self):
        harness_server = _server()
        status = harness_server._watcher_status(task_dir="", task_id="", run_id="")
        self.assertIsNone(status["receipts_writable"])
        self.assertIsNone(status["active_task_id"])
        self.assertIsNone(status["active_run_id"])
        for key in (
            "receipt_capability_warning", "receipts_recordable", "manager_running",
            "registration_present", "root_thread_id", "rollout_offset",
            "last_registration_error", "last_watcher_error",
        ):
            self.assertIn(key, status)

    def test_writable_task_dir_reports_true(self):
        harness_server = _server()
        with tempfile.TemporaryDirectory() as tmp:
            status = harness_server._watcher_status(task_dir=tmp, task_id="TASK__x")
            self.assertTrue(status["receipts_writable"])
            self.assertEqual(status["active_task_id"], "TASK__x")


class TestNextActionGate(unittest.TestCase):
    """Requirements 1 and 3 — never instruct a spawn that cannot be attested."""

    def test_spawn_instruction_replaced_when_receipts_unrecordable(self):
        harness_server = _server()
        ctx = {"next_action": "Run and await the required read-only review subagent(s)."}
        gated = harness_server._gate_next_action(ctx, {"receipts_recordable": False})
        self.assertNotEqual(gated["next_action"], ctx["next_action"])
        # The replacement may name subagents — it must tell the caller not to
        # spawn them, and must not read as an instruction to run one.
        self.assertNotIn("Run and await", gated["next_action"])
        self.assertIn("Do not spawn", gated["next_action"])
        self.assertIn("Planning and implementation still work", gated["next_action"])

    def test_instruction_preserved_when_receipts_recordable(self):
        harness_server = _server()
        ctx = {"next_action": "Run and await the required read-only review subagent(s)."}
        gated = harness_server._gate_next_action(ctx, {"receipts_recordable": True})
        self.assertEqual(gated["next_action"], ctx["next_action"])

    def test_non_spawn_instruction_is_left_alone(self):
        harness_server = _server()
        ctx = {"next_action": "Create PLAN.md via plan skill before source writes."}
        gated = harness_server._gate_next_action(ctx, {"receipts_recordable": False})
        self.assertEqual(gated["next_action"], ctx["next_action"])


class TestCodexRegistrationFailureIsDetected(unittest.TestCase):
    """The incident this REQ exists for happened on Codex, not Claude.

    `receipt_capability_warning()` reads ~/.claude/plugins/installed_plugins.json,
    so on a Codex session it is silent even when the Codex watcher never
    registered. Readiness must therefore also consider the Codex-side signals,
    or three review agents and a full QA suite complete with no receipts.
    """

    def _status_with(self, diagnostics):
        harness_server = _server()
        original_read = harness_server._read_watcher_diagnostics
        original_warning = harness_server.receipt_capability_warning
        # _SERVER is a module global that any McpServer() construction in this
        # suite rebinds, so isolate it or a sibling test's recorded error leaks
        # in and this assertion stops testing what it claims to.
        original_server = harness_server._SERVER
        harness_server._read_watcher_diagnostics = lambda root="": diagnostics
        harness_server.receipt_capability_warning = lambda *_a, **_kw: ""
        harness_server._SERVER = None
        try:
            return harness_server._watcher_status(task_dir="", task_id="TASK__x")
        finally:
            harness_server._read_watcher_diagnostics = original_read
            harness_server.receipt_capability_warning = original_warning
            harness_server._SERVER = original_server

    def test_unregistered_codex_watcher_is_not_recordable(self):
        status = self._status_with({
            "registration_present": False,
            "last_registration_error": "watcher registration did not complete",
        })
        self.assertFalse(
            status["receipts_recordable"],
            "a Codex session with no watcher registration was reported ready",
        )
        self.assertIn("not registered", status["receipts_unrecordable_reason"])
        self.assertIn(
            "watcher registration did not complete",
            status["receipts_unrecordable_reason"],
        )

    def test_watcher_start_failure_is_not_recordable(self):
        status = self._status_with({"last_watcher_error": "RuntimeError: rollout missing"})
        self.assertFalse(status["receipts_recordable"])
        self.assertIn("rollout missing", status["receipts_unrecordable_reason"])

    def test_registered_codex_watcher_stays_recordable(self):
        status = self._status_with({
            "registration_present": True, "last_registration_error": "",
        })
        self.assertTrue(status["receipts_recordable"])
        self.assertEqual(status["receipts_unrecordable_reason"], "")

    def test_gate_names_the_codex_cause_not_the_claude_one(self):
        harness_server = _server()
        ctx = {"next_action": "Run and await the required read-only review subagent(s)."}
        gated = harness_server._gate_next_action(ctx, {
            "receipts_recordable": False,
            "receipts_unrecordable_reason": "The receipt watcher is not registered for this session.",
        })
        self.assertIn("not registered for this session", gated["next_action"])
        self.assertNotIn("/plugin update", gated["next_action"])


class TestRegistrationFailurePropagates(unittest.TestCase):
    """Requirement 5 — best-effort registration, mandatory failure reporting."""

    def _spawn_payload(self, cwd):
        return json.dumps({
            "tool_name": "collaboration.spawn_agent", "cwd": cwd,
        }).encode()

    def _run_hook(self, cwd):
        return subprocess.run(
            [sys.executable, HOOK], input=self._spawn_payload(cwd),
            capture_output=True, cwd=cwd,
        )

    def test_failed_registration_is_recorded_and_hook_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "doc" / "harness").mkdir(parents=True)
            proc = self._run_hook(tmp)

            # C-12: a hook must fail safe.
            self.assertEqual(proc.returncode, 0)

            diagnostics = Path(tmp) / "doc" / "harness" / ".watcher-diagnostics.json"
            self.assertTrue(diagnostics.exists(), "registration result was discarded")
            data = json.loads(diagnostics.read_text(encoding="utf-8"))
            self.assertFalse(data["registration_present"])
            self.assertTrue(data["last_registration_error"])
            self.assertIn("registration failed", proc.stderr.decode())

    def test_registration_failure_never_blocks_the_spawn(self):
        """Settled decision 2026-08-26: a watcher error must not stop an agent.

        Attestation is a recording concern. Losing the ability to record is not
        a reason to stop the work from running — "result but no receipt" beats
        "neither". This test exists so that never silently becomes a block.
        """
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "doc" / "harness").mkdir(parents=True)
            proc = self._run_hook(tmp)

            self.assertEqual(proc.returncode, 0)
            stdout = proc.stdout.decode().strip()
            if stdout:
                # Any emitted decision must not deny the spawn.
                self.assertNotIn("deny", stdout.lower())
                self.assertNotIn("permissionDecision", stdout)

    def test_hook_writes_no_receipt(self):
        """Hard non-goal: nothing in this path may author attestation."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "doc" / "harness").mkdir(parents=True)
            self._run_hook(tmp)
            produced = [p.name for p in Path(tmp).rglob("*")]
            self.assertNotIn("RECEIPTS.jsonl", produced)


class TestNoReceiptSynthesis(unittest.TestCase):
    """Requirement 7 — no code path may synthesize attestation."""

    def test_changed_files_do_not_write_receipts(self):
        for rel in ("plugin/scripts/hook_pre_tool_use.py",):
            source = Path(REPO_ROOT, rel).read_text(encoding="utf-8")
            with self.subTest(path=rel):
                self.assertNotIn("record_subagent_receipt", source)
                self.assertNotIn('"RECEIPTS.jsonl"', source)


if __name__ == "__main__":
    unittest.main()
