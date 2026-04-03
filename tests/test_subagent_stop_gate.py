import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

from subagent_stop_gate import check_team_artifacts


class TestSubagentStopGateTeamReminders(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_team_task(self, task_id="TASK__stop_team"):
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
        (task_dir / "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- lead: integrator\n- worker-a: app\n- reviewer: docs\n\n"
            "## Owned Writable Paths\n- lead: tests/**\n- worker-a: app/**\n- reviewer: docs/**\n\n"
            "## Shared Read-Only Paths\n- api/**\n\n"
            "## Forbidden Writes\n- lead: app/**, docs/**\n- worker-a: tests/**, docs/**\n- reviewer: app/**, tests/**\n\n"
            "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n\n"
            "## Documentation Ownership\n- writer: reviewer\n- critic-document: lead\n",
            encoding="utf-8",
        )
        return task_dir

    def _write_worker_summary(self, task_dir: Path, worker_name: str, handled_path: str):
        team_dir = task_dir / "team"
        team_dir.mkdir(parents=True, exist_ok=True)
        rel_name = worker_name if worker_name.startswith("worker-") else f"worker-{worker_name}"
        (team_dir / f"{rel_name}.md").write_text(
            "# Worker Summary\n"
            "## Completed Work\n- finished assigned slice\n\n"
            f"## Owned Paths Handled\n- {handled_path}\n\n"
            "## Verification\n- python -m pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

    def _write_synthesis_and_runtime(self, task_dir: Path):
        (task_dir / "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged worker outputs\n\n"
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

    def test_reminds_worker_to_write_missing_summary(self):
        task_dir = self._make_team_task()
        reminders = check_team_artifacts(str(task_dir), "harness:developer@worker-a")
        joined = "\n".join(reminders)
        self.assertIn("team/worker-a.md", joined)
        self.assertIn("worker-a", joined)

    def test_reminds_document_owner_to_refresh_doc_sync(self):
        task_dir = self._make_team_task()
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        self._write_synthesis_and_runtime(task_dir)
        reminders = check_team_artifacts(str(task_dir), "harness:writer@reviewer")
        joined = "\n".join(reminders)
        self.assertIn("DOC_SYNC.md", joined)
        self.assertIn("reviewer", joined)


if __name__ == "__main__":
    unittest.main()
