"""Regression tests for TASK__add-qa-desktop-agent.

Locks in three changes:
  1. prewrite_gate.PROTECTED_ARTIFACTS["CRITIC__qa.md"] == "qa-agent"
     (was "qa-cli" — stale single-agent token)
  2. prewrite_gate.PROTECTED_ARTIFACT_HUMAN["CRITIC__qa.md"] names qa-desktop
  3. _lib.provenance_from_artifacts() returns "qa-desktop" key when
     CRITIC__qa.md exists in the task dir.

Also closes the pre-existing coverage gap: CRITIC__qa.md was in
PROTECTED_ARTIFACTS but had no deny-decision test.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest

from conftest import (  # type: ignore
    REPO_ROOT,
    SCRIPTS_DIR,
    invoke_hook,
    parse_decision,
    scratch_task_in_real_repo,
)

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # type: ignore
import prewrite_gate  # type: ignore

# Re-import to get fresh module state if test_prewrite_gate_* ran first.
importlib.reload(prewrite_gate)
importlib.reload(_lib)

GATE = os.path.join(SCRIPTS_DIR, "prewrite_gate.py")


class TestOwnerTokens(unittest.TestCase):
    def test_critic_qa_owner_token_is_qa_agent(self):
        """Machine owner= field in deny-reason tail must be 'qa-agent'."""
        self.assertEqual(prewrite_gate.PROTECTED_ARTIFACTS["CRITIC__qa.md"], "qa-agent")

    def test_critic_qa_human_text_includes_qa_desktop(self):
        """Human deny text for CRITIC__qa.md must name qa-desktop."""
        human = prewrite_gate.PROTECTED_ARTIFACT_HUMAN["CRITIC__qa.md"]
        self.assertIn("qa-desktop", human)
        for agent in ("qa-browser", "qa-api", "qa-cli", "qa-desktop"):
            self.assertIn(agent, human)


class TestProvenance(unittest.TestCase):
    def test_provenance_includes_qa_desktop_key(self):
        """provenance_from_artifacts must return a 'qa-desktop' key."""
        with tempfile.TemporaryDirectory() as td:
            prov = _lib.provenance_from_artifacts(td)
            self.assertIn("qa-desktop", prov)
            self.assertFalse(prov["qa-desktop"], "no CRITIC__qa.md → qa-desktop=False")

    def test_provenance_qa_desktop_true_with_critic_qa(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "CRITIC__qa.md"), "w").close()
            prov = _lib.provenance_from_artifacts(td)
            self.assertTrue(prov["qa-desktop"])
            for agent in ("qa-browser", "qa-api", "qa-cli", "qa-desktop"):
                self.assertTrue(prov[agent], f"{agent} provenance must be True")


class TestDenyDecisionCriticQa(unittest.TestCase):
    def test_write_critic_qa_md_denies_with_qa_agent_owner(self):
        """Direct Write to CRITIC__qa.md denies; structured tail owner=qa-agent."""
        with scratch_task_in_real_repo("qa-desktop-critic") as task_dir:
            critic = os.path.join(task_dir, "CRITIC__qa.md")
            r = invoke_hook(GATE, "Write", {"file_path": critic})
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIsNotNone(reason)
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("owner=qa-agent", reason)
            for agent in ("qa-browser", "qa-api", "qa-cli", "qa-desktop"):
                self.assertIn(agent, reason, f"{agent} missing from deny reason")


if __name__ == "__main__":
    unittest.main()
