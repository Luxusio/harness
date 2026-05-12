"""AC-006 — write_critic_qa renders Manual UX section + downgrades verdict.

Scenarios:
  - lens=browser + no manual_ux → placeholder + runtime_verdict forced PENDING.
  - lens=browser + manual_ux supplied → verdict preserved + content rendered.
  - lens=cli (non-browser) + no manual_ux → "_n/a — non-browser lens_" + verdict preserved.
  - legacy no-lens + no manual_ux → "_n/a_" rendered + verdict preserved.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(REPO / "plugin" / "mcp"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


server = _load("harness_server_ac006", REPO / "plugin" / "mcp" / "harness_server.py")


class TestManualUxSection(unittest.TestCase):
    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.td = self.td_obj.name
        with open(os.path.join(self.td, "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "task_id: TASK__ac006_demo\n"
                "status: implementing\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-05-12T00:00:00Z\n"
            )

    def tearDown(self):
        self.td_obj.cleanup()

    def _state_verdict(self):
        with open(os.path.join(self.td, "TASK_STATE.yaml"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("runtime_verdict:"):
                    return line.split(":", 1)[1].strip()
        return None

    def _critic_body(self):
        with open(os.path.join(self.td, "CRITIC__qa.md"), encoding="utf-8") as f:
            return f.read()

    def test_browser_lens_without_manual_ux_downgrades_to_pending(self):
        result = server._lens_merge_critic_qa(
            self.td, "browser", "PASS", "ok", "details",
        )
        self.assertFalse(result.get("isError", False))
        body = self._critic_body()
        self.assertIn("## Manual UX verification", body)
        self.assertIn("_NOT SUPPLIED", body)
        self.assertIn("PENDING", body)
        self.assertEqual(self._state_verdict(), "PENDING")

    def test_browser_lens_with_manual_ux_keeps_pass(self):
        server._lens_merge_critic_qa(
            self.td, "browser", "PASS", "ok", "details",
            manual_ux="Verified login, search, and checkout flows on 1280x720.",
        )
        body = self._critic_body()
        self.assertIn("## Manual UX verification", body)
        self.assertIn("Verified login, search, and checkout", body)
        self.assertNotIn("_NOT SUPPLIED", body)
        self.assertEqual(self._state_verdict(), "PASS")

    def test_non_browser_lens_renders_na(self):
        server._lens_merge_critic_qa(
            self.td, "cli", "PASS", "ok", "details",
        )
        body = self._critic_body()
        self.assertIn("## Manual UX verification", body)
        self.assertIn("_n/a — non-browser lens_", body)
        self.assertEqual(self._state_verdict(), "PASS")

    def test_legacy_no_lens_path_renders_na(self):
        result = server.handle_write_critic_qa({
            "task_id": "TASK__ac006_demo",
            "task_dir": self.td,
            "verdict": "PASS",
            "summary": "ok",
            "transcript": "details",
        })
        self.assertFalse(result.get("isError", False))
        body = self._critic_body()
        self.assertIn("## Manual UX verification", body)
        self.assertIn("_n/a", body)
        self.assertEqual(self._state_verdict(), "PASS")


if __name__ == "__main__":
    unittest.main()
