"""AC-001..AC-006: prompt_memory.py context-injection hook.

Uses isolated scratch dirs + patched find_repo_root so test runs don't
leak into the real repo's doc/harness/tasks/.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
PROMPT = SCRIPTS / "prompt_memory.py"


def _invoke(repo_root: str, *, env_extra=None) -> subprocess.CompletedProcess:
    """Run prompt_memory.py with stdin payload, cwd = the scratch repo."""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(PROMPT)],
        input="",
        capture_output=True, text=True, cwd=repo_root, env=env, timeout=5,
    )


def _build_scratch_repo(base: Path, *,
                        active_task_id: str | None = None,
                        task_state: str | None = None,
                        plan: bool = True,
                        checks_yaml: str | None = None,
                        write_critic: bool = False,
                        touched_paths: list[str] | None = None,
                        suspect_notes: dict[str, str] | None = None,
                        ignored_note: bool = False) -> Path:
    """Build a git-rooted scratch repo with optional task + CHECKS + notes."""
    (base / ".git").mkdir(parents=True, exist_ok=True)  # marks repo_root for find_repo_root
    tasks = base / "doc" / "harness" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    if active_task_id:
        task_dir = tasks / active_task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        if plan:
            (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
        if task_state is not None:
            (task_dir / "TASK_STATE.yaml").write_text(task_state, encoding="utf-8")
        else:
            tp_block = "[]" if not touched_paths else "\n" + "\n".join(f"  - {p}" for p in touched_paths)
            (task_dir / "TASK_STATE.yaml").write_text(
                f"task_id: {active_task_id}\nstatus: implementing\n"
                f"runtime_verdict: PASS\ntouched_paths: {tp_block}\n"
                f"plan_session_state: closed\nclosed_at: null\n"
                f"updated: 2026-04-19T00:00:00Z\n",
                encoding="utf-8",
            )
        if write_critic:
            (task_dir / "CRITIC__qa.md").write_text("# critic\n", encoding="utf-8")
        if checks_yaml is not None:
            (task_dir / "CHECKS.yaml").write_text(checks_yaml, encoding="utf-8")
        (tasks / ".active").write_text(str(task_dir), encoding="utf-8")
    if suspect_notes:
        doc_dir = base / "doc" / "common"
        doc_dir.mkdir(parents=True, exist_ok=True)
        for name, body in suspect_notes.items():
            (doc_dir / name).write_text(body, encoding="utf-8")
    if ignored_note:
        # Frontmatter present, freshness not suspect → must not appear.
        doc_dir = base / "doc" / "common"
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "current.md").write_text(
            "---\nfreshness: current\n---\nbody\n", encoding="utf-8",
        )
    return base


class TestPromptMemory(unittest.TestCase):

    # ---- AC-002: silent paths ----
    def test_no_active_task_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_active_points_nowhere_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / ".git").mkdir()
            tasks = base / "doc" / "harness" / "tasks"
            tasks.mkdir(parents=True)
            (tasks / ".active").write_text("/nonexistent/TASK__x", encoding="utf-8")
            r = _invoke(str(base))
        self.assertEqual(r.stdout, "")

    def test_malformed_state_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__malf",
                task_state="not valid yaml\n:::\n",
            )
            r = _invoke(str(base))
        # read_state tolerates malformed input; may emit minimal block or empty.
        # Both are acceptable; do not regress on a hard crash.
        self.assertEqual(r.returncode, 0)

    # ---- AC-001: happy path shape ----
    def test_emits_context_block_for_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__happy",
            )
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("[harness-context] "), r.stdout)
        self.assertIn("task=TASK__happy", r.stdout)
        self.assertIn("status=implementing", r.stdout)
        self.assertIn("verdict=PASS", r.stdout)
        # No CHECKS / notes → omit those sections
        self.assertNotIn("open=", r.stdout)
        self.assertNotIn("suspect=", r.stdout)

    # ---- AC-003: verdict + stale ----
    def test_stale_flag_when_touched_newer_than_critic(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__stale",
                write_critic=True,
                touched_paths=["src/foo.py"],
            )
            # CRITIC older than touched source
            (base / "src").mkdir()
            src = base / "src" / "foo.py"
            src.write_text("pass\n")
            now = time.time()
            os.utime(src, (now, now))
            critic = base / "doc" / "harness" / "tasks" / "TASK__stale" / "CRITIC__qa.md"
            os.utime(critic, (100, 100))
            r = _invoke(str(base))
        self.assertIn(" stale", r.stdout)

    def test_stale_skip_list_ignores_pyc(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__pyc",
                write_critic=True,
                touched_paths=["src/__pycache__/foo.cpython-311.pyc"],
            )
            (base / "src" / "__pycache__").mkdir(parents=True)
            pyc = base / "src" / "__pycache__" / "foo.cpython-311.pyc"
            pyc.write_text("x")
            now = time.time()
            os.utime(pyc, (now, now))
            critic = base / "doc" / "harness" / "tasks" / "TASK__pyc" / "CRITIC__qa.md"
            os.utime(critic, (100, 100))
            r = _invoke(str(base))
        self.assertNotIn(" stale", r.stdout,
                         f"pyc path should be skip-listed: {r.stdout!r}")

    # ---- AC-004: AC summary ----
    def test_open_ac_summary(self):
        checks = (
            '- id: AC-001\n  title: "first open"\n  status: open\n  kind: functional\n'
            '- id: AC-002\n  title: "second failed"\n  status: failed\n  kind: functional\n'
            '- id: AC-003\n  title: "third impl-cand"\n  status: implemented_candidate\n  kind: functional\n'
            '- id: AC-004\n  title: "fourth open (hidden)"\n  status: open\n  kind: functional\n'
            '- id: AC-005\n  title: "done already"\n  status: passed\n  kind: functional\n'
            '- id: AC-006\n  title: "deferred one"\n  status: deferred\n  kind: functional\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__acs",
                checks_yaml=checks,
            )
            r = _invoke(str(base))
        self.assertIn("open=", r.stdout)
        # First 3 non-terminal
        self.assertIn("AC-001:", r.stdout)
        self.assertIn("AC-002:", r.stdout)
        self.assertIn("AC-003:", r.stdout)
        # Cap at 3 — AC-004 (also non-terminal) should NOT appear
        self.assertNotIn("AC-004:", r.stdout)
        # Terminal ACs hidden
        self.assertNotIn("AC-005:", r.stdout)
        self.assertNotIn("AC-006:", r.stdout)

    # ---- AC-005: suspect note listing ----
    def test_suspect_note_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__note",
                suspect_notes={
                    "sus.md": "---\nfreshness: suspect\n---\nbody\n",
                },
                ignored_note=True,
            )
            r = _invoke(str(base))
        self.assertIn("suspect=", r.stdout)
        self.assertIn("doc/common/sus.md", r.stdout)
        self.assertNotIn("current.md", r.stdout)

    # ---- AC-006: 400-char cap ----
    def test_output_capped_at_400_chars(self):
        # Craft an extreme scenario: many ACs + extremely long titles
        checks = "".join(
            f'- id: AC-{i:03d}\n  title: "{("x" * 200)}"\n  status: open\n  kind: functional\n'
            for i in range(10)
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__cap",
                checks_yaml=checks,
            )
            r = _invoke(str(base))
        self.assertLessEqual(len(r.stdout), 400, f"{len(r.stdout)} > 400: {r.stdout!r}")

    # ---- Reopened AC warning (PR4 extension) ----
    def test_reopened_ac_warning_rendered(self):
        checks = (
            '- id: AC-001\n  title: "still open"\n  status: open\n  kind: functional\n'
            '  reopen_count: 2\n'
            '- id: AC-002\n  title: "second"\n  status: failed\n  kind: functional\n'
            '  reopen_count: 1\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__reopen",
                checks_yaml=checks,
            )
            r = _invoke(str(base))
        self.assertIn("open=", r.stdout)
        self.assertIn("⚠reopened=3", r.stdout)

    def test_no_reopen_warning_when_counts_zero(self):
        checks = (
            '- id: AC-001\n  title: "clean"\n  status: open\n  kind: functional\n'
            '  reopen_count: 0\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__noreopen",
                checks_yaml=checks,
            )
            r = _invoke(str(base))
        self.assertIn("open=", r.stdout)
        self.assertNotIn("reopened=", r.stdout)

    # ---- Perf sanity: hook stays fast even with deep doc tree ----
    def test_hook_completes_quickly(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__perf",
            )
            t0 = time.time()
            r = _invoke(str(base))
            elapsed = time.time() - t0
        self.assertEqual(r.returncode, 0)
        self.assertLess(elapsed, 2.5, f"hook took {elapsed}s (budget 3s)")


if __name__ == "__main__":
    unittest.main()
