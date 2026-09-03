"""AC-001: _lib.py gate helpers — emit_permission_decision / _log_gate_error /
_escape_hint / read_hook_input / log_gate_bypass.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS)

import _lib  # noqa: E402


def _mark_harness_enabled(path: str) -> None:
    manifest = os.path.join(path, "doc", "harness", "manifest.yaml")
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("type: test\n")


class TestEmitPermissionDecision(unittest.TestCase):
    def test_deny_emits_json_envelope(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _lib.emit_permission_decision("deny", "reason text")
        data = json.loads(buf.getvalue())
        hso = data["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertEqual(hso["permissionDecisionReason"], "reason text")

    def test_allow_is_silent(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _lib.emit_permission_decision("allow", "whatever")
        self.assertEqual(buf.getvalue(), "")

    def test_unknown_decision_is_silent(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _lib.emit_permission_decision("maybe", "ignored")
        self.assertEqual(buf.getvalue(), "")

    def test_long_reason_truncated(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _lib.emit_permission_decision("deny", "x" * 5000)
        data = json.loads(buf.getvalue())
        self.assertLessEqual(len(data["hookSpecificOutput"]["permissionDecisionReason"]), 2000)


class TestEscapeHint(unittest.TestCase):
    def test_prewrite(self):
        self.assertEqual(_lib._escape_hint("prewrite"),
                         "escape: HARNESS_SKIP_PREWRITE=1 <retry>")

    def test_unknown_fallback(self):
        # Still renders something grep-stable
        hint = _lib._escape_hint("weird-gate")
        self.assertIn("HARNESS_SKIP_WEIRD_GATE", hint)


class TestReadHookInput(unittest.TestCase):
    def _run_subprocess(self, stdin_text):
        import subprocess
        return subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s'); "
             "from _lib import read_hook_input; "
             "import json; print(json.dumps(read_hook_input()))" % SCRIPTS],
            input=stdin_text, capture_output=True, text=True, timeout=5,
        )

    def test_empty_stdin(self):
        r = self._run_subprocess("")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_malformed_json(self):
        r = self._run_subprocess("not json at all {{{")
        self.assertEqual(r.stdout.strip(), "{}")

    def test_valid_payload(self):
        r = self._run_subprocess('{"tool_name":"Bash","tool_input":{"command":"ls"}}')
        data = json.loads(r.stdout)
        self.assertEqual(data["tool_name"], "Bash")
        self.assertEqual(data["tool_input"]["command"], "ls")

    def test_non_dict_payload_returns_empty(self):
        r = self._run_subprocess('["list","not","dict"]')
        self.assertEqual(r.stdout.strip(), "{}")

    def test_find_repo_root_prefers_hook_payload_cwd(self):
        script = (
            "import io, json, os, sys, tempfile; "
            f"sys.path.insert(0, r'{SCRIPTS}'); "
            "import _lib; "
            "repo=tempfile.mkdtemp(); "
            "plugin=tempfile.mkdtemp(); "
            "os.makedirs(os.path.join(repo, '.git')); "
            "os.chdir(plugin); "
            "sys.stdin=io.StringIO(json.dumps({'cwd': repo})); "
            "_lib.read_hook_input(); "
            "print(json.dumps({'repo': repo, 'root': _lib.find_repo_root()}))"
        )
        import subprocess
        r = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["root"], data["repo"])

    def test_is_harness_enabled_requires_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))
            self.assertFalse(_lib.is_harness_enabled_repo(td))
            _mark_harness_enabled(td)
            self.assertTrue(_lib.is_harness_enabled_repo(td))


class TestLogGateError(unittest.TestCase):
    def test_writes_gate_error_line(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))  # so find_repo_root(td) returns td
            _mark_harness_enabled(td)
            cwd = os.getcwd()
            try:
                os.chdir(td)
                try:
                    raise ValueError("boom")
                except ValueError as exc:
                    _lib._log_gate_error(exc, "test_gate")
                learn = os.path.join(td, "doc", "harness", "learnings.jsonl")
                self.assertTrue(os.path.isfile(learn))
                with open(learn) as f:
                    line = f.readlines()[-1]
                entry = json.loads(line)
                self.assertEqual(entry["type"], "gate-error")
                self.assertEqual(entry["source"], "test_gate")
                self.assertIn("ValueError", entry["error"])
                self.assertIn("boom", entry["error"])
            finally:
                os.chdir(cwd)

    def test_no_gate_error_line_outside_harness_project(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))
            cwd = os.getcwd()
            try:
                os.chdir(td)
                try:
                    raise ValueError("boom")
                except ValueError as exc:
                    _lib._log_gate_error(exc, "test_gate")
                self.assertFalse(os.path.exists(os.path.join(td, "doc", "harness")))
            finally:
                os.chdir(cwd)

    def test_silent_on_write_failure(self):
        # Caller passes a faked exception; ensure no propagation when log dir
        # creation fails. Simulate via non-writable root.
        cwd = os.getcwd()
        try:
            os.chdir("/")
            # Should not raise regardless of OS write permissions
            try:
                raise RuntimeError("x")
            except RuntimeError as exc:
                _lib._log_gate_error(exc, "x")
        finally:
            os.chdir(cwd)


class TestLogGateBypass(unittest.TestCase):
    def test_writes_bypass_line(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))
            _mark_harness_enabled(td)
            cwd = os.getcwd()
            try:
                os.chdir(td)
                _lib.log_gate_bypass("prewrite", "src/x.py")
                learn = os.path.join(td, "doc", "harness", "learnings.jsonl")
                with open(learn) as f:
                    entry = json.loads(f.readlines()[-1])
                self.assertEqual(entry["type"], "gate-bypass")
                self.assertEqual(entry["source"], "prewrite")
                self.assertEqual(entry["path"], "src/x.py")
            finally:
                os.chdir(cwd)

    def test_no_bypass_line_outside_harness_project(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))
            cwd = os.getcwd()
            try:
                os.chdir(td)
                _lib.log_gate_bypass("prewrite", "src/x.py")
                self.assertFalse(os.path.exists(os.path.join(td, "doc", "harness")))
            finally:
                os.chdir(cwd)

class TrustBoundaryReachesEveryPendingNextAction(unittest.TestCase):
    """`emit_compact_context` must state the C-14 boundary wherever a lens is
    still pending.

    Callers read `task_verify`'s `next_action` directly, so the boundary has to
    be in that string on its own account. Asserting it only through the stop
    gate hides a real loss: the gate appends its own copy when the next_action
    lacks one, so dropping the boundary here leaves the gate's message intact
    while the MCP response silently loses it. That compensation is why the
    2026-09-03 defect — a QA-pending branch that stated the boundary partially —
    survived a suite that already exercised the gate.
    """

    def _context(self, *, review_done: bool):
        """Build a real task dir; receipts come from the proven test helper.

        Hand-rolled receipt rows do not satisfy `_receipt_entry_semantics_valid`,
        and a fixture that silently fails validation would test the error path
        instead of the branch.
        """
        from test_stop_gate import _write_completed_lenses  # local: shared fixture

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "doc", "harness", "tasks"))
            task_id = "TASK__pending"
            task_dir = os.path.join(tmp, "doc", "harness", "tasks", task_id)
            if review_done:
                _write_completed_lenses(tmp, task_id, [("review-code", "PASS")])
            else:
                os.makedirs(task_dir)
                with open(os.path.join(task_dir, "TASK.json"), "w", encoding="utf-8") as handle:
                    json.dump({
                        "run_id": _lib.new_uuid7(),
                        "execution_mode": "standard",
                        "required_lenses": ["review-code", "qa-cli"],
                        "close_receipt_fingerprint": None,
                    }, handle)
                with open(os.path.join(task_dir, "PLAN.md"), "w", encoding="utf-8") as handle:
                    handle.write("# Plan\n")
            return _lib.emit_compact_context(task_dir) or {}

    def test_review_pending_next_action_states_the_boundary(self):
        action = self._context(review_done=False).get("next_action", "")
        self.assertIn(_lib.TRUST_BOUNDARY, action, action)

    def test_qa_pending_next_action_states_the_boundary(self):
        action = self._context(review_done=True).get("next_action", "")
        self.assertIn(_lib.TRUST_BOUNDARY, action, action)


if __name__ == "__main__":
    unittest.main()
