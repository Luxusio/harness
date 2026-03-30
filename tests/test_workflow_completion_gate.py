"""Tests for task completion gate logic — Phase 0 §5.2.

Covers:
  - doc_changes_detected: true + no CRITIC__document.md → block
  - runtime_verdict: pending in TASK_STATE + old PASS text in artifact → block (stale PASS)
  - plan_verdict: pending + PLAN.md exists → block
  - workflow_violations non-empty → block
  - Normal passing task (scenario F) → no failures
  - investigate task without RESULT.md → block
  - directive pending → block
  - workflow_mode=compliant + capability unavailable → block
  - collapsed_approved + compliance_claim != degraded → block
  - critic-plan count 0 → block

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from task_completed_gate import compute_completion_failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _make_passing_task(task_dir):
    """Write all required artifacts for a passing repo-mutating task."""
    _write(os.path.join(task_dir, "TASK_STATE.yaml"),
        "task_id: TASK__test\n"
        "status: implemented\n"
        "lane: build\n"
        "mutates_repo: true\n"
        "plan_verdict: PASS\n"
        "runtime_verdict: PASS\n"
        "document_verdict: skipped\n"
        "doc_changes_detected: false\n"
        "execution_mode: standard\n"
        "orchestration_mode: solo\n"
        "workflow_violations: []\n"
        "workflow_mode: compliant\n"
        "compliance_claim: strict\n"
        "artifact_provenance_required: false\n"
        "result_required: false\n"
        "capability_delegation: available\n"
        "collapsed_mode_approved: false\n"
        "directive_capture_state: clean\n"
        "agent_run_developer_count: 1\n"
        "agent_run_developer_last: 2026-01-01T00:00:00Z\n"
        "agent_run_critic_plan_count: 1\n"
        "agent_run_critic_plan_last: 2026-01-01T00:00:00Z\n"
        "agent_run_critic_runtime_count: 1\n"
        "agent_run_critic_runtime_last: 2026-01-01T00:00:00Z\n"
        "agent_run_writer_count: 1\n"
        "agent_run_writer_last: 2026-01-01T00:00:00Z\n"
        "agent_run_critic_document_count: 0\n"
        "agent_run_critic_document_last: null\n"
        "touched_paths: [\"plugin/scripts/foo.py\"]\n"
        "roots_touched: [\"plugin\"]\n"
        "verification_targets: [\"plugin/scripts/foo.py\"]\n"
        "blockers: []\n"
        "updated: 2026-01-01T00:00:00Z\n"
    )
    _write(os.path.join(task_dir, "PLAN.md"), "# Plan\nscope: test\n")
    _write(os.path.join(task_dir, "CRITIC__plan.md"), "verdict: PASS\n")
    _write(os.path.join(task_dir, "HANDOFF.md"),
           "# Handoff\n## Current state\nDone.\n## Verification\nTests pass.\n")
    _write(os.path.join(task_dir, "DOC_SYNC.md"), "none\n")
    _write(os.path.join(task_dir, "CRITIC__runtime.md"),
           "verdict: PASS\n## Evidence Bundle\n### Command Transcript\n$ pytest\nPASSED\n")


# ---------------------------------------------------------------------------
# Scenario F — normal passing task
# ---------------------------------------------------------------------------

class TestPassingTask(unittest.TestCase):
    """Scenario F: fully correct task must produce zero failures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_passing_task_has_no_failures(self):
        _make_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertEqual(failures, [],
            f"Passing task must have no failures, got: {failures}")


# ---------------------------------------------------------------------------
# §5.2.1 — doc_changes_detected: true without CRITIC__document.md
# ---------------------------------------------------------------------------

class TestDocCriticRequired(unittest.TestCase):
    """Scenario A: doc_changes_detected: true but CRITIC__document.md absent → block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_doc_changes_without_critic_blocks(self):
        _make_passing_task(self.task_dir)
        # Flip doc_changes_detected to true
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "doc_changes_detected: false", "doc_changes_detected: true"
        ).replace(
            "document_verdict: skipped", "document_verdict: pending"
        )
        with open(state_file, "w") as f:
            f.write(content)
        # No CRITIC__document.md

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("document" in f.lower() for f in failures),
            f"Must block: doc_changes_detected=true, no CRITIC__document.md. Got: {failures}"
        )

    def test_doc_sync_with_content_triggers_critic_requirement(self):
        """DOC_SYNC.md with real content (not 'none') also requires document critic."""
        _make_passing_task(self.task_dir)
        # Override DOC_SYNC.md with real content
        _write(os.path.join(self.task_dir, "DOC_SYNC.md"),
               "# DOC_SYNC\n## Changed\n- Updated doc/common/api.md\n")
        # Set document_verdict to pending
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("document_verdict: skipped", "document_verdict: pending")
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("document" in f.lower() for f in failures),
            f"Must block when DOC_SYNC.md has real content and no document critic. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# §5.2.2 — stale PASS: runtime_verdict pending + artifact has old PASS text
# ---------------------------------------------------------------------------

class TestStalePASS(unittest.TestCase):
    """Scenario B: YAML verdict is pending but artifact still has 'verdict: PASS' text → block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_runtime_pass_is_blocked(self):
        _make_passing_task(self.task_dir)
        # Simulate file_changed_sync resetting YAML verdict to pending
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("runtime_verdict: PASS", "runtime_verdict: pending")
        with open(state_file, "w") as f:
            f.write(content)
        # CRITIC__runtime.md still has old PASS text — the "stale" case

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("runtime" in f.lower() for f in failures),
            f"Stale runtime PASS must be blocked. YAML says pending, artifact says PASS. Got: {failures}"
        )

    def test_stale_document_pass_is_blocked(self):
        _make_passing_task(self.task_dir)
        # Add CRITIC__document.md with PASS text
        _write(os.path.join(self.task_dir, "CRITIC__document.md"), "verdict: PASS\n")
        # Set doc_changes_detected: true and document_verdict: pending in YAML
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = (
            content
            .replace("doc_changes_detected: false", "doc_changes_detected: true")
            .replace("document_verdict: skipped", "document_verdict: pending")
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("document" in f.lower() for f in failures),
            f"Stale document PASS must be blocked. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# §5.2.3 — plan_verdict: pending with PLAN.md present → block
# ---------------------------------------------------------------------------

class TestPlanVerdictPending(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_verdict_pending_blocks(self):
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("plan_verdict: PASS", "plan_verdict: pending")
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("plan" in f.lower() for f in failures),
            f"plan_verdict: pending must block even if PLAN.md + CRITIC__plan.md exist. Got: {failures}"
        )

    def test_missing_plan_blocks(self):
        _make_passing_task(self.task_dir)
        os.remove(os.path.join(self.task_dir, "PLAN.md"))

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("plan" in f.lower() for f in failures),
            f"Missing PLAN.md must block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# §5.2.4 — workflow_violations non-empty → block
# ---------------------------------------------------------------------------

class TestWorkflowViolations(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_workflow_violation_blocks(self):
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "workflow_violations: []",
            'workflow_violations: ["source_mutation_before_plan_pass"]'
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("violation" in f.lower() for f in failures),
            f"workflow_violations must block. Got: {failures}"
        )

    def test_empty_violations_does_not_block(self):
        _make_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("violation" in f.lower() for f in failures),
            f"Empty violations must not block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# blocked_env cannot close
# ---------------------------------------------------------------------------

class TestBlockedEnv(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_blocked_env_status_blocks(self):
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("status: implemented", "status: blocked_env")
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("blocked_env" in f.lower() for f in failures),
            f"blocked_env must block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# HANDOFF stub detection
# ---------------------------------------------------------------------------

class TestHandoffStub(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unfilled_stub_blocks(self):
        """HANDOFF.md with only 'status: pending' stub must block."""
        _make_passing_task(self.task_dir)
        # Overwrite with a stub (as created by task_created_gate)
        _write(os.path.join(self.task_dir, "HANDOFF.md"),
               "# Handoff: TASK__test\nstatus: pending\nupdated: 2026-01-01T00:00:00Z\n")

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("handoff" in f.lower() or "stub" in f.lower() for f in failures),
            f"Unfilled HANDOFF stub must block. Got: {failures}"
        )

    def test_filled_handoff_passes(self):
        """Filled HANDOFF.md must not be flagged as stub."""
        _make_passing_task(self.task_dir)
        # Already has a good HANDOFF in _make_passing_task
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("stub" in f.lower() for f in failures),
            f"Filled HANDOFF must not be flagged as stub. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# Investigate lane: RESULT.md required
# ---------------------------------------------------------------------------

class TestInvestigateResult(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_investigate_without_result_blocks(self):
        """investigate lane task missing RESULT.md → block."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("lane: build", "lane: investigate")
        content = content.replace("result_required: false", "result_required: true")
        content = content.replace("mutates_repo: true", "mutates_repo: false")
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("result" in f.lower() and "investigate" in f.lower() for f in failures),
            f"Investigate task without RESULT.md must block. Got: {failures}"
        )

    def test_investigate_with_result_passes(self):
        """investigate lane task with RESULT.md present → no result-related failure."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("lane: build", "lane: investigate")
        content = content.replace("result_required: false", "result_required: true")
        with open(state_file, "w") as f:
            f.write(content)
        _write(os.path.join(self.task_dir, "RESULT.md"), "# Result\nFindings here.\n")

        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("result" in f.lower() and "investigate" in f.lower() for f in failures),
            f"Investigate task with RESULT.md must not block on result. Got: {failures}"
        )

    def test_result_required_true_blocks_without_result(self):
        """result_required: true blocks regardless of lane."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace("result_required: false", "result_required: true")
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("result" in f.lower() for f in failures),
            f"result_required: true without RESULT.md must block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# Directive capture gate
# ---------------------------------------------------------------------------

class TestDirectiveCaptureGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_directive_pending_blocks(self):
        """directive_capture_state=pending → block."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "directive_capture_state: clean",
            "directive_capture_state: pending"
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("directive" in f.lower() for f in failures),
            f"Pending directives must block close. Got: {failures}"
        )

    def test_directive_clean_passes(self):
        """directive_capture_state=clean → no directive-related failure."""
        _make_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("directive" in f.lower() for f in failures),
            f"Clean directive state must not block. Got: {failures}"
        )

    def test_directive_captured_passes(self):
        """directive_capture_state=captured → no directive-related failure."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "directive_capture_state: clean",
            "directive_capture_state: captured"
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("directive" in f.lower() for f in failures),
            f"Captured directive state must not block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# Capability / compliance gate
# ---------------------------------------------------------------------------

class TestCapabilityComplianceGate(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_compliant_with_unavailable_delegation_blocks(self):
        """workflow_mode=compliant + capability_delegation=unavailable → block."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "capability_delegation: available",
            "capability_delegation: unavailable"
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("compliant" in f.lower() and "unavailable" in f.lower() for f in failures),
            f"Compliant mode + unavailable delegation must block. Got: {failures}"
        )

    def test_collapsed_approved_needs_degraded_claim(self):
        """collapsed_approved + compliance_claim=strict → block."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "workflow_mode: compliant",
            "workflow_mode: collapsed_approved"
        )
        # compliance_claim is still strict — should fail
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("collapsed_approved" in f.lower() and "degraded" in f.lower() for f in failures),
            f"Collapsed approved with strict compliance must block. Got: {failures}"
        )

    def test_collapsed_approved_with_degraded_passes(self):
        """collapsed_approved + compliance_claim=degraded + approved=true → no capability failure."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "workflow_mode: compliant",
            "workflow_mode: collapsed_approved"
        ).replace(
            "compliance_claim: strict",
            "compliance_claim: degraded"
        ).replace(
            "collapsed_mode_approved: false",
            "collapsed_mode_approved: true"
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("collapsed" in f.lower() for f in failures),
            f"Properly approved collapsed mode must not block. Got: {failures}"
        )

    def test_compliant_with_available_delegation_passes(self):
        """workflow_mode=compliant + capability_delegation=available → no failure."""
        _make_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("capability" in f.lower() or "delegation" in f.lower() for f in failures),
            f"Available delegation should not trigger capability block. Got: {failures}"
        )


# ---------------------------------------------------------------------------
# Critic-plan count hard requirement
# ---------------------------------------------------------------------------

class TestCriticPlanHardRequirement(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = os.path.join(self.tmp.name, "TASK__test")
        os.makedirs(self.task_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_critic_plan_count_zero_blocks(self):
        """critic-plan run count = 0 → block."""
        _make_passing_task(self.task_dir)
        state_file = os.path.join(self.task_dir, "TASK_STATE.yaml")
        with open(state_file) as f:
            content = f.read()
        content = content.replace(
            "agent_run_critic_plan_count: 1",
            "agent_run_critic_plan_count: 0"
        )
        with open(state_file, "w") as f:
            f.write(content)

        failures = compute_completion_failures(self.task_dir)
        self.assertTrue(
            any("critic-plan" in f.lower() or "critic_plan" in f.lower() for f in failures),
            f"critic-plan count=0 must block. Got: {failures}"
        )

    def test_critic_plan_count_nonzero_passes(self):
        """critic-plan run count >= 1 → no failure."""
        _make_passing_task(self.task_dir)
        failures = compute_completion_failures(self.task_dir)
        self.assertFalse(
            any("critic-plan" in f.lower() and "no recorded" in f.lower() for f in failures),
            f"critic-plan count=1 must not block. Got: {failures}"
        )


if __name__ == "__main__":
    unittest.main()
