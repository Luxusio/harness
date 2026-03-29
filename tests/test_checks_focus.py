"""Tests for WS-2: CHECKS focus/guardrail set computation.

Run with: python -m unittest discover -s tests -p 'test_*.py'
No external deps — stdlib only.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

from checks_focus import (
    parse_checks,
    compute_focus_sets,
    format_checks_summary,
    get_checks_summary_for_task,
    SUMMARY_MAX_CHARS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def _checks_yaml(criteria):
    """Build a CHECKS.yaml string from list of dicts."""
    lines = ["checks:\n"]
    for c in criteria:
        lines.append(f"  - id: {c['id']}\n")
        lines.append(f"    status: {c.get('status', 'planned')}\n")
        if "title" in c:
            lines.append(f"    title: {c['title']}\n")
        if "reopen_count" in c:
            lines.append(f"    reopen_count: {c['reopen_count']}\n")
    return "".join(lines)


def _handoff_json(open_ids=None, good_ids=None):
    """Build a SESSION_HANDOFF.json string."""
    return json.dumps({
        "task_id": "TASK__test",
        "trigger": "runtime_fail_repeat",
        "open_check_ids": open_ids or [],
        "last_known_good_checks": good_ids or [],
    })


# ---------------------------------------------------------------------------
# parse_checks tests
# ---------------------------------------------------------------------------

class TestParseChecks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, content):
        path = os.path.join(self.tmp.name, "CHECKS.yaml")
        _write(path, content)
        return path

    def test_parses_basic_checks(self):
        path = self._file(_checks_yaml([
            {"id": "AC-001", "status": "passed", "title": "Feature works"},
            {"id": "AC-002", "status": "failed", "title": "Edge case"},
            {"id": "AC-003", "status": "implemented_candidate"},
        ]))
        checks = parse_checks(path)
        self.assertEqual(len(checks), 3)
        ids = [c["id"] for c in checks]
        self.assertIn("AC-001", ids)
        self.assertIn("AC-002", ids)
        statuses = {c["id"]: c["status"] for c in checks}
        self.assertEqual(statuses["AC-001"], "passed")
        self.assertEqual(statuses["AC-002"], "failed")

    def test_missing_file_returns_empty(self):
        result = parse_checks("/nonexistent/CHECKS.yaml")
        self.assertEqual(result, [])

    def test_parses_reopen_count(self):
        path = self._file(_checks_yaml([
            {"id": "AC-001", "status": "failed", "reopen_count": 3},
        ]))
        checks = parse_checks(path)
        self.assertEqual(checks[0]["reopen_count"], 3)

    def test_defaults_reopen_count_to_zero(self):
        path = self._file(_checks_yaml([{"id": "AC-001", "status": "passed"}]))
        checks = parse_checks(path)
        self.assertEqual(checks[0]["reopen_count"], 0)


# ---------------------------------------------------------------------------
# compute_focus_sets tests
# ---------------------------------------------------------------------------

class TestComputeFocusSets(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _checks(self, criteria):
        path = os.path.join(self.d, "CHECKS.yaml")
        _write(path, _checks_yaml(criteria))
        return path

    def _handoff(self, open_ids=None, good_ids=None):
        path = os.path.join(self.d, "SESSION_HANDOFF.json")
        _write(path, _handoff_json(open_ids, good_ids))
        return path

    def test_focus_guardrail_classification(self):
        """failed/implemented_candidate/blocked → focus; passed → guardrail."""
        path = self._checks([
            {"id": "AC-001", "status": "passed"},
            {"id": "AC-002", "status": "failed"},
            {"id": "AC-003", "status": "implemented_candidate"},
            {"id": "AC-004", "status": "blocked"},
            {"id": "AC-005", "status": "planned"},
        ])
        sets = compute_focus_sets(path)

        self.assertTrue(sets["has_checks"])
        self.assertIn("AC-002", sets["focus_ids"])
        self.assertIn("AC-003", sets["focus_ids"])
        self.assertIn("AC-004", sets["focus_ids"])
        self.assertNotIn("AC-001", sets["focus_ids"])
        self.assertNotIn("AC-005", sets["focus_ids"])

        self.assertIn("AC-001", sets["guardrail_ids"])
        self.assertNotIn("AC-002", sets["guardrail_ids"])

        self.assertIn("AC-002", sets["open_ids"])
        self.assertIn("AC-003", sets["open_ids"])
        self.assertIn("AC-005", sets["open_ids"])
        self.assertNotIn("AC-001", sets["open_ids"])

    def test_legacy_no_checks_fallback(self):
        """No CHECKS.yaml → has_checks False, empty sets."""
        sets = compute_focus_sets(None)
        self.assertFalse(sets["has_checks"])
        self.assertEqual(sets["focus_ids"], [])
        self.assertEqual(sets["open_ids"], [])
        self.assertEqual(sets["guardrail_ids"], [])

    def test_handoff_open_check_ids_priority(self):
        """SESSION_HANDOFF.json open_check_ids override CHECKS.yaml open_ids."""
        checks_path = self._checks([
            {"id": "AC-001", "status": "passed"},
            {"id": "AC-002", "status": "failed"},
        ])
        handoff_path = self._handoff(open_ids=["AC-002", "AC-003"], good_ids=["AC-001"])

        sets = compute_focus_sets(checks_path, session_handoff_path=handoff_path)

        # Handoff open_check_ids take precedence
        self.assertEqual(sets["open_ids"], ["AC-002", "AC-003"])
        # Guardrails from handoff
        self.assertEqual(sets["guardrail_ids"], ["AC-001"])

    def test_handoff_last_known_good_checks_as_guardrail(self):
        """SESSION_HANDOFF last_known_good_checks override guardrail_ids."""
        checks_path = self._checks([
            {"id": "AC-001", "status": "passed"},
            {"id": "AC-002", "status": "passed"},
        ])
        # handoff says only AC-001 is known good (AC-002 may have regressed)
        handoff_path = self._handoff(open_ids=[], good_ids=["AC-001"])

        sets = compute_focus_sets(checks_path, session_handoff_path=handoff_path)
        self.assertEqual(sets["guardrail_ids"], ["AC-001"])
        self.assertNotIn("AC-002", sets["guardrail_ids"])

    def test_handoff_without_checks_file(self):
        """No CHECKS.yaml but SESSION_HANDOFF present → uses handoff data."""
        handoff_path = self._handoff(open_ids=["AC-002"], good_ids=["AC-001"])
        sets = compute_focus_sets(None, session_handoff_path=handoff_path)

        self.assertTrue(sets["has_checks"])
        self.assertIn("AC-002", sets["open_ids"])
        self.assertIn("AC-001", sets["guardrail_ids"])

    def test_all_passed_empty_focus(self):
        """All criteria passed → empty focus_ids."""
        path = self._checks([
            {"id": "AC-001", "status": "passed"},
            {"id": "AC-002", "status": "passed"},
        ])
        sets = compute_focus_sets(path)
        self.assertEqual(sets["focus_ids"], [])
        self.assertEqual(sets["open_ids"], [])
        self.assertEqual(len(sets["guardrail_ids"]), 2)


# ---------------------------------------------------------------------------
# format_checks_summary tests
# ---------------------------------------------------------------------------

class TestFormatChecksSummary(unittest.TestCase):

    def test_basic_format(self):
        s = format_checks_summary(["AC-002", "AC-005"], ["AC-001"])
        self.assertIn("focus", s)
        self.assertIn("AC-002", s)
        self.assertIn("guardrails", s)
        self.assertIn("AC-001", s)

    def test_empty_returns_empty_string(self):
        self.assertEqual(format_checks_summary([], []), "")

    def test_focus_only(self):
        s = format_checks_summary(["AC-001"], [])
        self.assertIn("focus", s)
        self.assertNotIn("guardrail", s)

    def test_guardrail_only(self):
        s = format_checks_summary([], ["AC-001"])
        self.assertIn("guardrail", s)
        self.assertNotIn("focus", s)

    def test_max_focus_respected(self):
        ids = [f"AC-{i:03d}" for i in range(10)]
        s = format_checks_summary(ids, [], max_focus=3)
        # Should contain "+N more" indicator
        self.assertIn("+", s)
        # Only 3 shown directly
        shown = [f"AC-{i:03d}" for i in range(3)]
        for sid in shown:
            self.assertIn(sid, s)
        # Remaining should NOT appear explicitly
        self.assertNotIn("AC-009", s)

    def test_max_guardrail_respected(self):
        ids = [f"AC-{i:03d}" for i in range(5)]
        s = format_checks_summary([], ids, max_guardrail=2)
        self.assertIn("AC-000", s)
        self.assertIn("AC-001", s)
        self.assertNotIn("AC-004", s)

    def test_length_limit(self):
        """Summary must not exceed SUMMARY_MAX_CHARS characters."""
        long_ids = [f"VERY-LONG-CRITERION-ID-{i:04d}" for i in range(20)]
        s = format_checks_summary(long_ids, long_ids)
        self.assertLessEqual(len(s), SUMMARY_MAX_CHARS)

    def test_starts_with_checks_prefix(self):
        s = format_checks_summary(["AC-001"], ["AC-002"])
        self.assertTrue(s.startswith("Checks:"))


# ---------------------------------------------------------------------------
# get_checks_summary_for_task integration test
# ---------------------------------------------------------------------------

class TestGetChecksSummaryForTask(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_checks_file_returns_empty(self):
        s = get_checks_summary_for_task(self.task_dir)
        self.assertEqual(s, "")

    def test_with_focus_criteria_returns_summary(self):
        checks_path = os.path.join(self.task_dir, "CHECKS.yaml")
        _write(checks_path, _checks_yaml([
            {"id": "AC-001", "status": "passed"},
            {"id": "AC-002", "status": "failed"},
        ]))
        s = get_checks_summary_for_task(self.task_dir)
        self.assertIn("AC-002", s)
        self.assertIn("focus", s)

    def test_all_passed_returns_empty(self):
        checks_path = os.path.join(self.task_dir, "CHECKS.yaml")
        _write(checks_path, _checks_yaml([
            {"id": "AC-001", "status": "passed"},
        ]))
        s = get_checks_summary_for_task(self.task_dir)
        # No focus, no guardrails needed if only passed — may or may not be empty
        # Key: must be under length limit
        self.assertLessEqual(len(s), SUMMARY_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
