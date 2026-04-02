#!/usr/bin/env python3
"""Tests for prompt_memory.py — complaint summary and reminder injection."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"
os.environ["HARNESS_SKIP_PREREAD"] = "1"

import prompt_memory as prompt_memory_module
from prompt_memory import _get_complaint_summary, _is_complaint_like, is_casual


class TestGetComplaintSummary(unittest.TestCase):

    def _make_task_dir_with_complaints(self, open_count=1, resolved_count=0):
        d = tempfile.mkdtemp()
        # Write TASK_STATE.yaml
        with open(os.path.join(d, "TASK_STATE.yaml"), "w") as f:
            f.write("task_id: TASK__test\nstatus: planned\n")

        complaints = ["complaints:"]
        for i in range(open_count):
            complaints.append(f"  - id: cmp_open{i:03d}")
            complaints.append("    status: open")
            complaints.append("    kind: outcome_fail")
            complaints.append("    lane: objective")
            complaints.append("    scope: task")
            complaints.append(f"    text: \"complaint {i}\"")
            complaints.append("    captured_at: \"2026-03-31T00:00:00Z\"")
            complaints.append("    source_prompt_ref: user_prompt")
            complaints.append("    related_check_ids: []")
            complaints.append("    blocks_close: true")
            complaints.append("    calibration_candidate: false")
            complaints.append("    promoted_note_path: null")
            complaints.append("    promoted_directive_id: null")
            complaints.append("    evidence_refs: []")
            complaints.append("    resolution: \"\"")
        for i in range(resolved_count):
            complaints.append(f"  - id: cmp_resolved{i:03d}")
            complaints.append("    status: resolved")
            complaints.append("    kind: outcome_fail")
            complaints.append("    lane: objective")
            complaints.append("    scope: task")
            complaints.append(f"    text: \"resolved {i}\"")
            complaints.append("    captured_at: \"2026-03-31T00:00:00Z\"")
            complaints.append("    source_prompt_ref: user_prompt")
            complaints.append("    related_check_ids: []")
            complaints.append("    blocks_close: true")
            complaints.append("    calibration_candidate: false")
            complaints.append("    promoted_note_path: null")
            complaints.append("    promoted_directive_id: null")
            complaints.append("    evidence_refs: []")
            complaints.append("    resolution: \"fixed\"")

        with open(os.path.join(d, "COMPLAINTS.yaml"), "w") as f:
            f.write("\n".join(complaints) + "\n")
        return d

    def test_returns_summary_when_open_complaints(self):
        d = self._make_task_dir_with_complaints(open_count=1)
        summary = _get_complaint_summary(d)
        self.assertTrue(summary.startswith("Complaints:"))
        self.assertIn("outcome_fail", summary)

    def test_returns_empty_when_no_open_complaints(self):
        d = self._make_task_dir_with_complaints(open_count=0, resolved_count=2)
        summary = _get_complaint_summary(d)
        self.assertEqual(summary, "")

    def test_returns_empty_for_nonexistent_dir(self):
        summary = _get_complaint_summary("/nonexistent/path/xyz")
        self.assertEqual(summary, "")

    def test_multiple_open_complaints_in_summary(self):
        d = self._make_task_dir_with_complaints(open_count=2)
        summary = _get_complaint_summary(d)
        self.assertIn("|", summary)


class TestIsComplaintLike(unittest.TestCase):

    def test_still_not_working(self):
        self.assertTrue(_is_complaint_like("still not working"))

    def test_still_broken(self):
        self.assertTrue(_is_complaint_like("still broken"))

    def test_again_failure(self):
        self.assertTrue(_is_complaint_like("this is broken again"))

    def test_didnt_fix(self):
        self.assertTrue(_is_complaint_like("you didn't fix it"))

    def test_korean_not_working(self):
        self.assertTrue(_is_complaint_like("아직 안 됨"))

    def test_korean_still(self):
        self.assertTrue(_is_complaint_like("여전히 안 됨"))

    def test_question_not_complaint(self):
        self.assertFalse(_is_complaint_like("how does this work?"))

    def test_long_question_not_complaint(self):
        self.assertFalse(_is_complaint_like("can you explain how the feedback capture system handles deduplication?"))

    def test_neutral_task_not_complaint(self):
        # Short non-question prompts hit the short-prompt heuristic;
        # a long neutral statement without negation signals is not complaint-like
        self.assertFalse(_is_complaint_like("please explain the architecture of the feedback capture system in detail"))


class TestIsCasual(unittest.TestCase):

    def test_hi_is_casual(self):
        self.assertTrue(is_casual("hi"))

    def test_thanks_is_casual(self):
        self.assertTrue(is_casual("thanks"))

    def test_implement_not_casual(self):
        self.assertFalse(is_casual("implement the plan"))

    def test_short_empty_is_casual(self):
        self.assertTrue(is_casual("ok"))


if __name__ == "__main__":
    unittest.main()


class TestSimilarFailureHint(unittest.TestCase):

    def test_gather_context_surfaces_similar_failure_in_fix_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = os.path.join(tmp, "TASK__current")
            previous = os.path.join(tmp, "TASK__previous")
            os.makedirs(current)
            os.makedirs(previous)

            with open(os.path.join(current, "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "task_id: TASK__current\n"
                    "status: implemented\n"
                    "lane: debug\n"
                    "runtime_verdict: FAIL\n"
                    "updated: 2026-04-01T00:00:00Z\n"
                    "verification_targets: [src/api/users.py]\n"
                )
            with open(os.path.join(current, "REQUEST.md"), "w", encoding="utf-8") as f:
                f.write("Fix the users API persistence bug.\n")
            with open(os.path.join(current, "CHECKS.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "checks:\n"
                    "  - id: AC-001\n"
                    "    status: failed\n"
                    "    title: Users API persists updates\n"
                )

            with open(os.path.join(previous, "TASK_STATE.yaml"), "w", encoding="utf-8") as f:
                f.write(
                    "task_id: TASK__previous\n"
                    "status: closed\n"
                    "lane: debug\n"
                    "runtime_verdict_fail_count: 2\n"
                    "updated: 2026-03-01T00:00:00Z\n"
                    "verification_targets: [src/api/users.py]\n"
                )
            with open(os.path.join(previous, "REQUEST.md"), "w", encoding="utf-8") as f:
                f.write("Repair the failing users API save path.\n")
            with open(os.path.join(previous, "CRITIC__runtime.md"), "w", encoding="utf-8") as f:
                f.write(
                    "verdict: FAIL\n"
                    "summary: persistence still fails after reload\n"
                )

            with mock.patch.object(prompt_memory_module, "TASK_DIR", tmp), \
                 mock.patch.object(prompt_memory_module, "select_relevant_notes", return_value=[]):
                parts = prompt_memory_module.gather_context("fix src/api/users.py still broken")

            repair_parts = [part for part in parts if part.startswith("repair:")]
            self.assertTrue(repair_parts, parts)
            self.assertIn("TASK__previous", repair_parts[0])
            self.assertTrue(
                "AC-001" in repair_parts[0] or "src/api/users.py" in repair_parts[0],
                repair_parts[0],
            )
            self.assertLessEqual(len(parts), 4)
