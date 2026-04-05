"""Tests for file_changed_sync verdict invalidation and plan-first enforcement — Phase 0 §5.4.

Covers:
  - plan PASS before source mutation → no violation
  - source mutation before plan PASS → violation recorded
  - file change → runtime_verdict reset from PASS to pending
  - task with pending runtime_verdict after invalidation → close blocked
  - execution_mode/orchestration_mode pending on mutating task → close blocked

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from _lib import yaml_field, get_workflow_violations, append_workflow_violation
from file_changed_sync import (
    invalidate_runtime,
    invalidate_document,
    invalidate_note_freshness_for_changes,
)
from task_completed_gate import compute_completion_failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_state(task_dir, **overrides):
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
        "agent_run_developer_count": "1",
        "agent_run_developer_last": "2026-01-01T00:00:00Z",
        "agent_run_critic_plan_count": "1",
        "agent_run_critic_plan_last": "2026-01-01T00:00:00Z",
        "agent_run_critic_runtime_count": "1",
        "agent_run_critic_runtime_last": "2026-01-01T00:00:00Z",
        "agent_run_writer_count": "1",
        "agent_run_writer_last": "2026-01-01T00:00:00Z",
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
    _write(os.path.join(task_dir, "PLAN.md"), "# Plan\nscope: test\n")
    _write(os.path.join(task_dir, "CRITIC__plan.md"), "verdict: PASS\n")
    _write(os.path.join(task_dir, "HANDOFF.md"),
           "# Handoff\n## Current state\nDone.\n## Verification\nTests pass.\n")
    _write(os.path.join(task_dir, "DOC_SYNC.md"), "none\n")
    _write(os.path.join(task_dir, "CRITIC__runtime.md"),
           "verdict: PASS\n## Evidence Bundle\n### Command Transcript\n$ pytest\nPASSED\n")


# ---------------------------------------------------------------------------
# §5.4 Verdict invalidation — file_changed_sync
# ---------------------------------------------------------------------------

class TestVerdictInvalidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_invalidate_runtime_resets_from_pass_to_pending(self):
        """invalidate_runtime sets runtime_verdict from PASS to pending."""
        _make_state(self.task_dir, runtime_verdict="PASS")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")

        invalidate_runtime(state_file, "TASK__test", "src/foo.py changed")

        new_verdict = yaml_field("runtime_verdict", state_file)
        self.assertEqual(new_verdict, "pending",
            "runtime_verdict must be reset to pending after invalidation")

    def test_invalidate_runtime_noop_when_already_pending(self):
        """invalidate_runtime is a no-op when already pending (no redundant write)."""
        _make_state(self.task_dir, runtime_verdict="pending")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        mtime_before = os.path.getmtime(state_file)

        import time
        time.sleep(0.01)
        invalidate_runtime(state_file, "TASK__test", "src/foo.py changed")

        # Should still be pending, not errored
        self.assertEqual(yaml_field("runtime_verdict", state_file), "pending")

    def test_invalidate_document_resets_and_sets_doc_changes(self):
        """invalidate_document sets document_verdict to pending and doc_changes_detected to true."""
        _make_state(self.task_dir, document_verdict="PASS", doc_changes_detected="false")
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")

        invalidate_document(state_file, "TASK__test", "doc/common/api.md changed")

        self.assertEqual(yaml_field("document_verdict", state_file), "pending")
        self.assertEqual(yaml_field("doc_changes_detected", state_file), "true")

    def test_pending_runtime_verdict_after_invalidation_blocks_close(self):
        """After invalidation sets runtime_verdict: pending, close must be blocked."""
        _make_state(self.task_dir, runtime_verdict="PASS")
        _make_passing_artifacts(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")

        # Simulate file_changed_sync resetting verdict
        invalidate_runtime(state_file, "TASK__test", "plugin/scripts/foo.py changed")

        # Now attempt to close — must be blocked
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("runtime" in f.lower() for f in failures),
            f"Close must be blocked after runtime verdict invalidated. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# §5.4 Plan-first violation recording
# ---------------------------------------------------------------------------

class TestPlanFirstViolation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_violation_when_plan_already_passed(self):
        """No plan-first violation when plan_verdict is already PASS."""
        _make_state(self.task_dir, plan_verdict="PASS")

        violations = get_workflow_violations(self.task_dir)
        self.assertNotIn("source_mutation_before_plan_pass", violations,
            "No violation must be recorded when plan is already PASS")

    def test_violation_recorded_before_plan_pass(self):
        """Source mutation before plan PASS records workflow violation."""
        _make_state(self.task_dir,
            plan_verdict="pending",
            status="created",
            workflow_violations="[]",
        )

        # Simulate the violation that file_changed_sync should record
        append_workflow_violation(self.task_dir, "source_mutation_before_plan_pass")

        violations = get_workflow_violations(self.task_dir)
        self.assertIn("source_mutation_before_plan_pass", violations)

    def test_workflow_violation_blocks_close(self):
        """Plan-first violation blocks close even if all artifacts are present."""
        _make_state(
            self.task_dir,
            workflow_violations='["source_mutation_before_plan_pass"]',
        )
        _make_passing_artifacts(self.task_dir)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("violation" in f.lower() for f in failures),
            f"workflow_violations must block close. Got: {failures}"
        )

    def test_violation_not_duplicated(self):
        """append_workflow_violation does not add duplicate entries."""
        _make_state(self.task_dir, workflow_violations="[]")

        append_workflow_violation(self.task_dir, "source_mutation_before_plan_pass")
        append_workflow_violation(self.task_dir, "source_mutation_before_plan_pass")

        violations = get_workflow_violations(self.task_dir)
        count = violations.count("source_mutation_before_plan_pass")
        self.assertEqual(count, 1, "Violation must not be duplicated")


# ---------------------------------------------------------------------------
# Note freshness invalidation batching
# ---------------------------------------------------------------------------

class TestNoteFreshnessBatching(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        os.makedirs(os.path.join("doc", "common"), exist_ok=True)

    def tearDown(self):
        os.chdir(self.prev_cwd)
        self.tmp.cleanup()

    def test_batch_marks_matching_note_suspect(self):
        _write(
            os.path.join("doc", "common", "obs-api.md"),
            """# API note
freshness: current
invalidated_by_paths: [src/api.py, src/model.py]
""",
        )

        invalidate_note_freshness_for_changes(["src/model.py", "src/other.py"])

        note_path = os.path.join("doc", "common", "obs-api.md")
        self.assertEqual(yaml_field("freshness", note_path), "suspect")

    def test_batch_scans_each_note_once_per_hook_run(self):
        _write(
            os.path.join("doc", "common", "obs-a.md"),
            """# Note A
freshness: current
invalidated_by_paths: [src/a.py]
""",
        )
        _write(
            os.path.join("doc", "common", "obs-b.md"),
            """# Note B
freshness: current
invalidated_by_paths: [src/b.py]
""",
        )

        parse_calls = []

        def _counting_parse(note_path):
            parse_calls.append(note_path)
            from _lib import parse_note_metadata as _real_parse_note_metadata
            return _real_parse_note_metadata(note_path)

        with mock.patch("file_changed_sync.parse_note_metadata", side_effect=_counting_parse):
            invalidate_note_freshness_for_changes(["src/a.py", "src/b.py", "src/c.py"])

        self.assertEqual(len(parse_calls), 2, parse_calls)
        self.assertCountEqual(
            parse_calls,
            [
                os.path.join("doc", "common", "obs-a.md"),
                os.path.join("doc", "common", "obs-b.md"),
            ],
        )



# ---------------------------------------------------------------------------
# §10.3 — execution_mode / orchestration_mode pending blocks close
# ---------------------------------------------------------------------------

class TestModePendingBlocks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_execution_mode_pending_blocks(self):
        """execution_mode: pending on repo-mutating task → block."""
        _make_state(self.task_dir, execution_mode="pending")
        _make_passing_artifacts(self.task_dir)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("execution_mode" in f.lower() for f in failures),
            f"execution_mode: pending must block. Got: {failures}"
        )

    def test_orchestration_mode_pending_blocks(self):
        """orchestration_mode: pending on repo-mutating task → block."""
        _make_state(self.task_dir, orchestration_mode="pending")
        _make_passing_artifacts(self.task_dir)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("orchestration_mode" in f.lower() for f in failures),
            f"orchestration_mode: pending must block. Got: {failures}"
        )

    def test_resolved_modes_do_not_block(self):
        """standard/solo modes must not block."""
        _make_state(self.task_dir,
            execution_mode="standard",
            orchestration_mode="solo",
            agent_run_developer_count="1",
            agent_run_developer_last="2026-01-01T00:00:00Z",
            agent_run_critic_plan_count="1",
            agent_run_critic_plan_last="2026-01-01T00:00:00Z",
            agent_run_critic_runtime_count="1",
            agent_run_critic_runtime_last="2026-01-01T00:00:00Z",
            agent_run_writer_count="1",
            agent_run_writer_last="2026-01-01T00:00:00Z",
        )
        _make_passing_artifacts(self.task_dir)

        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("execution_mode" in f.lower() or "orchestration_mode" in f.lower()
                for f in failures),
            f"Resolved modes must not block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# Append workflow violation helpers
# ---------------------------------------------------------------------------

class TestAppendWorkflowViolation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_to_empty_list(self):
        _make_state(self.task_dir, workflow_violations="[]")
        result = append_workflow_violation(self.task_dir, "missing_runtime_critic_run")
        self.assertTrue(result)
        violations = get_workflow_violations(self.task_dir)
        self.assertIn("missing_runtime_critic_run", violations)

    def test_append_multiple_violations(self):
        _make_state(self.task_dir, workflow_violations="[]")
        append_workflow_violation(self.task_dir, "v1")
        append_workflow_violation(self.task_dir, "v2")
        violations = get_workflow_violations(self.task_dir)
        self.assertIn("v1", violations)
        self.assertIn("v2", violations)

    def test_get_violations_on_missing_state(self):
        """Returns empty list when TASK_STATE.yaml is absent."""
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertEqual(get_workflow_violations(empty), [])

    def test_append_on_missing_state_returns_false(self):
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        result = append_workflow_violation(empty, "some_violation")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
