"""Tests for WS-1: CHECKS.yaml strict close gate (close_gate: strict_high_risk).

Covers:
  - close_gate absent + implemented_candidate → no block (legacy behavior)
  - close_gate: standard + implemented_candidate → no block
  - close_gate: strict_high_risk + implemented_candidate → block
  - close_gate: strict_high_risk + planned → block
  - close_gate: strict_high_risk + blocked → block
  - close_gate: strict_high_risk + all passed → no block
  - close_gate: strict_high_risk + multiple non-passed → single grouped failure
  - close_gate: strict_high_risk + failed → block (subsumes standard behavior)
  - should_set_strict_close_gate() policy helper
  - parse_checks_close_gate() parser

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from task_completed_gate import compute_completion_failures, _parse_checks_yaml
from _lib import parse_checks_close_gate, should_set_strict_close_gate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_non_mutating_passing_task(task_dir):
    """Minimal passing non-mutating task (no runtime/doc requirements)."""
    _write(os.path.join(task_dir, "TASK_STATE.yaml"),
        "task_id: TASK__test\n"
        "status: implemented\n"
        "mutates_repo: false\n"
        "plan_verdict: PASS\n"
        "runtime_verdict: PASS\n"
        "document_verdict: skipped\n"
        "execution_mode: standard\n"
        "orchestration_mode: solo\n"
        "workflow_violations: []\n"
    )
    _write(os.path.join(task_dir, "PLAN.md"), "# Plan\n")
    _write(os.path.join(task_dir, "CRITIC__plan.md"), "verdict: PASS\n")
    _write(os.path.join(task_dir, "HANDOFF.md"),
           "# Handoff\n## Result\nfrom: dev\nscope: t\nchanges: x\nverification_inputs: y\nblockers: none\nnext_action: done\n")


def _has_strict_gate_failure(failures):
    """Check if any failure message is from the strict close gate."""
    return any("STRICT CLOSE GATE" in f or "strict_high_risk" in f for f in failures)


def _has_checks_failure(failures):
    """Check if any failure mentions CHECKS.yaml."""
    return any("CHECKS.yaml" in f for f in failures)


# ---------------------------------------------------------------------------
# parse_checks_close_gate unit tests
# ---------------------------------------------------------------------------

class TestParseChecksCloseGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_returns_standard(self):
        result = parse_checks_close_gate("/nonexistent/CHECKS.yaml")
        self.assertEqual(result, "standard")

    def test_no_close_gate_field_returns_standard(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, 'checks:\n  - id: AC-001\n    status: passed\n')
        result = parse_checks_close_gate(path)
        self.assertEqual(result, "standard")

    def test_close_gate_standard(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, 'close_gate: standard\nchecks:\n  - id: AC-001\n    status: passed\n')
        result = parse_checks_close_gate(path)
        self.assertEqual(result, "standard")

    def test_close_gate_strict_high_risk(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, 'close_gate: strict_high_risk\nchecks:\n  - id: AC-001\n    status: passed\n')
        result = parse_checks_close_gate(path)
        self.assertEqual(result, "strict_high_risk")

    def test_unknown_value_returns_standard(self):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, 'close_gate: something_else\nchecks:\n')
        result = parse_checks_close_gate(path)
        self.assertEqual(result, "standard")


# ---------------------------------------------------------------------------
# should_set_strict_close_gate policy tests
# ---------------------------------------------------------------------------

class TestShouldSetStrictCloseGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sprinted_mode_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: sprinted\n"
            "review_overlays: []\n"
            "risk_tags: []\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_security_overlay_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: [security]\n"
            "risk_tags: []\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_performance_overlay_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: [performance]\n"
            "risk_tags: []\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_structural_risk_tag_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: []\n"
            "risk_tags: [structural]\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_migration_risk_tag_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: []\n"
            "risk_tags: [migration]\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_schema_risk_tag_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: []\n"
            "risk_tags: [schema]\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_cross_root_risk_tag_triggers_strict(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: []\n"
            "risk_tags: [cross-root]\n"
        )
        self.assertTrue(should_set_strict_close_gate(path))

    def test_standard_mode_no_overlays_no_risk_tags(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: []\n"
            "risk_tags: []\n"
        )
        self.assertFalse(should_set_strict_close_gate(path))

    def test_light_mode_no_overlays(self):
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: light\n"
            "review_overlays: []\n"
            "risk_tags: []\n"
        )
        self.assertFalse(should_set_strict_close_gate(path))

    def test_missing_file_returns_false(self):
        self.assertFalse(should_set_strict_close_gate("/nonexistent"))

    def test_frontend_refactor_overlay_does_not_trigger(self):
        """frontend-refactor is not in the strict trigger list."""
        path = os.path.join(self.tmp.name, "TASK_STATE.yaml")
        _write(path,
            "execution_mode: standard\n"
            "review_overlays: [frontend-refactor]\n"
            "risk_tags: []\n"
        )
        self.assertFalse(should_set_strict_close_gate(path))


# ---------------------------------------------------------------------------
# Strict close gate integration in compute_completion_failures
# ---------------------------------------------------------------------------

class TestStrictCloseGateBlocking(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    # --- Legacy / standard behavior preserved ---

    def test_no_close_gate_implemented_candidate_no_block(self):
        """close_gate absent + implemented_candidate → no block (legacy)."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(_has_strict_gate_failure(failures),
            f"Legacy: no close_gate + implemented_candidate must not block. Got: {failures}")
        self.assertFalse(_has_checks_failure(failures),
            f"Legacy: implemented_candidate must not trigger any CHECKS blocking. Got: {failures}")

    def test_close_gate_standard_implemented_candidate_no_block(self):
        """close_gate: standard + implemented_candidate → no block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: standard\nchecks:\n  - id: AC-001\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(_has_strict_gate_failure(failures),
            f"Standard gate + implemented_candidate must not block. Got: {failures}")

    def test_close_gate_standard_failed_blocks(self):
        """close_gate: standard + failed → block (existing behavior preserved)."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: standard\nchecks:\n  - id: AC-001\n    title: "bad"\n    status: failed\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_checks_failure(failures),
            f"Standard gate + failed must block. Got: {failures}")

    def test_high_risk_state_without_close_gate_promotes_to_strict(self):
        """sprinted / high-risk task should enforce strict semantics even if CHECKS.yaml omits close_gate."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"),
            "task_id: TASK__test\n"
            "status: implemented\n"
            "mutates_repo: false\n"
            "plan_verdict: PASS\n"
            "runtime_verdict: PASS\n"
            "document_verdict: skipped\n"
            "execution_mode: sprinted\n"
            "orchestration_mode: solo\n"
            "review_overlays: []\n"
            "risk_tags: []\n"
            "workflow_violations: []\n"
        )
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'checks:\n  - id: AC-001\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures), failures)

    def test_high_risk_state_overrides_standard_close_gate(self):
        """Explicit standard gate does not weaken high-risk close behavior."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "TASK_STATE.yaml"),
            "task_id: TASK__test\n"
            "status: implemented\n"
            "mutates_repo: false\n"
            "plan_verdict: PASS\n"
            "runtime_verdict: PASS\n"
            "document_verdict: skipped\n"
            "execution_mode: standard\n"
            "orchestration_mode: solo\n"
            "review_overlays: [performance]\n"
            "risk_tags: []\n"
            "workflow_violations: []\n"
        )
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: standard\nchecks:\n  - id: AC-001\n    title: "candidate"\n    status: planned\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures), failures)

    # --- Strict gate blocking ---

    def test_strict_gate_implemented_candidate_blocks(self):
        """close_gate: strict_high_risk + implemented_candidate → block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n  - id: AC-001\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures),
            f"Strict gate + implemented_candidate must block. Got: {failures}")

    def test_strict_gate_planned_blocks(self):
        """close_gate: strict_high_risk + planned → block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n  - id: AC-001\n    title: "pending"\n    status: planned\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures),
            f"Strict gate + planned must block. Got: {failures}")

    def test_strict_gate_blocked_blocks(self):
        """close_gate: strict_high_risk + blocked → block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n  - id: AC-001\n    title: "env issue"\n    status: blocked\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures),
            f"Strict gate + blocked must block. Got: {failures}")

    def test_strict_gate_failed_blocks(self):
        """close_gate: strict_high_risk + failed → block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n  - id: AC-001\n    title: "bad"\n    status: failed\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(_has_strict_gate_failure(failures),
            f"Strict gate + failed must block. Got: {failures}")

    def test_strict_gate_all_passed_no_block(self):
        """close_gate: strict_high_risk + all passed → no block."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n'
            '  - id: AC-001\n    title: "ok"\n    status: passed\n'
            '  - id: AC-002\n    title: "also ok"\n    status: passed\n')
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(_has_strict_gate_failure(failures),
            f"Strict gate + all passed must not block. Got: {failures}")
        self.assertFalse(_has_checks_failure(failures),
            f"Strict gate + all passed must not trigger any CHECKS failure. Got: {failures}")

    def test_strict_gate_multiple_non_passed_single_grouped_failure(self):
        """Multiple non-passed criteria → single grouped failure entry."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n'
            '  - id: AC-001\n    title: "failed one"\n    status: failed\n'
            '  - id: AC-002\n    title: "ok"\n    status: passed\n'
            '  - id: AC-003\n    title: "still planned"\n    status: planned\n'
            '  - id: AC-004\n    title: "candidate"\n    status: implemented_candidate\n')
        failures = compute_completion_failures(self.task_dir)
        strict_failures = [f for f in failures if "STRICT CLOSE GATE" in f]
        self.assertEqual(len(strict_failures), 1,
            f"Should be single grouped STRICT failure entry. Got: {strict_failures}")
        # Verify all non-passed IDs are mentioned
        self.assertIn("AC-001", strict_failures[0])
        self.assertIn("AC-003", strict_failures[0])
        self.assertIn("AC-004", strict_failures[0])
        # AC-002 is passed — should not be in the failure
        self.assertNotIn("AC-002", strict_failures[0])

    def test_strict_gate_message_groups_by_status(self):
        """Verify the failure message groups criteria by status."""
        _make_non_mutating_passing_task(self.task_dir)
        _write(os.path.join(self.task_dir, "CHECKS.yaml"),
            'close_gate: strict_high_risk\nchecks:\n'
            '  - id: AC-001\n    title: "f"\n    status: failed\n'
            '  - id: AC-002\n    title: "p"\n    status: planned\n'
            '  - id: AC-003\n    title: "b"\n    status: blocked\n')
        failures = compute_completion_failures(self.task_dir)
        strict_failures = [f for f in failures if "STRICT CLOSE GATE" in f]
        self.assertEqual(len(strict_failures), 1)
        msg = strict_failures[0]
        self.assertIn("[failed]", msg)
        self.assertIn("[planned]", msg)
        self.assertIn("[blocked]", msg)

    # --- Backward compatibility ---

    def test_no_checks_file_no_strict_block(self):
        """No CHECKS.yaml → no strict gate blocking."""
        _make_non_mutating_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(_has_strict_gate_failure(failures),
            f"No CHECKS.yaml must not trigger strict gate. Got: {failures}")


if __name__ == "__main__":
    unittest.main()
