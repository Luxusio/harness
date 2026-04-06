import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

import write_artifact


class TestWriteArtifactTeamOwnership(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved_env = {
            "HARNESS_TEAM_WORKER": os.environ.get("HARNESS_TEAM_WORKER"),
            "CLAUDE_AGENT_NAME": os.environ.get("CLAUDE_AGENT_NAME"),
        }

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _make_team_task(self, task_id="TASK__artifact_team"):
        task_dir = Path(self.tmp.name) / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            "status: planned\n"
            "mutates_repo: true\n"
            "lane: build\n"
            "plan_verdict: PASS\n"
            "runtime_verdict: PASS\n"
            "document_verdict: pending\n"
            "doc_changes_detected: true\n"
            "execution_mode: standard\n"
            "orchestration_mode: team\n"
            "team_provider: omc\n"
            "team_status: planned\n"
            "team_plan_required: true\n"
            "team_synthesis_required: true\n"
            "fallback_used: none\n",
            encoding="utf-8",
        )
        (task_dir / "PLAN.md").write_text("# Plan\n\nShip the feature.\n", encoding="utf-8")
        (task_dir / "CRITIC__plan.md").write_text("verdict: PASS\n", encoding="utf-8")
        return task_dir

    def _write_team_plan(self, task_dir: Path):
        (task_dir / "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- lead: integrator\n- reviewer: docs\n\n"
            "## Owned Writable Paths\n- lead: tests/**\n- reviewer: docs/**\n\n"
            "## Shared Read-Only Paths\n- app/**\n\n"
            "## Forbidden Writes\n- lead: docs/**\n- reviewer: tests/**\n\n"
            "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n\n"
            "## Documentation Ownership\n- writer: reviewer\n- critic-document: lead\n",
            encoding="utf-8",
        )

    def _write_reviewer_summary(self, task_dir: Path):
        team_dir = task_dir / "team"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "worker-reviewer.md").write_text(
            "# Worker Summary\n"
            "## Completed Work\n- updated the docs slice\n\n"
            "## Owned Paths Handled\n- docs/architecture.md\n\n"
            "## Verification\n- python -m pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

    def _write_synthesis_and_runtime(self, task_dir: Path):
        (task_dir / "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged docs and tests updates\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- python -m pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )
        time.sleep(0.02)
        (task_dir / "CRITIC__runtime.md").write_text(
            "verdict: PASS\n"
            "summary: final verification passed\n\n"
            "## Transcript\n"
            "python -m pytest tests/test_example.py\n",
            encoding="utf-8",
        )

    def _prepare_documentation_phase(self):
        task_dir = self._make_team_task()
        self._write_team_plan(task_dir)
        self._write_reviewer_summary(task_dir)
        self._write_synthesis_and_runtime(task_dir)
        return task_dir

    def test_doc_sync_blocks_wrong_team_worker(self):
        task_dir = self._prepare_documentation_phase()
        os.environ["HARNESS_TEAM_WORKER"] = "lead"
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer@lead"

        args = SimpleNamespace(
            task_dir=str(task_dir),
            what_changed="aligned docs",
            new_files=None,
            updated_files="docs/architecture.md",
            deleted_files=None,
            notes=None,
        )
        with self.assertRaisesRegex(ValueError, "reserved for team owner"):
            write_artifact.cmd_doc_sync(args)

    def test_doc_sync_records_team_owner_metadata(self):
        task_dir = self._prepare_documentation_phase()
        os.environ["HARNESS_TEAM_WORKER"] = "reviewer"
        os.environ["CLAUDE_AGENT_NAME"] = "harness:writer@reviewer"

        args = SimpleNamespace(
            task_dir=str(task_dir),
            what_changed="aligned docs",
            new_files=None,
            updated_files="docs/architecture.md",
            deleted_files=None,
            notes="verified after runtime QA",
        )
        rc = write_artifact.cmd_doc_sync(args)
        self.assertEqual(rc, 0)

        doc_sync = (task_dir / "DOC_SYNC.md").read_text(encoding="utf-8")
        self.assertIn("team_worker: reviewer", doc_sync)
        self.assertIn("team_owner: reviewer", doc_sync)

        meta = json.loads((task_dir / "DOC_SYNC.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("team_worker"), "reviewer")
        self.assertEqual(meta.get("team_expected_workers"), ["reviewer"])
        self.assertTrue(meta.get("team_owner_match"))

    def test_handoff_infers_single_synthesis_owner_when_env_missing(self):
        task_dir = self._prepare_documentation_phase()
        os.environ.pop("HARNESS_TEAM_WORKER", None)
        os.environ.pop("CLAUDE_AGENT_NAME", None)

        args = SimpleNamespace(
            task_dir=str(task_dir),
            verify_cmd="python -m pytest tests/test_example.py",
            what_changed="merged team outputs",
            expected_output=None,
            do_not_regress=None,
        )
        rc = write_artifact.cmd_handoff(args)
        self.assertEqual(rc, 0)

        handoff = (task_dir / "HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("team_worker: lead", handoff)
        self.assertIn("team_worker_inferred: true", handoff)

        meta = json.loads((task_dir / "HANDOFF.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta.get("team_worker"), "lead")
        self.assertTrue(meta.get("team_worker_inferred"))
        self.assertEqual(meta.get("team_expected_workers"), ["lead"])

    def test_critic_plan_rejects_mismatched_agent_role(self):
        task_dir = self._make_team_task("TASK__artifact_role_guard")
        os.environ["CLAUDE_AGENT_NAME"] = "harness:harness"

        args = SimpleNamespace(
            task_dir=str(task_dir),
            verdict="PASS",
            summary="plan looks good",
            checks=None,
            issues=None,
        )
        with self.assertRaisesRegex(ValueError, r"must be written by \[critic-plan\]"):
            write_artifact.cmd_critic_plan(args)


if __name__ == "__main__":
    unittest.main()
