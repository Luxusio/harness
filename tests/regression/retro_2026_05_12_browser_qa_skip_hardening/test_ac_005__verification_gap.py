"""AC-005 — verification_gap_check.py SessionStart inject.

Scenarios:
  - No .active marker → silent.
  - browser_qa_supported=false → silent.
  - browser_qa_supported=true + no frontend → silent.
  - browser_qa_supported=true + frontend + qa-browser section present → silent.
  - browser_qa_supported=true + frontend + no qa-browser → prints warning.
  - HARNESS_DISABLE_VERIFY_GAP=1 → silent regardless.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "plugin" / "scripts" / "verification_gap_check.py"


def _write(p, body):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)


def _setup_task(td_root, *, browser_supported, touched, critic_body, with_active=True):
    """Build a fixture repo and return env to use when invoking the script."""
    os.makedirs(os.path.join(td_root, "doc", "harness", "tasks", "TASK__demo"), exist_ok=True)
    task_dir = os.path.join(td_root, "doc", "harness", "tasks", "TASK__demo")
    paths_yaml = ("\n".join(f"  - {p}" for p in touched)) if touched else ""
    state_body = (
        "task_id: TASK__demo\n"
        "status: implementing\n"
        "runtime_verdict: pending\n"
        + ("touched_paths:\n" + paths_yaml + "\n" if touched else "touched_paths: []\n")
        + "plan_session_state: closed\n"
        "closed_at: null\n"
        "updated: 2026-05-12T00:00:00Z\n"
    )
    _write(os.path.join(task_dir, "TASK_STATE.yaml"), state_body)
    if critic_body is not None:
        _write(os.path.join(task_dir, "CRITIC__qa.md"), critic_body)
    _write(os.path.join(td_root, "doc", "harness", "manifest.yaml"),
           "name: demo\n"
           "type: library\n"
           "qa:\n"
           f"  browser_qa_supported: {str(browser_supported).lower()}\n")
    if with_active:
        _write(os.path.join(td_root, "doc", "harness", "tasks", ".active"), task_dir)


def _run(td_root, env_extra=None):
    env = os.environ.copy()
    env.pop("HARNESS_DISABLE_VERIFY_GAP", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd=td_root, env=env,
    )


class TestVerificationGapCheck(unittest.TestCase):
    def setUp(self):
        self.td_obj = tempfile.TemporaryDirectory()
        self.td = self.td_obj.name

    def tearDown(self):
        self.td_obj.cleanup()

    def test_silent_when_no_active_task(self):
        _setup_task(self.td, browser_supported=True, touched=["src/app.tsx"],
                    critic_body="# CRITIC\n## qa-api verdict: PASS\n", with_active=False)
        res = _run(self.td)
        self.assertEqual(res.stdout.strip(), "")

    def test_silent_when_browser_not_supported(self):
        _setup_task(self.td, browser_supported=False, touched=["src/app.tsx"],
                    critic_body="# CRITIC\n## qa-api verdict: PASS\n")
        res = _run(self.td)
        self.assertEqual(res.stdout.strip(), "")

    def test_silent_when_no_frontend_touched(self):
        _setup_task(self.td, browser_supported=True, touched=["plugin/scripts/foo.py"],
                    critic_body="# CRITIC\n## qa-api verdict: PASS\n")
        res = _run(self.td)
        self.assertEqual(res.stdout.strip(), "")

    def test_silent_when_qa_browser_section_present(self):
        _setup_task(self.td, browser_supported=True, touched=["src/app.tsx"],
                    critic_body="# CRITIC\n## qa-browser verdict: PASS\n")
        res = _run(self.td)
        self.assertEqual(res.stdout.strip(), "")

    def test_warns_when_gap_present(self):
        _setup_task(self.td, browser_supported=True, touched=["src/app.tsx"],
                    critic_body="# CRITIC\n## qa-api verdict: PASS\n")
        res = _run(self.td)
        self.assertIn("[verification-gap]", res.stdout)
        self.assertIn("TASK__demo", res.stdout)
        self.assertIn("browser QA required", res.stdout)

    def test_silent_when_disable_env_set(self):
        _setup_task(self.td, browser_supported=True, touched=["src/app.tsx"],
                    critic_body="# CRITIC\n## qa-api verdict: PASS\n")
        res = _run(self.td, env_extra={"HARNESS_DISABLE_VERIFY_GAP": "1"})
        self.assertEqual(res.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
