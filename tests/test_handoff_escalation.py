import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))

from handoff_escalation import generate_handoff, preview_handoff
from _lib import build_team_bootstrap, build_team_dispatch, build_team_launch


class TestTeamHandoffEscalation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_team_task(self, task_id="TASK__team_handoff"):
        task_dir = os.path.join(self.tmp.name, task_id)
        os.makedirs(task_dir, exist_ok=True)
        Path(task_dir, "TASK_STATE.yaml").write_text(
            f"task_id: {task_id}\n"
            "status: in_progress\n"
            "lane: build\n"
            "plan_verdict: PASS\n"
            "execution_mode: standard\n"
            "orchestration_mode: team\n"
            "team_provider: omc\n"
            "team_status: planned\n"
            "team_plan_required: true\n"
            "team_synthesis_required: true\n"
            "fallback_used: none\n"
            "roots_touched: [app, api]\n"
            "touched_paths: [app/main.py, api/server.py]\n",
            encoding="utf-8",
        )
        Path(task_dir, "PLAN.md").write_text("# Plan\n\nShip the feature.\n", encoding="utf-8")
        Path(task_dir, "HANDOFF.md").write_text(
            "# Handoff\n\n## Verification\n```bash\npytest tests/test_example.py\n```\n",
            encoding="utf-8",
        )
        Path(task_dir, "CRITIC__runtime.md").write_text(
            "verdict: FAIL\n"
            "summary: app/main.py still fails smoke test\n\n"
            "## Transcript\n"
            "pytest tests/test_example.py\n",
            encoding="utf-8",
        )
        return task_dir

    def _enable_runtime_gate(self, task_dir, verdict="pending"):
        state_path = Path(task_dir, "TASK_STATE.yaml")
        content = state_path.read_text(encoding="utf-8")
        if "mutates_repo:" in content:
            content = content.replace("mutates_repo: false", "mutates_repo: true")
        else:
            content += "mutates_repo: true\n"
        if "runtime_verdict:" in content:
            content = content.replace("runtime_verdict: pending", f"runtime_verdict: {verdict}")
        else:
            content += f"runtime_verdict: {verdict}\n"
        state_path.write_text(content, encoding="utf-8")

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

    def _write_documentation_owner_plan(self, task_dir):
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n- lead: integrator\n- worker-a: app\n- reviewer: doc-reviewer\n\n"
            "## Owned Writable Paths\n- lead: tests/**\n- worker-a: app/**\n- reviewer: docs/**\n\n"
            "## Shared Read-Only Paths\n- api/**\n\n"
            "## Forbidden Writes\n- lead: app/**, docs/**\n- worker-a: tests/**, docs/**\n- reviewer: tests/**, app/**\n\n"
            "## Synthesis Strategy\n- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n\n"
            "## Documentation Ownership\n- writer: reviewer\n- critic-document: lead\n",
            encoding="utf-8",
        )

    def _write_worker_summary(self, task_dir, worker_name, handled_path):
        team_dir = Path(task_dir, "team")
        team_dir.mkdir(parents=True, exist_ok=True)
        rel_name = worker_name if worker_name.startswith("worker-") else f"worker-{worker_name}"
        Path(team_dir, rel_name + ".md").write_text(
            "# Worker Summary\n"
            "## Completed Work\n- finished assigned slice\n\n"
            f"## Owned Paths Handled\n- {handled_path}\n\n"
            "## Verification\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
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
            what_changed = "- docs/architecture.md aligned with the verified implementation"
            updated = "- docs/architecture.md"
            notes = "- verified after final runtime QA"
        else:
            what_changed = "none"
            updated = "none"
            notes = "none"
        Path(task_dir, "DOC_SYNC.md").write_text(
            "# DOC_SYNC: task\n"
            "written_at: 2026-01-01T00:00:00Z\n\n"
            "## What changed\n"
            f"{what_changed}\n\n"
            "## New files\nnone\n\n"
            "## Updated files\n"
            f"{updated}\n\n"
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
            "summary: docs match the verified behavior\n\n"
            "## Findings\n"
            "- docs/architecture.md is current\n",
            encoding="utf-8",
        )

    def _write_runtime_pass(self, task_dir):
        Path(task_dir, "CRITIC__runtime.md").write_text(
            "verdict: PASS\n"
            "summary: final runtime verification passed\n\n"
            "## Transcript\n"
            "pytest tests/test_example.py\n",
            encoding="utf-8",
        )

    def test_team_handoff_surfaces_pending_workers_and_paths(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "worker_summaries")
        self.assertEqual(team["pending_workers"], ["worker-b"])
        self.assertIn("api/**", team["pending_owned_paths"])
        self.assertIn("api/**", handoff["paths_in_focus"])
        self.assertIn("TEAM_PLAN.md", handoff["files_to_read_first"])
        self.assertIn("team/worker-a.md", handoff["files_to_read_first"])
        self.assertIn("worker-b", handoff["next_step"])

    def test_team_handoff_requests_bootstrap_before_worker_resume(self):
        task_dir = self._make_team_task("TASK__team_handoff_bootstrap")
        self._write_complete_team_plan(task_dir)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "bootstrap")
        self.assertTrue(team["bootstrap_available"])
        self.assertFalse(team["bootstrap_generated"])
        self.assertIn("team/bootstrap/index.json", team["pending_artifacts"])
        self.assertIn("team-bootstrap", handoff["next_step"])

    def test_team_handoff_requests_dispatch_after_bootstrap(self):
        task_dir = self._make_team_task("TASK__team_handoff_dispatch")
        self._write_complete_team_plan(task_dir)
        build_team_bootstrap(task_dir, write_files=True)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "dispatch")
        self.assertTrue(team["bootstrap_generated"])
        self.assertTrue(team["dispatch_available"])
        self.assertFalse(team["dispatch_generated"])
        self.assertIn("team/bootstrap/provider/dispatch.json", team["pending_artifacts"])
        self.assertIn("team-dispatch", handoff["next_step"])

    def test_team_handoff_prefers_team_relaunch_for_pending_worker_when_dispatch_is_current(self):
        task_dir = self._make_team_task("TASK__team_handoff_relaunch_worker")
        self._write_complete_team_plan(task_dir)
        build_team_bootstrap(task_dir, write_files=True)
        build_team_dispatch(task_dir, write_files=True)
        build_team_launch(task_dir, write_files=True)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "worker_summaries")
        self.assertTrue(team["relaunch_available"])
        self.assertEqual(team["relaunch_phase"], "implement")
        self.assertEqual(team["relaunch_worker"], "worker-a")
        self.assertIn("team-relaunch", handoff["next_step"])
        self.assertIn("worker-a", handoff["next_step"])

    def test_team_handoff_requests_launch_after_dispatch(self):
        task_dir = self._make_team_task("TASK__team_handoff_launch")
        self._write_complete_team_plan(task_dir)
        build_team_bootstrap(task_dir, write_files=True)
        build_team_dispatch(task_dir, write_files=True)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "launch")
        self.assertTrue(team["launch_available"])
        self.assertFalse(team["launch_generated"])
        self.assertIn("team/bootstrap/provider/launch.json", team["pending_artifacts"])
        self.assertIn("team-launch", handoff["next_step"])

    def test_team_handoff_requires_plan_before_worker_execution(self):
        task_dir = self._make_team_task()
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n\n"
            "## Worker Roster\n- TODO: assign workers\n\n"
            "## Owned Writable Paths\n- TBD\n\n"
            "## Shared Read-Only Paths\n- TBD\n\n"
            "## Forbidden Writes\n- TBD\n\n"
            "## Synthesis Strategy\n- TODO\n",
            encoding="utf-8",
        )

        handoff = generate_handoff(task_dir, "sprinted_compaction")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "plan")
        self.assertIn("TEAM_PLAN.md", team["pending_artifacts"])
        self.assertIn("TEAM_PLAN.md", handoff["files_to_read_first"])
        self.assertIn("Complete TEAM_PLAN.md", handoff["next_step"])

    def test_team_handoff_points_to_synthesis_refresh_after_workers_finish(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- TODO\n\n"
            "## Cross-Checks\n- TBD\n",
            encoding="utf-8",
        )

        handoff = generate_handoff(task_dir, "criterion_reopen_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "synthesis")
        self.assertIn("TEAM_SYNTHESIS.md", team["pending_artifacts"])
        self.assertIn("TEAM_SYNTHESIS.md", handoff["files_to_read_first"])
        self.assertIn("TEAM_SYNTHESIS.md", handoff["next_step"])


    def test_complete_team_handoff_exposes_worker_details_and_refresh_need(self):
        task_dir = self._make_team_task()
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and api slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "complete")
        self.assertTrue(team["handoff_refresh_needed"])
        self.assertIn("HANDOFF.md", handoff["files_to_read_first"])
        self.assertIn("HANDOFF.md", handoff["next_step"])
        worker_a = team["workers"]["worker-a"]
        self.assertEqual(worker_a["artifact"], "team/worker-a.md")
        self.assertIn("app/**", worker_a["owned_writable_paths"])
        self.assertIn("app/main.py", worker_a["owned_paths_handled"])
        self.assertIn("pytest", worker_a["verification_excerpt"])


    def test_team_handoff_enters_verification_phase_after_synthesis(self):
        task_dir = self._make_team_task("TASK__team_handoff_verify")
        self._enable_runtime_gate(task_dir, verdict="PASS")
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and api slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "verification")
        self.assertTrue(team["verification_needed"])
        self.assertIn("CRITIC__runtime.md", team["pending_artifacts"])
        self.assertIn("CRITIC__runtime.md", handoff["next_step"])
        self.assertIn("final runtime verification", handoff["next_step"])

    def test_team_handoff_enters_documentation_phase_after_final_verification(self):
        task_dir = self._make_team_task("TASK__team_handoff_docs")
        self._enable_runtime_gate(task_dir, verdict="PASS")
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and api slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )
        self._write_runtime_pass(task_dir)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "documentation")
        self.assertIn("DOC_SYNC.md", team["pending_artifacts"])
        self.assertIn("DOC_SYNC.md", handoff["next_step"])

    def test_team_handoff_documentation_phase_surfaces_document_critic(self):
        task_dir = self._make_team_task("TASK__team_handoff_doc_critic")
        self._enable_runtime_gate(task_dir, verdict="PASS")
        self._write_complete_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "worker-b", "api/server.py")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and api slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )
        Path(task_dir, "CRITIC__runtime.md").write_text(
            "verdict: PASS\nsummary: final runtime verification passed\n\n## Transcript\npytest tests/test_example.py\n",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=True)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "documentation")
        self.assertTrue(team["document_critic_needed"])
        self.assertIn("CRITIC__document.md", team["pending_artifacts"])
        self.assertIn("critic-document", handoff["next_step"])

    def test_team_handoff_documentation_phase_names_doc_owners(self):
        task_dir = self._make_team_task("TASK__team_handoff_doc_owners")
        self._enable_runtime_gate(task_dir, verdict="PASS")
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and docs slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )
        Path(task_dir, "CRITIC__runtime.md").write_text(
            "verdict: PASS\nsummary: final runtime verification passed\n\n## Transcript\npytest tests/test_example.py\n",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=True)

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["doc_sync_owners"], ["reviewer"])
        self.assertEqual(team["document_critic_owners"], ["lead"])
        self.assertIn("reviewer", handoff["next_step"])
        self.assertIn("lead", handoff["next_step"])

    def test_team_handoff_degraded_round_returns_to_synthesis_phase(self):
        task_dir = self._make_team_task("TASK__team_handoff_degraded")
        self._enable_runtime_gate(task_dir, verdict="PASS")
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.py")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            "# Team Synthesis\n"
            "## Integrated Result\n- merged app and docs slices\n\n"
            "## Cross-Checks\n- ownership respected\n\n"
            "## Verification Summary\n- pytest tests/test_example.py\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )
        self._write_runtime_pass(task_dir)
        self._write_doc_sync(task_dir, meaningful=True)
        self._write_document_pass(task_dir)

        build_team_bootstrap(task_dir, write_files=True)
        build_team_dispatch(task_dir, write_files=True)

        os.utime(Path(task_dir, "TEAM_PLAN.md"), (10, 10))
        os.utime(Path(task_dir, "team", "worker-a.md"), (20, 20))
        os.utime(Path(task_dir, "team", "worker-reviewer.md"), (30, 30))
        os.utime(Path(task_dir, "TEAM_SYNTHESIS.md"), (40, 40))
        os.utime(Path(task_dir, "CRITIC__runtime.md"), (50, 50))
        os.utime(Path(task_dir, "DOC_SYNC.md"), (60, 60))
        os.utime(Path(task_dir, "CRITIC__document.md"), (70, 70))
        os.utime(Path(task_dir, "HANDOFF.md"), (80, 80))
        self._set_state_field(task_dir, "team_status", "degraded")
        os.utime(Path(task_dir, "TASK_STATE.yaml"), (90, 90))

        handoff = generate_handoff(task_dir, "runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        team = handoff["team_recovery"]
        self.assertEqual(team["phase"], "synthesis")
        self.assertIn("TEAM_SYNTHESIS.md", team["pending_artifacts"])
        self.assertIn("TEAM_SYNTHESIS.md", handoff["files_to_read_first"])
        self.assertIn("lead should refresh TEAM_SYNTHESIS.md", handoff["next_step"])
        self.assertIn("TEAM_SYNTHESIS.md", handoff["next_step"])

    def test_preview_handoff_does_not_write_session_file(self):
        task_dir = self._make_team_task("TASK__handoff_preview")
        self._write_complete_team_plan(task_dir)

        handoff = preview_handoff(task_dir, trigger="runtime_fail_repeat")

        self.assertIsNotNone(handoff)
        self.assertFalse(Path(task_dir, "SESSION_HANDOFF.json").exists())

    def test_blocked_env_handoff_reads_environment_snapshot(self):
        task_dir = os.path.join(self.tmp.name, "TASK__blocked_env_handoff")
        os.makedirs(task_dir, exist_ok=True)
        Path(task_dir, "TASK_STATE.yaml").write_text(
            "task_id: TASK__blocked_env_handoff\n"
            "status: blocked_env\n"
            "lane: build\n"
            "execution_mode: sprinted\n"
            "blockers: missing playwright browsers\n"
            "roots_touched: [app, tests]\n"
            "touched_paths: [app/main.tsx, tests/e2e/refund.spec.ts]\n",
            encoding="utf-8",
        )
        Path(task_dir, "PLAN.md").write_text("# Plan\n", encoding="utf-8")
        Path(task_dir, "ENVIRONMENT_SNAPSHOT.md").write_text(
            "# Environment Snapshot\n- browsers missing\n", encoding="utf-8"
        )

        handoff = preview_handoff(task_dir)

        self.assertIsNotNone(handoff)
        self.assertEqual(handoff["trigger"], "blocked_env_reentry")
        self.assertIn("ENVIRONMENT_SNAPSHOT.md", handoff["next_step"])
        self.assertIn("ENVIRONMENT_SNAPSHOT.md", handoff["files_to_read_first"])


if __name__ == "__main__":
    unittest.main()
