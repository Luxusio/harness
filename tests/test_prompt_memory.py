#!/usr/bin/env python3
"""Tests for prompt_memory.py — complaint summary and reminder injection."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"
os.environ["HARNESS_SKIP_PREREAD"] = "1"

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
