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

    def test_mcp_bash_guard(self):
        self.assertEqual(_lib._escape_hint("mcp_bash_guard"),
                         "escape: HARNESS_SKIP_MCP_GUARD=1 <retry>")

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


class TestLogGateError(unittest.TestCase):
    def test_writes_gate_error_line(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".git"))  # so find_repo_root(td) returns td
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


if __name__ == "__main__":
    unittest.main()
