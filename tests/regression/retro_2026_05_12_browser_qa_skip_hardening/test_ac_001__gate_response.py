"""AC-001 — gate_response helper + next_action_command propagation.

Covers:
  - canonical shape (`decision`, `reason`, `next_action_command`, `owner_skill`, `docs`)
  - `emit_permission_decision` kwargs land in the PreToolUse envelope as a tail
  - stop_gate.py emits next_action_command derived from missing_for_close

Run: python3 -m unittest tests.regression.retro_2026_05_12_browser_qa_skip_hardening.test_ac_001__gate_response
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate_response = _load("_gate_response_test", SCRIPTS / "_gate_response.py")
lib = _load("_lib_test", SCRIPTS / "_lib.py")


class TestGateResponseShape(unittest.TestCase):
    def test_canonical_shape_keys(self):
        out = gate_response.gate_response(
            "block",
            reason="test",
            next_action_command="cmd",
            owner_skill="skill",
            docs="docs",
        )
        for key in ("decision", "reason", "next_action_command", "owner_skill", "docs"):
            self.assertIn(key, out)
        self.assertEqual(out["decision"], "block")

    def test_block_shortcut_decision_is_block(self):
        self.assertEqual(gate_response.block(reason="x")["decision"], "block")

    def test_deny_shortcut_decision_is_deny(self):
        self.assertEqual(gate_response.deny(reason="x")["decision"], "deny")

    def test_optional_kwargs_default_to_empty_string(self):
        out = gate_response.gate_response("block", reason="r")
        self.assertEqual(out["next_action_command"], "")
        self.assertEqual(out["owner_skill"], "")
        self.assertEqual(out["docs"], "")


class TestEmitPermissionDecisionAppendsTail(unittest.TestCase):
    """Verify emit_permission_decision wraps the new kwargs into the reason."""

    def _capture_emit(self, **kwargs):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            lib.emit_permission_decision("deny", "base reason", **kwargs)
        raw = buf.getvalue()
        if not raw:
            return None
        return json.loads(raw)

    def test_tail_contains_next_action_when_provided(self):
        payload = self._capture_emit(
            next_action_command="Skill('harness:plan', 'x')",
            owner_skill="plan-skill",
            docs="CONTRACTS.md",
        )
        self.assertIsNotNone(payload)
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("base reason", reason)
        self.assertIn("↳ next action: Skill('harness:plan', 'x')", reason)
        self.assertIn("↳ owner: plan-skill", reason)
        self.assertIn("↳ docs: CONTRACTS.md", reason)

    def test_no_tail_when_all_kwargs_empty(self):
        payload = self._capture_emit()
        self.assertIsNotNone(payload)
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(reason, "base reason")
        self.assertNotIn("↳", reason)

    def test_allow_decision_is_silent(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            lib.emit_permission_decision("allow", "anything", next_action_command="x")
        self.assertEqual(buf.getvalue(), "")


class TestStopGateEmitsNextAction(unittest.TestCase):
    """End-to-end: stop_gate.py builds payload with next_action_command from missing_for_close."""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.repo = self.td_obj.name
        os.makedirs(os.path.join(self.repo, "doc", "harness", "tasks"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "doc", "harness", "tasks", "TASK__demo"), exist_ok=True)
        # minimal TASK_STATE.yaml for emit_compact_context
        with open(os.path.join(self.repo, "doc", "harness", "tasks", "TASK__demo",
                               "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "task_id: TASK__demo\n"
                "status: implementing\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-05-12T00:00:00Z\n"
            )
        with open(os.path.join(self.repo, "doc", "harness", "tasks", ".active"),
                  "w", encoding="utf-8") as f:
            f.write(os.path.join(self.repo, "doc", "harness", "tasks", "TASK__demo"))

    def tearDown(self):
        self.td_obj.cleanup()

    def test_stop_gate_block_has_next_action_when_handoff_missing(self):
        stop_gate = _load("stop_gate_test", SCRIPTS / "stop_gate.py")
        # Patch find_repo_root to point at our fixture
        with mock.patch.object(stop_gate, "find_repo_root", return_value=self.repo):
            buf = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO("")):
                with mock.patch.object(sys, "stdout", buf):
                    stop_gate.main()
            out = buf.getvalue()
        self.assertIn("decision", out)
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("next_action_command", payload)
        # PLAN.md missing → plan-skill route
        self.assertIn("plan", payload["next_action_command"].lower())


if __name__ == "__main__":
    unittest.main()
