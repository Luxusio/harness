"""AC-001 — gate_response helper + next_action_command propagation.

Covers:
  - canonical shape (`decision`, `reason`, `next_action_command`, `owner_skill`, `docs`)
  - `emit_permission_decision` kwargs land in the PreToolUse envelope as a tail

Run: python3 -m unittest tests.regression.retro_2026_05_12_browser_qa_skip_hardening.test_ac_001__gate_response
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
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


if __name__ == "__main__":
    unittest.main()
