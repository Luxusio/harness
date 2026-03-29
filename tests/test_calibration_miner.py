"""Tests for WS-3: calibration mining (calibration_miner.py).

Run with: python -m unittest discover -s tests -p 'test_*.py'
No external deps — stdlib only.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from calibration_miner import (
    find_calibration_candidates,
    mine_calibration_case,
    run_mining,
    count_candidates,
    MIN_REOPEN_COUNT,
    MIN_FAIL_COUNT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _make_task(base_dir, task_id, status="implemented",
               reopen_count=0, fail_count=None):
    """Create a minimal task directory with optional CHECKS.yaml and CRITIC__runtime.md."""
    task_dir = os.path.join(base_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)

    state = (
        f"task_id: {task_id}\n"
        f"status: {status}\n"
        "touched_paths: []\n"
    )
    if fail_count is not None:
        state += f"runtime_verdict_fail_count: {fail_count}\n"
    _write(os.path.join(task_dir, "TASK_STATE.yaml"), state)

    if reopen_count > 0:
        checks = (
            "checks:\n"
            "  - id: AC-001\n"
            "    title: Main criterion\n"
            "    status: failed\n"
            f"    reopen_count: {reopen_count}\n"
        )
        _write(os.path.join(task_dir, "CHECKS.yaml"), checks)

    return task_dir


def _make_critic_runtime(task_dir, verdict="FAIL", unmet="test assertion failed"):
    content = (
        f"verdict: {verdict}\n"
        f"task_id: {os.path.basename(task_dir)}\n"
        f"unmet_acceptance: {unmet}\n"
        "evidence: some evidence\n"
    )
    _write(os.path.join(task_dir, "CRITIC__runtime.md"), content)


# ---------------------------------------------------------------------------
# find_calibration_candidates tests
# ---------------------------------------------------------------------------

class TestFindCalibrationCandidates(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_reopen_count_qualifies(self):
        """Task with reopen_count >= MIN_REOPEN_COUNT qualifies."""
        _make_task(self.tasks_dir, "TASK__reo", reopen_count=MIN_REOPEN_COUNT)
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 1)

    def test_fail_count_qualifies(self):
        """Task with stored runtime_verdict_fail_count >= MIN_FAIL_COUNT qualifies."""
        _make_task(self.tasks_dir, "TASK__fail", fail_count=MIN_FAIL_COUNT)
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 1)

    def test_below_threshold_does_not_qualify(self):
        """Task with reopen_count < MIN_REOPEN_COUNT and fail_count < MIN_FAIL_COUNT."""
        _make_task(self.tasks_dir, "TASK__low", reopen_count=MIN_REOPEN_COUNT - 1)
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 0)

    def test_closed_task_excluded(self):
        """Closed tasks are not included."""
        _make_task(self.tasks_dir, "TASK__closed", status="closed",
                   reopen_count=MIN_REOPEN_COUNT)
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 0)

    def test_archived_task_excluded(self):
        _make_task(self.tasks_dir, "TASK__arch", status="archived",
                   fail_count=MIN_FAIL_COUNT)
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 0)

    def test_empty_tasks_dir_returns_empty(self):
        candidates = find_calibration_candidates(
            os.path.join(self.tasks_dir, "nonexistent")
        )
        self.assertEqual(candidates, [])

    def test_multiple_qualifying_tasks(self):
        _make_task(self.tasks_dir, "TASK__a", reopen_count=MIN_REOPEN_COUNT)
        _make_task(self.tasks_dir, "TASK__b", fail_count=MIN_FAIL_COUNT)
        _make_task(self.tasks_dir, "TASK__c")  # no qualifiers
        candidates = find_calibration_candidates(self.tasks_dir)
        self.assertEqual(len(candidates), 2)


# ---------------------------------------------------------------------------
# mine_calibration_case tests
# ---------------------------------------------------------------------------

class TestMineCalibrationCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = os.path.join(self.tmp.name, "tasks")
        self.output_dir = os.path.join(self.tmp.name, "calibration")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reopen_count_generates_case(self):
        """reopen_count >= MIN_REOPEN_COUNT → case is generated."""
        task_dir = _make_task(self.tasks_dir, "TASK__reo", reopen_count=MIN_REOPEN_COUNT)
        case = mine_calibration_case(task_dir, output_dir=self.output_dir)

        self.assertIsNotNone(case)
        self.assertIn("reopen_count", case["trigger"])
        self.assertEqual(case["task_id"], "TASK__reo")

        # File must exist
        out_path = os.path.join(self.output_dir, f"{case['slug']}.md")
        self.assertTrue(os.path.isfile(out_path))

    def test_fail_count_generates_case(self):
        """Stored fail count >= MIN_FAIL_COUNT → case is generated."""
        task_dir = _make_task(self.tasks_dir, "TASK__fail", fail_count=MIN_FAIL_COUNT)
        _make_critic_runtime(task_dir)
        case = mine_calibration_case(task_dir, output_dir=self.output_dir)

        self.assertIsNotNone(case)
        self.assertIn("runtime_fail_count", case["trigger"])

        out_path = os.path.join(self.output_dir, f"{case['slug']}.md")
        self.assertTrue(os.path.isfile(out_path))

    def test_no_candidate_returns_none(self):
        """Task below both thresholds → returns None."""
        task_dir = _make_task(self.tasks_dir, "TASK__ok")
        result = mine_calibration_case(task_dir, output_dir=self.output_dir)
        self.assertIsNone(result)

    def test_dry_run_does_not_write(self):
        """write=False → case returned but no file written."""
        task_dir = _make_task(self.tasks_dir, "TASK__dry", reopen_count=MIN_REOPEN_COUNT)
        case = mine_calibration_case(task_dir, output_dir=self.output_dir, write=False)

        self.assertIsNotNone(case)
        # Output dir should NOT be created
        self.assertFalse(os.path.isdir(self.output_dir))

    def test_duplicate_update_not_new_file(self):
        """Running mine twice for the same task updates the existing file, not a new one."""
        task_dir = _make_task(self.tasks_dir, "TASK__dup", reopen_count=MIN_REOPEN_COUNT)

        case1 = mine_calibration_case(task_dir, output_dir=self.output_dir)
        out_path = os.path.join(self.output_dir, f"{case1['slug']}.md")
        mtime1 = os.path.getmtime(out_path)

        # Small sleep to ensure mtime differs
        import time
        time.sleep(0.05)

        case2 = mine_calibration_case(task_dir, output_dir=self.output_dir)
        mtime2 = os.path.getmtime(out_path)

        # Same file (same slug), not a second file
        files = os.listdir(self.output_dir)
        self.assertEqual(len(files), 1)
        # File was updated (mtime changed)
        self.assertGreaterEqual(mtime2, mtime1)
        # Both cases have same slug
        self.assertEqual(case1["slug"], case2["slug"])

    def test_case_content_has_required_sections(self):
        """Generated case file contains pattern_title, trigger, why, what sections."""
        task_dir = _make_task(self.tasks_dir, "TASK__content", reopen_count=MIN_REOPEN_COUNT)
        case = mine_calibration_case(task_dir, output_dir=self.output_dir)

        out_path = os.path.join(self.output_dir, f"{case['slug']}.md")
        with open(out_path) as f:
            content = f.read()

        self.assertIn("pattern_title", content)
        self.assertIn("trigger", content)
        self.assertIn("Why previous PASS was wrong", content)
        self.assertIn("What critic must check next time", content)

    def test_evidence_refs_populated(self):
        """CHECKS.yaml and CRITIC__runtime.md are listed in evidence_refs."""
        task_dir = _make_task(self.tasks_dir, "TASK__ev", reopen_count=MIN_REOPEN_COUNT)
        _make_critic_runtime(task_dir, verdict="FAIL")

        case = mine_calibration_case(task_dir, output_dir=self.output_dir)
        self.assertIn("CHECKS.yaml", case["evidence_refs"])
        self.assertIn("CRITIC__runtime.md", case["evidence_refs"])


# ---------------------------------------------------------------------------
# run_mining tests
# ---------------------------------------------------------------------------

class TestRunMining(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = os.path.join(self.tmp.name, "tasks")
        self.output_dir = os.path.join(self.tmp.name, "calibration")

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_candidates_no_write(self):
        """No qualifying tasks → empty result, no output dir created."""
        _make_task(self.tasks_dir, "TASK__ok")
        cases = run_mining(tasks_dir=self.tasks_dir, output_dir=self.output_dir)
        self.assertEqual(cases, [])
        self.assertFalse(os.path.isdir(self.output_dir))

    def test_qualifying_tasks_produce_cases(self):
        _make_task(self.tasks_dir, "TASK__a", reopen_count=MIN_REOPEN_COUNT)
        _make_task(self.tasks_dir, "TASK__b", fail_count=MIN_FAIL_COUNT)
        cases = run_mining(tasks_dir=self.tasks_dir, output_dir=self.output_dir)
        self.assertEqual(len(cases), 2)

    def test_dry_run_no_writes(self):
        _make_task(self.tasks_dir, "TASK__dry", reopen_count=MIN_REOPEN_COUNT)
        cases = run_mining(tasks_dir=self.tasks_dir, output_dir=self.output_dir, dry_run=True)
        self.assertEqual(len(cases), 1)
        self.assertFalse(os.path.isdir(self.output_dir))


# ---------------------------------------------------------------------------
# count_candidates tests
# ---------------------------------------------------------------------------

class TestCountCandidates(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_zero_when_no_candidates(self):
        _make_task(self.tasks_dir, "TASK__ok")
        self.assertEqual(count_candidates(self.tasks_dir), 0)

    def test_returns_correct_count(self):
        _make_task(self.tasks_dir, "TASK__a", reopen_count=MIN_REOPEN_COUNT)
        _make_task(self.tasks_dir, "TASK__b", fail_count=MIN_FAIL_COUNT)
        _make_task(self.tasks_dir, "TASK__c")
        self.assertEqual(count_candidates(self.tasks_dir), 2)

    def test_empty_dir_returns_zero(self):
        self.assertEqual(
            count_candidates(os.path.join(self.tasks_dir, "nonexistent")), 0
        )


if __name__ == "__main__":
    unittest.main()
