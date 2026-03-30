"""Tests for subagent/critic provenance recording and enforcement — Phase 0 §5.3.

Covers:
  - increment_agent_run updates TASK_STATE.yaml count + last_seen_at
  - get_agent_run_count reads back correctly
  - runtime path touched + developer run count == 0 → close block
  - repo-mutating task + critic-runtime run count == 0 → close block
  - doc-sync needed + writer run count == 0 → close block
  - doc critic needed + critic-document run count == 0 → close block

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import increment_agent_run, get_agent_run_count, yaml_field
from task_completed_gate import compute_completion_failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_state(task_dir, **overrides):
    """Write TASK_STATE.yaml with all agent_run_* fields at 0 by default."""
    defaults = {
        "task_id": "TASK__test",
        "status": "implemented",
        "mutates_repo": "true",
        "plan_verdict": "PASS",
        "runtime_verdict": "PASS",
        "document_verdict": "skipped",
        "doc_changes_detected": "false",
        "execution_mode": "standard",
        "orchestration_mode": "solo",
        "workflow_violations": "[]",
        "agent_run_developer_count": "0",
        "agent_run_developer_last": "null",
        "agent_run_critic_plan_count": "0",
        "agent_run_critic_plan_last": "null",
        "agent_run_critic_runtime_count": "0",
        "agent_run_critic_runtime_last": "null",
        "agent_run_writer_count": "0",
        "agent_run_writer_last": "null",
        "agent_run_critic_document_count": "0",
        "agent_run_critic_document_last": "null",
        "touched_paths": '["plugin/scripts/foo.py"]',
        "roots_touched": '["plugin"]',
        "verification_targets": '["plugin/scripts/foo.py"]',
        "blockers": "[]",
        "updated": "2026-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    lines = [f"{k}: {v}" for k, v in defaults.items()]
    _write(os.path.join(task_dir, "TASK_STATE.yaml"), "\n".join(lines) + "\n")


def _make_passing_artifacts(task_dir):
    """Write non-state artifact files for a passing task."""
    _write(os.path.join(task_dir, "PLAN.md"), "# Plan\nscope: test\n")
    _write(os.path.join(task_dir, "CRITIC__plan.md"), "verdict: PASS\n")
    _write(os.path.join(task_dir, "HANDOFF.md"),
           "# Handoff\n## Current state\nDone.\n## Verification\nTests pass.\n")
    _write(os.path.join(task_dir, "DOC_SYNC.md"), "none\n")
    _write(os.path.join(task_dir, "CRITIC__runtime.md"),
           "verdict: PASS\n## Evidence Bundle\n### Command Transcript\n$ pytest\nPASSED\n")


# ---------------------------------------------------------------------------
# increment_agent_run and get_agent_run_count
# ---------------------------------------------------------------------------

class TestAgentRunCounting(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_initial_count_is_zero(self):
        _make_state(self.task_dir)
        for agent in ("developer", "writer", "critic-plan", "critic-runtime", "critic-document"):
            self.assertEqual(get_agent_run_count(self.task_dir, agent), 0,
                f"Initial count for {agent} must be 0")

    def test_increment_increases_count(self):
        _make_state(self.task_dir)
        increment_agent_run(self.task_dir, "developer")
        self.assertEqual(get_agent_run_count(self.task_dir, "developer"), 1)

    def test_increment_twice(self):
        _make_state(self.task_dir)
        increment_agent_run(self.task_dir, "writer")
        increment_agent_run(self.task_dir, "writer")
        self.assertEqual(get_agent_run_count(self.task_dir, "writer"), 2)

    def test_increment_updates_last_seen_at(self):
        _make_state(self.task_dir)
        increment_agent_run(self.task_dir, "critic-runtime")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        last = yaml_field("agent_run_critic_runtime_last", state_file)
        self.assertNotEqual(last, "null", "last_seen_at must be updated after increment")
        self.assertIn("T", last, "last_seen_at must be ISO timestamp")

    def test_agents_are_independent(self):
        _make_state(self.task_dir)
        increment_agent_run(self.task_dir, "developer")
        increment_agent_run(self.task_dir, "developer")
        increment_agent_run(self.task_dir, "writer")
        self.assertEqual(get_agent_run_count(self.task_dir, "developer"), 2)
        self.assertEqual(get_agent_run_count(self.task_dir, "writer"), 1)
        self.assertEqual(get_agent_run_count(self.task_dir, "critic-plan"), 0)

    def test_count_on_missing_state(self):
        """Returns 0 if TASK_STATE.yaml does not exist — no crash."""
        empty_dir = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty_dir)
        self.assertEqual(get_agent_run_count(empty_dir, "developer"), 0)

    def test_increment_on_missing_state_returns_false(self):
        """increment_agent_run returns False if state file absent."""
        empty_dir = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty_dir)
        result = increment_agent_run(empty_dir, "developer")
        self.assertFalse(result)

    def test_hyphenated_agent_names(self):
        """critic-plan, critic-runtime, critic-document must work with hyphens."""
        _make_state(self.task_dir)
        for agent in ("critic-plan", "critic-runtime", "critic-document"):
            increment_agent_run(self.task_dir, agent)
            count = get_agent_run_count(self.task_dir, agent)
            self.assertEqual(count, 1, f"count for {agent} must be 1 after increment")


# ---------------------------------------------------------------------------
# §5.3 Provenance enforcement in completion gate
# ---------------------------------------------------------------------------

class TestProvenanceEnforcement(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_full_passing(self, **state_overrides):
        """Build a fully passing task with specified state overrides."""
        _make_state(self.task_dir, **state_overrides)
        _make_passing_artifacts(self.task_dir)

    def test_no_developer_run_with_runtime_paths_blocks(self):
        """verification_targets non-empty + developer count == 0 → block."""
        self._make_full_passing(
            agent_run_developer_count="0",
            agent_run_critic_runtime_count="1",
            agent_run_critic_plan_count="1",
            agent_run_writer_count="1",
        )
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("developer" in f.lower() for f in failures),
            f"Must block: runtime paths touched, developer count=0. Got: {failures}"
        )

    def test_developer_run_recorded_unblocks(self):
        """developer count >= 1 clears the developer provenance block."""
        self._make_full_passing(
            agent_run_developer_count="1",
            agent_run_developer_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="1",
            agent_run_critic_runtime_last="2026-01-01T00:00:00Z",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="1",
            agent_run_writer_last="2026-01-01T00:00:00Z",
        )
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("developer" in f.lower() for f in failures),
            f"developer count=1 must not be flagged. Got: {failures}"
        )

    def test_no_critic_runtime_run_blocks(self):
        """Repo-mutating task with critic-runtime count == 0 → block."""
        self._make_full_passing(
            agent_run_developer_count="1",
            agent_run_developer_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="0",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="1",
            agent_run_writer_last="2026-01-01T00:00:00Z",
        )
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("critic-runtime" in f.lower() or "runtime critic" in f.lower()
                or "critic_runtime" in f.lower() for f in failures),
            f"Must block: repo-mutating task, critic-runtime count=0. Got: {failures}"
        )

    def test_no_writer_run_on_mutating_task_blocks(self):
        """Repo-mutating task with writer count == 0 → block."""
        self._make_full_passing(
            agent_run_developer_count="1",
            agent_run_developer_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="1",
            agent_run_critic_runtime_last="2026-01-01T00:00:00Z",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="0",
        )
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("writer" in f.lower() for f in failures),
            f"Must block: mutating task, writer count=0. Got: {failures}"
        )

    def test_no_doc_critic_run_when_needed_blocks(self):
        """doc_changes_detected: true + critic-document count == 0 → block."""
        self._make_full_passing(
            doc_changes_detected="true",
            document_verdict="pending",
            agent_run_developer_count="1",
            agent_run_developer_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="1",
            agent_run_critic_runtime_last="2026-01-01T00:00:00Z",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="1",
            agent_run_writer_last="2026-01-01T00:00:00Z",
            agent_run_critic_document_count="0",
        )
        # Create document critic artifact to test provenance-only block
        _write(os.path.join(self.task_dir, "CRITIC__document.md"), "verdict: PASS\n")
        # But YAML says pending → stale PASS, and doc critic count is 0
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("document" in f.lower() for f in failures),
            f"Must block: doc critic needed, critic-document count=0. Got: {failures}"
        )

    def test_no_runtime_paths_no_developer_requirement(self):
        """Tasks with empty verification_targets don't require developer run."""
        _make_state(self.task_dir,
            mutates_repo="true",
            plan_verdict="PASS",
            runtime_verdict="PASS",
            document_verdict="skipped",
            doc_changes_detected="false",
            execution_mode="standard",
            orchestration_mode="solo",
            workflow_violations="[]",
            agent_run_developer_count="0",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="1",
            agent_run_critic_runtime_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="1",
            agent_run_writer_last="2026-01-01T00:00:00Z",
            # Empty verification_targets — no runtime paths
            touched_paths='[".claude/harness/tasks/TASK__test/HANDOFF.md"]',
            verification_targets="[]",
        )
        _make_passing_artifacts(self.task_dir)

        failures = compute_completion_failures(self.task_dir)
        # developer not required when verification_targets is empty
        self.assertFalse(
            any("developer" in f.lower() for f in failures),
            f"Empty verification_targets must not require developer run. Got: {failures}"
        )


if __name__ == "__main__":
    unittest.main()
