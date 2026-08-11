"""Regression tests for hook-owned subagent receipt provenance."""
from __future__ import annotations

import importlib
import json
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
    def test_unified_receipt_owner_token_is_hook(self):
        self.assertEqual(
            prewrite_gate.PROTECTED_ARTIFACTS["RECEIPTS.jsonl"],
            "receipt-lifecycle-hook",
        )

    def test_conversation_owner_token_is_hook(self):
        self.assertEqual(
            prewrite_gate.PROTECTED_ARTIFACTS["CONVERSATION.md"],
            "conversation-hook",
        )

    def test_unified_receipt_human_text_names_hooks(self):
        human = prewrite_gate.PROTECTED_ARTIFACT_HUMAN["RECEIPTS.jsonl"]
        self.assertIn("review and QA lifecycle hooks", human)

    def test_conversation_human_text_names_hooks(self):
        human = prewrite_gate.PROTECTED_ARTIFACT_HUMAN["CONVERSATION.md"]
        self.assertIn("conversation hooks", human)


class TestProvenance(unittest.TestCase):
    def test_provenance_includes_qa_and_ux_keys_from_subagent_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            run_id = "a" * 32
            with open(os.path.join(td, "TASK_RUN.json"), "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "task_run_id": run_id,
                    "started_at": "2026-08-11T05:00:00.000000Z",
                }) + "\n")
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
                self.assertFalse(prov[agent], f"empty receipt stream cannot prove {agent}")

            receipt = {
                "kind": "subagent",
                "status": "completed",
                "lens": "qa-cli",
                "verdict": "PASS",
                "task_run_id": run_id,
            }
            with open(os.path.join(td, "SUBAGENT_RECEIPTS.jsonl"), "w") as f:
                f.write(json.dumps(receipt) + "\n")
            prov = _lib.provenance_from_artifacts(td)
            self.assertTrue(prov["qa-cli"])
            for agent in ("qa-browser", "qa-api", "qa-desktop"):
                self.assertFalse(prov[agent], f"qa-cli cannot prove {agent}")


class TestDenyDecisionSubagentReceipt(unittest.TestCase):
    def test_write_unified_receipt_denies_with_hook_owner(self):
        with scratch_task_in_real_repo("subagent-receipt") as task_dir:
            receipt = os.path.join(task_dir, "RECEIPTS.jsonl")
            r = invoke_hook(GATE, "Write", {"file_path": receipt})
            self.assertEqual(r.returncode, 0)
            decision, reason = parse_decision(r.stdout)
            self.assertEqual(decision, "deny")
            self.assertIsNotNone(reason)
            self.assertIn("C-05-protected-artifact", reason)
            self.assertIn("owner=receipt-lifecycle-hook", reason)


if __name__ == "__main__":
    unittest.main()
