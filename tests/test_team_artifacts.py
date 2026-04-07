import os
import re
import sys
import tempfile
import time
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
            "# Handoff\n\n## Result\n- feature delivered\n\n## Verification\n- manual smoke test passed\n\n## Next Steps\n- monitor logs\n",
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

    def _refresh_handoff(self, task_dir):
        Path(task_dir, "HANDOFF.md").write_text(
            "# Handoff\n\n"
            "## Result\n- team artifacts merged\n\n"
            "## Verification\n- pytest tests/test_example.py\n\n"
            "## Team Trace\n- refreshed after TEAM_SYNTHESIS.md\n",
            encoding="utf-8",
        )

    def _enable_runtime_gate(self, task_dir, verdict="PASS"):
        state_path = Path(task_dir, "TASK_STATE.yaml")
        content = state_path.read_text(encoding="utf-8")
        content = content.replace("mutates_repo: false", "mutates_repo: true")
        if re.search(r"^runtime_verdict:\s*.+$", content, re.MULTILINE):
            content = re.sub(r"^runtime_verdict:\s*.+$", f"runtime_verdict: {verdict}", content, flags=re.MULTILINE)
        else:
            content += f"runtime_verdict: {verdict}\n"
        state_path.write_text(content, encoding="utf-8")

    def _write_runtime_pass(self, task_dir):
        Path(task_dir, "CRITIC__runtime.md").write_text(
            "verdict: PASS\n"
            "summary: final runtime verification passed\n\n"
            "## Transcript\n"
            "pytest tests/test_example.py\n",
            encoding="utf-8",
        )

    def _set_state_field(self, task_dir, field, value):
        state_path = Path(task_dir, "TASK_STATE.yaml")
        content = state_path.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(field)}:\s*.+$", content, re.MULTILINE):
            content = re.sub(rf"^{re.escape(field)}:\s*.+$", f"{field}: {value}", content, flags=re.MULTILINE)
        else:
            content += f"{field}: {value}\n"
        state_path.write_text(content, encoding="utf-8")

    def _write_doc_sync(self, task_dir, *, meaningful=False):
        if meaningful:
            what_changed = "- docs/architecture.md aligned with the merged implementation"
            updated_files = "- docs/architecture.md"
            notes = "- verified after runtime QA"
        else:
            what_changed = "none"
            updated_files = "none"
            notes = "none"
        Path(task_dir, "DOC_SYNC.md").write_text(
            "# DOC_SYNC: task\n"
            "written_at: 2026-01-01T00:00:00Z\n\n"
            "## What changed\n"
            f"{what_changed}\n\n"
            "## New files\nnone\n\n"
            "## Updated files\n"
            f"{updated_files}\n\n"
            "## Deleted files\nnone\n\n"
            "## Notes\n"
            f"{notes}\n",
            encoding="utf-8",
        )

    def _write_document_pass(self, task_dir):
        self._set_state_field(task_dir, "document_verdict", "PASS")
        self._set_state_field(task_dir, "doc_changes_detected", "true")
        Path(task_dir, "CRITIC__document.md").write_text(
            "verdict: PASS\n"
            "summary: documentation matches the verified behavior\n\n"
            "## Findings\n"
            "- docs/architecture.md reflects the merged feature\n",
            encoding="utf-8",
        )

    def _write_lead_plan(self, task_dir):
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- lead: integrator\n- worker-a: app\n- worker-b: api\n\n"
            "## Owned Writable Paths\n- lead: tests/**\n- worker-a: app/**\n- worker-b: api/**\n\n"
            "## Shared Read-Only Paths\n- docs/**\n\n"
            "## Forbidden Writes\n- lead: app/**, api/**\n- worker-a: tests/**, api/**\n- worker-b: tests/**, app/**\n\n"
            "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n",
            encoding="utf-8",
        )

    def _write_worker_summary(self, task_dir, worker_name, handled_path, verification="- pytest tests/test_example.py\n"):
        rel_name = worker_name if worker_name.startswith("worker-") else f"worker-{worker_name}"
        team_dir = Path(task_dir, "team")
        team_dir.mkdir(parents=True, exist_ok=True)
        Path(team_dir, rel_name + ".md").write_text(
            "# Worker Summary\n"
            "## Completed Work\n- finished assigned slice\n\n"
            f"## Owned Paths Handled\n- {handled_path}\n\n"
            f"## Verification\n{verification}\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

    def _write_complete_worker_summaries(self, task_dir):
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")

    def test_incomplete_team_artifacts_block_close(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
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
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("TEAM_PLAN.md", joined)
        self.assertNotIn("TEAM_SYNTHESIS.md", joined)
        self.assertNotIn("team_status must resolve", joined)

    def test_overlapping_team_plan_blocks_close(self):
        task_dir = self._make_team_task()
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- worker-a: app\n- worker-b: api\n\n"
            "## Owned Writable Paths\n- worker-a: src/api/*.ts\n- worker-b: src/api/auth.ts\n\n"
            "## Shared Read-Only Paths\n- docs/**\n\n"
            "## Forbidden Writes\n- worker-a: src/api/auth.ts\n- worker-b: src/api/*.ts\n\n"
            "## Synthesis Strategy\n- lead merges then verifies\n",
            encoding="utf-8",
        )
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_PLAN.md is incomplete", joined)
        self.assertIn("overlapping writable ownership", joined)

    def test_missing_worker_summaries_block_close(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_SYNTHESIS.md is incomplete", joined)
        self.assertIn("missing worker summaries: worker-b", joined)

    def test_worker_summary_ownership_mismatch_blocks_close(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "api/server.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_SYNTHESIS.md is incomplete", joined)
        self.assertIn("incomplete worker summaries", joined)
        self.assertIn("owned path 'api/server.py' is owned by worker-b", joined)

    def test_synthesis_must_refresh_after_latest_worker_summary(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        self._refresh_handoff(task_dir)

        time.sleep(0.02)
        team_dir = Path(task_dir, "team")
        worker_path = team_dir / "worker-a.md"
        worker_path.write_text(worker_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("TEAM_SYNTHESIS.md is incomplete", joined)
        self.assertIn("refresh TEAM_SYNTHESIS.md after the latest worker summary update", joined)


    def test_stale_handoff_after_team_synthesis_blocks_close(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        time.sleep(0.02)
        self._write_complete_team_synthesis(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("HANDOFF.md is stale for team close", joined)
        self.assertIn("refresh HANDOFF.md", joined)

    def test_lead_worker_not_required_to_write_worker_summary(self):
        task_dir = self._make_team_task("TASK__lead_team")
        self._write_lead_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        self._write_complete_team_synthesis(task_dir)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("missing worker summaries: lead", joined)
        self.assertNotIn("TEAM_SYNTHESIS.md", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)


    def test_final_team_runtime_verification_must_refresh_after_synthesis(self):
        task_dir = self._make_team_task("TASK__team_runtime_stale")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_runtime_pass(task_dir)
        time.sleep(0.02)
        self._write_complete_team_synthesis(task_dir)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("Final team runtime verification is stale", joined)
        self.assertIn("run final runtime verification", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)

    def test_handoff_must_refresh_after_final_team_runtime_verification(self):
        task_dir = self._make_team_task("TASK__team_runtime_handoff")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)
        time.sleep(0.02)
        self._write_runtime_pass(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("Final team runtime verification is stale", joined)
        self.assertIn("Team documentation sync is stale", joined)
        self.assertIn("DOC_SYNC.md", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)

    def test_runtime_verification_then_handoff_refresh_allows_close(self):
        task_dir = self._make_team_task("TASK__team_runtime_ready")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        time.sleep(0.02)
        self._write_runtime_pass(task_dir)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("Final team runtime verification is stale", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)

    def test_team_doc_sync_must_refresh_after_final_runtime_verification(self):
        task_dir = self._make_team_task("TASK__team_doc_sync_stale")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        self._write_doc_sync(task_dir, meaningful=False)
        time.sleep(0.02)
        self._write_runtime_pass(task_dir)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("Team documentation sync is stale", joined)
        self.assertIn("refresh DOC_SYNC.md", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)

    def test_team_document_critic_must_refresh_after_doc_sync(self):
        task_dir = self._make_team_task("TASK__team_doc_critic_stale")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        self._write_runtime_pass(task_dir)
        time.sleep(0.02)
        self._write_doc_sync(task_dir, meaningful=True)
        time.sleep(0.02)
        self._write_document_pass(task_dir)
        time.sleep(0.02)
        self._write_doc_sync(task_dir, meaningful=True)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertIn("Team document critic is stale", joined)
        self.assertIn("critic-document", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)

    def test_team_docs_then_document_critic_then_handoff_allows_close(self):
        task_dir = self._make_team_task("TASK__team_doc_ready")
        self._enable_runtime_gate(task_dir)
        self._write_complete_team_plan(task_dir)
        self._write_complete_worker_summaries(task_dir)
        self._write_complete_team_synthesis(task_dir)
        self._write_runtime_pass(task_dir)
        time.sleep(0.02)
        self._write_doc_sync(task_dir, meaningful=True)
        time.sleep(0.02)
        self._write_document_pass(task_dir)
        time.sleep(0.02)
        self._refresh_handoff(task_dir)

        failures = compute_completion_failures(task_dir)
        joined = "\n".join(failures)
        self.assertNotIn("Team documentation sync is stale", joined)
        self.assertNotIn("Team document critic is stale", joined)
        self.assertNotIn("HANDOFF.md is stale", joined)


if __name__ == "__main__":
    unittest.main()
