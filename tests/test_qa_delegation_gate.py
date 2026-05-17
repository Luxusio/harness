from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "qa_delegation_gate_test", str(SCRIPTS / "qa_delegation_gate.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qa_delegation_gate = _load_gate()


class TestQaDelegationGate(unittest.TestCase):
    def _run_gate(self, payload: dict, *, env: dict[str, str] | None = None) -> str:
        buf = io.StringIO()
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        with mock.patch.dict(os.environ, merged_env, clear=True):
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                with contextlib.redirect_stdout(buf):
                    qa_delegation_gate.main()
        return buf.getvalue()

    def test_main_browser_mcp_call_is_denied(self):
        out = self._run_gate({
            "tool_name": "mcp__chrome-devtools__take_snapshot",
            "transcript_path": "/tmp/not-present.jsonl",
        })

        self.assertIn("hookSpecificOutput", out)
        payload = json.loads(out)
        hso = payload["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("qa-browser", hso["permissionDecisionReason"])

    def test_explicit_qa_browser_payload_is_allowed(self):
        out = self._run_gate({
            "tool_name": "mcp__chrome-devtools__take_snapshot",
            "subagent_type": "harness:qa-browser",
        })

        self.assertEqual(out, "")

    def test_qa_browser_transcript_fallback_is_allowed(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write(
                "---\n"
                "name: qa-browser\n"
                "description: harness browser QA agent\n"
                "---\n\n"
                "You are a senior QA engineer specializing in web application testing.\n"
            )
            transcript_path = fh.name
        try:
            out = self._run_gate({
                "tool_name": "mcp__chrome-devtools__take_snapshot",
                "transcript_path": transcript_path,
            })
        finally:
            os.unlink(transcript_path)

        self.assertEqual(out, "")

    def test_prose_mention_in_transcript_does_not_allow(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fh:
            fh.write("The main session should spawn harness:qa-browser for this work.\n")
            transcript_path = fh.name
        try:
            out = self._run_gate({
                "tool_name": "mcp__chrome-devtools__take_snapshot",
                "transcript_path": transcript_path,
            })
        finally:
            os.unlink(transcript_path)

        self.assertIn("hookSpecificOutput", out)

    def test_non_browser_mcp_call_is_silent(self):
        out = self._run_gate({"tool_name": "Bash"})

        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
