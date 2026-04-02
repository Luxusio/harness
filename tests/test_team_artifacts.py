import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

from task_completed_gate import compute_completion_failures


class TestTeamArtifacts(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_team_task(self, task_id="TASK__team_artifacts"):
        task_dir = os.path.join(self.tmp.name, task_id)
        os.makedirs(task_dir, exist_ok=True)
        Path(task_dir, "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            "status: planned\n"
            "mutates_repo: false\n"
            "lane: build\n"
            "plan_verdict: PASS\n"
            "execution_mode: standard\n"
            "orchestration_mode: team\n"
            "team_provider: omc\n"
            "team_status: planned\n"
            "team_plan_required: true\n"
            "team_synthesis_required: true\n"
            "fallback_used: none\n",
            encoding="utf-8",
        )
        Path(task_dir, "PLAN.md").write_text("# Plan\n\nShip the feature.\n", encoding="utf-8")
        Path(task_dir, "CRITIC__plan.md").write_text("verdict: PASS\n", encoding="utf-8")
        Path(task_dir, "HANDOFF.md").write_text(
            "# Handoff\n\n## Result\n- verified: manual smoke test\n",
            encoding="utf-8",
        )
        return task_dir

    def _write_complete_team_plan(self, task_dir):
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- worker-a: app\n- worker-b: api\n\n"
            "## Owned Writable Paths\n- worker-a: app/**\n- worker-b: api/**\n\n"
            "## Shared Read-Only Paths\n- docs/**\n\n"
            "## Forbidden Writes\n- worker-a: api/**\n- worker-b: app/**\n\n"
            "## Synthesis Strategy\n- lead merges then verifies\n",
            encoding="utf-8",
        )

    def _write_complete_team_synthesis(self, task_dir):
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and api work\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

    def test_incomplete_team_artifacts_block_close(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- TODO: summarize\n\n"
            "## Cross-Checks\n- TBD\n",
            encoding="utf-8",
        )

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_SYNTHESIS.md is incomplete", joined)
        self.assertIn("team_status must resolve", joined)

    def test_complete_team_artifacts_allow_close_without_manual_team_status_edit(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("TEAM_PLAN.md", joined)
        self.assertNotIn("TEAM_SYNTHESIS.md", joined)
        self.assertNotIn("team_status must resolve", joined)

    def test_invalid_team_ownership_blocks_close(self):
        task_dir = self._make_team_task()
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- worker-a: app\n- worker-b: api\n\n"
            "## Owned Writable Paths\n- worker-a: src/api/**\n- worker-b: src/api/auth.ts\n\n"
            "## Shared Read-Only Paths\n- docs/**\n\n"
            "## Forbidden Writes\n- worker-a: none\n- worker-b: none\n\n"
            "## Synthesis Strategy\n- lead merges then verifies\n",
            encoding="utf-8",
        )
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_PLAN.md is incomplete", joined)
        self.assertIn("ownership errors", joined)
        self.assertIn("overlapping writable ownership", joined)

if __name__ == "__main__":
    unittest.main()
