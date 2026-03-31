#!/usr/bin/env python3
"""Tests for feedback_capture.py — complaint staging and management."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from feedback_capture import (
    stage_complaint, get_open_complaints, summarize_open_complaints,
    mark_promoted, mark_resolved, mark_dismissed, _parse_complaints,
)


def _make_task_dir(extra_yaml=""):
    """Create a temp task dir with minimal TASK_STATE.yaml."""
    d = tempfile.mkdtemp()
    state = (
        "task_id: TASK__test\n"
        "status: planned\n"
        "complaint_capture_state: clean\n"
        "pending_complaint_ids: []\n"
        "last_complaint_at: null\n"
        "directive_capture_state: clean\n"
        "pending_directive_ids: []\n"
    )
    if extra_yaml:
        state += extra_yaml + "\n"
    with open(os.path.join(d, "TASK_STATE.yaml"), "w") as f:
        f.write(state)
    return d


class TestStageComplaint(unittest.TestCase):

    def test_creates_complaints_yaml(self):
        d = _make_task_dir()
        entry = stage_complaint(d, "still broken", kind="outcome_fail")
        self.assertTrue(os.path.isfile(os.path.join(d, "COMPLAINTS.yaml")))
        self.assertEqual(entry["kind"], "outcome_fail")
        self.assertEqual(entry["status"], "open")
        self.assertIn("cmp_", entry["id"])

    def test_dedupe_same_text_open(self):
        d = _make_task_dir()
        e1 = stage_complaint(d, "same text", kind="outcome_fail")
        e2 = stage_complaint(d, "same text", kind="process_fail")
        # Should update existing, not create new
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertEqual(len(complaints), 1)
        self.assertEqual(complaints[0]["id"], e1["id"])
        self.assertEqual(complaints[0]["kind"], "process_fail")

    def test_dedupe_different_text(self):
        d = _make_task_dir()
        stage_complaint(d, "first complaint")
        stage_complaint(d, "second complaint")
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertEqual(len(complaints), 2)

    def test_dedupe_resolved_creates_new(self):
        d = _make_task_dir()
        e1 = stage_complaint(d, "same text")
        mark_resolved(d, e1["id"])
        e2 = stage_complaint(d, "same text")
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertEqual(len(complaints), 2)
        self.assertNotEqual(e1["id"], e2["id"])

    def test_updates_task_state_pending(self):
        d = _make_task_dir()
        stage_complaint(d, "something wrong", blocks_close=True)
        state_path = os.path.join(d, "TASK_STATE.yaml")
        with open(state_path) as f:
            content = f.read()
        self.assertIn("complaint_capture_state: pending", content)
        self.assertIn("last_complaint_at:", content)

    def test_updates_task_state_clean_after_all_resolved(self):
        d = _make_task_dir()
        e = stage_complaint(d, "issue")
        mark_resolved(d, e["id"])
        # Stage another, then resolve it
        e2 = stage_complaint(d, "another issue")
        mark_resolved(d, e2["id"])
        state_path = os.path.join(d, "TASK_STATE.yaml")
        with open(state_path) as f:
            content = f.read()
        self.assertIn("complaint_capture_state: clean", content)
        self.assertIn("pending_complaint_ids: []", content)

    def test_blocks_close_field_stored(self):
        d = _make_task_dir()
        entry = stage_complaint(d, "minor issue", blocks_close=False)
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertFalse(complaints[0]["blocks_close"])

    def test_calibration_candidate_field_stored(self):
        d = _make_task_dir()
        stage_complaint(d, "false pass issue", kind="false_pass", calibration_candidate=True)
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertTrue(complaints[0]["calibration_candidate"])

    def test_related_check_ids_stored(self):
        d = _make_task_dir()
        stage_complaint(d, "ac fail", related_check_ids=["AC-001", "AC-003"])
        complaints = _parse_complaints(os.path.join(d, "COMPLAINTS.yaml"))
        self.assertEqual(complaints[0]["related_check_ids"], ["AC-001", "AC-003"])

    def test_no_task_state_does_not_crash(self):
        d = tempfile.mkdtemp()
        # No TASK_STATE.yaml — should not crash
        entry = stage_complaint(d, "test complaint")
        self.assertEqual(entry["status"], "open")


class TestGetOpenComplaints(unittest.TestCase):

    def test_returns_only_open(self):
        d = _make_task_dir()
        e1 = stage_complaint(d, "open one")
        e2 = stage_complaint(d, "also open")
        mark_resolved(d, e2["id"])
        open_list = get_open_complaints(d)
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0]["id"], e1["id"])

    def test_empty_when_no_file(self):
        d = tempfile.mkdtemp()
        self.assertEqual(get_open_complaints(d), [])

    def test_promoted_not_in_open(self):
        d = _make_task_dir()
        e = stage_complaint(d, "promoted one")
        mark_promoted(d, e["id"])
        self.assertEqual(get_open_complaints(d), [])


class TestSummarizeOpenComplaints(unittest.TestCase):

    def test_format_single(self):
        d = _make_task_dir()
        stage_complaint(d, "something broke", kind="outcome_fail", blocks_close=True)
        s = summarize_open_complaints(d)
        self.assertTrue(s.startswith("Complaints:"))
        self.assertIn("outcome_fail", s)
        self.assertIn("blocking", s)

    def test_format_multiple(self):
        d = _make_task_dir()
        stage_complaint(d, "first", kind="outcome_fail")
        stage_complaint(d, "second", kind="process_fail")
        s = summarize_open_complaints(d)
        self.assertIn("|", s)

    def test_empty_when_no_open(self):
        d = _make_task_dir()
        self.assertEqual(summarize_open_complaints(d), "")

    def test_non_blocking_in_summary(self):
        d = _make_task_dir()
        stage_complaint(d, "minor pref", kind="preference_fail", blocks_close=False)
        s = summarize_open_complaints(d)
        self.assertNotIn("blocking", s)


class TestMarkHelpers(unittest.TestCase):

    def test_mark_resolved(self):
        d = _make_task_dir()
        e = stage_complaint(d, "to resolve")
        result = mark_resolved(d, e["id"], resolution="Fixed in v2")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["resolution"], "Fixed in v2")
        # Should no longer be open
        self.assertEqual(get_open_complaints(d), [])

    def test_mark_promoted(self):
        d = _make_task_dir()
        e = stage_complaint(d, "to promote")
        result = mark_promoted(d, e["id"], promoted_note_path="doc/common/REQ__foo.md")
        self.assertEqual(result["status"], "promoted")
        self.assertEqual(result["promoted_note_path"], "doc/common/REQ__foo.md")

    def test_mark_dismissed(self):
        d = _make_task_dir()
        e = stage_complaint(d, "to dismiss")
        result = mark_dismissed(d, e["id"], reason="duplicate")
        self.assertEqual(result["status"], "dismissed")
        self.assertEqual(result["resolution"], "duplicate")

    def test_mark_nonexistent_returns_none(self):
        d = _make_task_dir()
        result = mark_resolved(d, "cmp_nonexistent")
        self.assertIsNone(result)

    def test_mark_promoted_with_directive_id(self):
        d = _make_task_dir()
        e = stage_complaint(d, "process complaint")
        result = mark_promoted(d, e["id"], promoted_directive_id="DIR-42")
        self.assertEqual(result["promoted_directive_id"], "DIR-42")


if __name__ == "__main__":
    unittest.main()
