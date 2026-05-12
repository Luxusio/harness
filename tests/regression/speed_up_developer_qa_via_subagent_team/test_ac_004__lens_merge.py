"""Regression test for AC-004 (TASK__speed-up-developer-qa-via-subagent-team).

Verifies the lens-aware merge path in plugin/mcp/harness_server.py:
  - Creates CRITIC__qa.md with a global header on first lens write
  - Appends sections on subsequent lens writes
  - Computes worst-wins runtime_verdict (severity: PENDING < PASS < BLOCKED_ENV < FAIL)
  - Does NOT downgrade FAIL back to PASS

Runs via `python3 -m unittest tests.regression.speed_up_developer_qa_via_subagent_team.test_ac_004__lens_merge`.
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

spec = importlib.util.spec_from_file_location(
    "harness_server_ac004", str(REPO / "plugin" / "mcp" / "harness_server.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestLensMergeCriticQA(unittest.TestCase):
    """AC-004: write_critic_qa lens-aware merge path."""

    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.td = self.td_obj.name
        with open(os.path.join(self.td, "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "task_id: TASK__test_ac_004\n"
                "status: created\n"
                "runtime_verdict: pending\n"
                "touched_paths: []\n"
                "plan_session_state: closed\n"
                "closed_at: null\n"
                "updated: 2026-05-11T00:00:00Z\n"
            )

    def tearDown(self):
        self.td_obj.cleanup()

    def _read_state_field(self, field):
        with open(os.path.join(self.td, "TASK_STATE.yaml"), encoding="utf-8") as f:
            for line in f:
                if line.startswith(field + ":"):
                    return line.split(":", 1)[1].strip()
        return None

    def test_worst_verdict_helper(self):
        self.assertEqual(mod._worst_verdict("PENDING", "PASS"), "PASS")
        self.assertEqual(mod._worst_verdict("PASS", "FAIL"), "FAIL")
        self.assertEqual(mod._worst_verdict("FAIL", "PASS"), "FAIL")
        self.assertEqual(mod._worst_verdict("BLOCKED_ENV", "PASS"), "BLOCKED_ENV")
        self.assertEqual(mod._worst_verdict("PASS", "BLOCKED_ENV"), "BLOCKED_ENV")

    def test_first_lens_writer_creates_file(self):
        result = mod._lens_merge_critic_qa(
            self.td, "cli", "PASS", "summary one", "transcript one"
        )
        self.assertFalse(result.get("isError", False))
        path = os.path.join(self.td, "CRITIC__qa.md")
        self.assertTrue(os.path.exists(path))
        content = Path(path).read_text(encoding="utf-8")
        self.assertIn("# CRITIC — qa", content)
        self.assertIn("## qa-cli verdict: PASS", content)
        self.assertIn("summary one", content)
        self.assertIn("transcript one", content)
        self.assertEqual(self._read_state_field("runtime_verdict"), "PASS")

    def test_second_lens_appends_section(self):
        mod._lens_merge_critic_qa(self.td, "cli", "PASS", "s1", "t1")
        mod._lens_merge_critic_qa(self.td, "browser", "FAIL", "s2", "t2")
        path = os.path.join(self.td, "CRITIC__qa.md")
        content = Path(path).read_text(encoding="utf-8")
        # Both sections present
        self.assertIn("## qa-cli verdict: PASS", content)
        self.assertIn("## qa-browser verdict: FAIL", content)
        # Only one global header
        self.assertEqual(content.count("# CRITIC — qa"), 1)
        # Worst-wins: PASS -> FAIL
        self.assertEqual(self._read_state_field("runtime_verdict"), "FAIL")

    def test_worst_wins_does_not_downgrade(self):
        mod._lens_merge_critic_qa(self.td, "cli", "FAIL", "s1", "t1")
        mod._lens_merge_critic_qa(self.td, "api", "PASS", "s2", "t2")
        # FAIL stays FAIL even when subsequent lens is PASS
        self.assertEqual(self._read_state_field("runtime_verdict"), "FAIL")

    def test_three_lens_merge_blocked_env(self):
        mod._lens_merge_critic_qa(self.td, "cli", "PASS", "s1", "t1")
        mod._lens_merge_critic_qa(self.td, "browser", "BLOCKED_ENV", "s2", "t2")
        mod._lens_merge_critic_qa(self.td, "api", "PASS", "s3", "t3")
        content = Path(os.path.join(self.td, "CRITIC__qa.md")).read_text(encoding="utf-8")
        for lens in ("cli", "browser", "api"):
            self.assertIn(f"## qa-{lens} verdict:", content)
        self.assertEqual(self._read_state_field("runtime_verdict"), "BLOCKED_ENV")


if __name__ == "__main__":
    unittest.main()
