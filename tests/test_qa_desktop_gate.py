"""Regression tests for hook-owned subagent receipt provenance."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest

from conftest import (  # type: ignore
    SCRIPTS_DIR,
    invoke_hook,
    parse_decision,
    scratch_task_in_real_repo,
)

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # type: ignore
import prewrite_gate  # type: ignore

importlib.reload(prewrite_gate)
importlib.reload(_lib)

GATE = os.path.join(SCRIPTS_DIR, "prewrite_gate.py")


class TestOwnerTokens(unittest.TestCase):
    def test_subagent_receipt_owner_token_is_hook(self):
        self.assertEqual(
            prewrite_gate.PROTECTED_ARTIFACTS["SUBAGENT_RECEIPTS.jsonl"],
            "subagent-start-hook",
        )

    def test_conversation_owner_token_is_hook(self):
        self.assertEqual(
            prewrite_gate.PROTECTED_ARTIFACTS["CONVERSATION.md"],
            "conversation-hook",
        )

    def test_subagent_receipt_human_text_names_hooks(self):
        human = prewrite_gate.PROTECTED_ARTIFACT_HUMAN["SUBAGENT_RECEIPTS.jsonl"]
        self.assertIn("subagent-start hook", human)

    def test_conversation_human_text_names_hooks(self):
        human = prewrite_gate.PROTECTED_ARTIFACT_HUMAN["CONVERSATION.md"]
        self.assertIn("conversation hooks", human)


class TestProvenance(unittest.TestCase):
    def test_provenance_includes_qa_and_ux_keys_from_subagent_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            prov = _lib.provenance_from_artifacts(td)
            for agent in (
                "qa-browser",
                "qa-api",
                "qa-cli",
                "qa-desktop",
                "ux-browser",
                "ux-api",
                "ux-cli",
                "ux-desktop",
            ):
                self.assertIn(agent, prov)
                self.assertFalse(prov[agent])

            open(os.path.join(td, "SUBAGENT_RECEIPTS.jsonl"), "w").close()
            prov = _lib.provenance_from_artifacts(td)
            self.assertTrue(prov["subagent-start-hook"])
            for agent in ("qa-browser", "qa-api", "qa-cli", "qa-desktop"):
                self.assertTrue(prov[agent], f"{agent} provenance must be True")


class TestDenyDecisionSubagentReceipt(unittest.TestCase):
    def test_write_subagent_receipt_denies_with_hook_owner(self):
        with scratch_task_in_real_repo("subagent-receipt") as task_dir:
            receipt = os.path.join(task_dir, "SUBAGENT_RECEIPTS.jsonl")
            r = invoke_hook(GATE, "Write", {"file_path": receipt})
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIsNotNone(reason)
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("owner=subagent-start-hook", reason)


if __name__ == "__main__":
    unittest.main()
