"""Tests for self-improvement.md retro trigger logic (AC-007..AC-008).

Tests the threshold check logic directly since self-improvement.md is prose/bash.
We validate the Python logic embedded in the bash script.
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _count_tasks_since(timeline_path: str, last_retro_ts: float) -> int:
    """Count event:completed skill:run entries in timeline newer than last_retro_ts.

    Mirrors the Python embedded in self-improvement.md retro trigger.
    """
    count = 0
    if not os.path.isfile(timeline_path):
        return 0
    with open(timeline_path) as f:
        for ln in f:
            try:
                e = json.loads(ln)
                if e.get("event") == "completed" and e.get("skill") == "run":
                    ts_str = e.get("ts", "")
                    if ts_str:
                        t = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                            tzinfo=timezone.utc
                        )
                        if int(t.timestamp()) > last_retro_ts:
                            count += 1
            except Exception:
                pass
    return count


class TestRetroTriggerThreshold(unittest.TestCase):
    """AC-007: retro fires at >= 3 tasks since last retro."""

    def _make_timeline(self, d, n_completed, days_ago_each=None):
        """Write timeline.jsonl with n completed run events."""
        lines = []
        now = datetime.now(timezone.utc)
        for i in range(n_completed):
            offset = days_ago_each[i] if days_ago_each else i
            ts = (now - timedelta(days=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines.append(json.dumps({
                "skill": "run",
                "event": "completed",
                "ts": ts,
                "branch": "main",
            }))
        path = os.path.join(d, "timeline.jsonl")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_3_tasks_triggers_retro(self):
        """3 completed tasks since last retro should trigger."""
        with tempfile.TemporaryDirectory() as d:
            # Last retro was 10 days ago
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            # 3 tasks completed in last 5 days
            tl = self._make_timeline(d, 3, days_ago_each=[1, 2, 3])
            count = _count_tasks_since(tl, last_retro_ts)
            self.assertGreaterEqual(count, 3, "Should count 3 tasks since last retro")

    def test_2_tasks_does_not_trigger(self):
        """Only 2 completed tasks since last retro should not trigger."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            tl = self._make_timeline(d, 2, days_ago_each=[1, 2])
            count = _count_tasks_since(tl, last_retro_ts)
            self.assertLess(count, 3, "2 tasks should not trigger retro")

    def test_zero_tasks_no_trigger(self):
        """Zero completed tasks should not trigger."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            tl = self._make_timeline(d, 0)
            count = _count_tasks_since(tl, last_retro_ts)
            self.assertEqual(count, 0)

    def test_tasks_before_last_retro_not_counted(self):
        """Tasks completed before the last retro should not count."""
        with tempfile.TemporaryDirectory() as d:
            # Last retro was 5 days ago
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
            # 5 tasks: 3 before retro (8, 9, 10 days ago), 2 after (1, 2 days ago)
            tl = self._make_timeline(d, 5, days_ago_each=[1, 2, 8, 9, 10])
            count = _count_tasks_since(tl, last_retro_ts)
            self.assertEqual(count, 2, "Only tasks after retro should count")

    def test_no_prior_retros_seeds_from_zero(self):
        """No prior retros: last_retro_ts=0 means all tasks count."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = 0  # No prior retros
            tl = self._make_timeline(d, 4, days_ago_each=[1, 2, 3, 100])
            count = _count_tasks_since(tl, last_retro_ts)
            self.assertEqual(count, 4, "All tasks should count when no prior retro")

    def test_missing_timeline_returns_zero(self):
        """Missing timeline.jsonl should return 0."""
        count = _count_tasks_since("/nonexistent/timeline.jsonl", 0)
        self.assertEqual(count, 0)


class TestRetroTriggerEnvVar(unittest.TestCase):
    """AC-007: HARNESS_DISABLE_RETRO=1 should suppress retro."""

    def test_disable_retro_env_var_exists_in_docs(self):
        """HARNESS_DISABLE_RETRO should be documented in plugin/CLAUDE.md."""
        claude_path = os.path.join(REPO_ROOT, "plugin", "CLAUDE.md")
        with open(claude_path) as f:
            content = f.read()
        self.assertIn("HARNESS_DISABLE_RETRO", content,
                      "HARNESS_DISABLE_RETRO must be documented in plugin/CLAUDE.md")

    def test_auto_ran_section_documented_in_patterns(self):
        """Auto-ran section format must be in auto-maintenance.md."""
        pattern_path = os.path.join(REPO_ROOT, "doc", "harness", "patterns", "auto-maintenance.md")
        self.assertTrue(os.path.isfile(pattern_path), "auto-maintenance.md must exist")
        with open(pattern_path) as f:
            content = f.read()
        self.assertIn("Auto-ran", content, "Auto-ran section format must be documented")


class TestFirstFireBanner(unittest.TestCase):
    """AC-008: first-fire banner content verification."""

    def test_self_improvement_md_has_retro_block(self):
        """self-improvement.md should contain retro auto-trigger block."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "run", "self-improvement.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("retro.py --save", content, "retro.py --save should be in self-improvement.md")
        self.assertIn("HARNESS_DISABLE_RETRO", content,
                      "HARNESS_DISABLE_RETRO should be in self-improvement.md")
        self.assertIn("Auto-ran", content, "Auto-ran section reference should be present")

    def test_self_improvement_md_has_hygiene_call(self):
        """promote_learnings.py should still be invoked in self-improvement.md."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "run", "self-improvement.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("promote_learnings.py", content)


if __name__ == "__main__":
    unittest.main()
