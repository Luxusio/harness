"""Tests for the receipt-backed self-improvement retro trigger (AC-008)."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS)
SPEC = importlib.util.spec_from_file_location(
    "retro_trigger", os.path.join(SCRIPTS, "retro.py")
)
assert SPEC and SPEC.loader
RETRO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RETRO)


def _receipt_fingerprint(raw: bytes = b"") -> str:
    digest = hashlib.sha256()
    digest.update(b"RECEIPTS.jsonl\0")
    digest.update(raw)
    digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _write_task(
    repo: str,
    name: str,
    published: float,
    *,
    state: str = "closed",
) -> Path:
    task = Path(repo, "doc", "harness", "tasks", f"TASK__{name}")
    task.mkdir(parents=True)
    receipt = task / "RECEIPTS.jsonl"
    receipt.write_bytes(b"")
    close = _receipt_fingerprint()
    if state in {"open", "blocked", "reopened"}:
        close = None
    elif state == "stale-fingerprint":
        close = "sha256:" + "f" * 64
    control = {
        "close_receipt_fingerprint": close,
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "run_id": "018f0000-0000-7000-8000-000000000001",
    }
    control_path = task / "TASK.json"
    if state == "invalid":
        control_path.write_text("{not json}\n", encoding="utf-8")
    else:
        control_path.write_text(json.dumps(control) + "\n", encoding="utf-8")
    if state == "blocked":
        (task / "BLOCKED.md").write_text("blocked\n", encoding="utf-8")
    os.utime(control_path, (published, published))
    return task


class TestRetroTriggerThreshold(unittest.TestCase):
    """AC-008: retro fires at >= 3 verified closes since last retro."""

    def _make_tasks(self, repo, n_completed, days_ago_each=None):
        now = datetime.now(timezone.utc)
        for i in range(n_completed):
            offset = days_ago_each[i] if days_ago_each else i
            ts = (now - timedelta(days=offset)).timestamp()
            _write_task(repo, f"{i:03d}", ts)

    def test_3_tasks_triggers_retro(self):
        """Three verified closes preserve the existing cadence."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            self._make_tasks(d, 3, days_ago_each=[1, 2, 3])
            count = RETRO.count_verified_closes_since(d, last_retro_ts)
            self.assertEqual(RETRO.RETRO_CLOSE_THRESHOLD, 3)
            self.assertGreaterEqual(count, 3, "Should count 3 tasks since last retro")

    def test_2_tasks_does_not_trigger(self):
        """Only 2 completed tasks since last retro should not trigger."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            self._make_tasks(d, 2, days_ago_each=[1, 2])
            count = RETRO.count_verified_closes_since(d, last_retro_ts)
            self.assertLess(count, 3, "2 tasks should not trigger retro")

    def test_zero_tasks_no_trigger(self):
        """Zero completed tasks should not trigger."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            self._make_tasks(d, 0)
            count = RETRO.count_verified_closes_since(d, last_retro_ts)
            self.assertEqual(count, 0)

    def test_tasks_before_last_retro_not_counted(self):
        """Tasks completed before the last retro should not count."""
        with tempfile.TemporaryDirectory() as d:
            last_retro_ts = (datetime.now(timezone.utc) - timedelta(days=5)).timestamp()
            self._make_tasks(d, 5, days_ago_each=[1, 2, 8, 9, 10])
            count = RETRO.count_verified_closes_since(d, last_retro_ts)
            self.assertEqual(count, 2, "Only tasks after retro should count")

    def test_no_prior_retros_seeds_from_zero(self):
        """No prior retros: last_retro_ts=0 means all tasks count."""
        with tempfile.TemporaryDirectory() as d:
            self._make_tasks(d, 4, days_ago_each=[1, 2, 3, 100])
            count = RETRO.count_verified_closes_since(d, 0)
            self.assertEqual(count, 4, "All tasks should count when no prior retro")

    def test_missing_tasks_root_returns_zero(self):
        """Missing task directory root should return 0."""
        count = RETRO.count_verified_closes_since("/nonexistent/repo", 0)
        self.assertEqual(count, 0)

    def test_only_verified_task_json_publications_count(self):
        """Open, blocked, reopened, invalid, stale, and touched-only tasks stay out."""
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).timestamp()
            cutoff = now - 100
            included = _write_task(d, "closed", now - 10)
            for state in ("open", "blocked", "reopened", "invalid", "stale-fingerprint"):
                _write_task(d, state, now - 10, state=state)

            old = _write_task(d, "touched-only", cutoff - 10)
            os.utime(old, (now, now))

            tasks = RETRO.verified_closed_tasks_since(d, cutoff)
            self.assertEqual(
                tasks,
                [(included.joinpath("TASK.json").stat().st_mtime, "TASK__closed")],
            )

    def test_malformed_task_names_do_not_count_or_inject_report_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).timestamp()
            valid = _write_task(d, "valid", now)
            _write_task(d, "bad`\n- injected", now)
            _write_task(d, "bad name", now)

            tasks = RETRO.verified_closed_tasks_since(d, 0)
            self.assertEqual(
                tasks,
                [(valid.joinpath("TASK.json").stat().st_mtime, "TASK__valid")],
            )
            report = RETRO._section_tasks(d, 1)
            self.assertIn("`TASK__valid`", report)
            self.assertNotIn("injected", report)
            self.assertNotIn("bad name", report)

    def test_markdown_renderer_fails_closed_for_noncanonical_text(self):
        self.assertEqual(
            RETRO._markdown_task_name("TASK__safe`\n- injected"),
            "[invalid-task]",
        )

    def test_unsafe_task_control_and_symlink_task_are_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).timestamp()
            unsafe = _write_task(d, "unsafe", now)
            unsafe.joinpath("TASK.json").chmod(0o666)

            outside = Path(d, "outside")
            outside.mkdir()
            tasks_root = Path(d, "doc", "harness", "tasks")
            tasks_root.joinpath("TASK__symlink").symlink_to(outside, target_is_directory=True)

            self.assertEqual(RETRO.count_verified_closes_since(d, 0), 0)

    def test_symlinked_or_writable_tasks_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            real_root = Path(d, "real-tasks")
            real_root.mkdir()
            tasks_root = Path(d, "doc", "harness", "tasks")
            tasks_root.parent.mkdir(parents=True)
            tasks_root.symlink_to(real_root, target_is_directory=True)
            self.assertEqual(RETRO.count_verified_closes_since(d, 0), 0)

        with tempfile.TemporaryDirectory() as d:
            _write_task(d, "closed", datetime.now(timezone.utc).timestamp())
            tasks_root = Path(d, "doc", "harness", "tasks")
            tasks_root.chmod(0o777)
            self.assertEqual(RETRO.count_verified_closes_since(d, 0), 0)

    def test_tasks_root_rebound_during_enumeration_rejects_partial_count(self):
        with tempfile.TemporaryDirectory() as d:
            _write_task(d, "closed", datetime.now(timezone.utc).timestamp())
            tasks_root = Path(d, "doc", "harness", "tasks")
            displaced = tasks_root.with_name("tasks-displaced")
            original_check = RETRO._verified_close_publication_fd
            rebound = False

            def replace_root(task_fd):
                nonlocal rebound
                if not rebound:
                    tasks_root.rename(displaced)
                    tasks_root.mkdir()
                    rebound = True
                return original_check(task_fd)

            with mock.patch.object(
                RETRO, "_verified_close_publication_fd", side_effect=replace_root
            ):
                self.assertEqual(RETRO.count_verified_closes_since(d, 0), 0)

    def test_task_leaf_rebind_does_not_replace_enumerated_generation(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).timestamp()
            original = _write_task(d, "target", now, state="open")
            displaced = original.with_name("TASK__target-original")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            task_fd = os.open(original, flags)
            try:
                original.rename(displaced)
                _write_task(d, "target", now, state="closed")
                self.assertIsNone(RETRO._verified_close_publication_fd(task_fd))
            finally:
                os.close(task_fd)

    def test_receipt_writer_cannot_race_after_closed_classification(self):
        with tempfile.TemporaryDirectory() as d:
            task = _write_task(d, "closed", datetime.now(timezone.utc).timestamp())
            attempted = threading.Event()
            acquired = threading.Event()
            writer = None
            original_status = RETRO.task_control_status

            def write_after_classification():
                attempted.set()
                with RETRO.receipt_stream_transaction(str(task)):
                    acquired.set()

            def status_then_race(task_dir, control):
                nonlocal writer
                status = original_status(task_dir, control)
                writer = threading.Thread(target=write_after_classification)
                writer.start()
                self.assertTrue(attempted.wait(1))
                self.assertFalse(acquired.wait(0.1))
                return status

            with mock.patch.object(RETRO, "task_control_status", side_effect=status_then_race):
                self.assertIsNotNone(RETRO._verified_close_publication(str(task)))

            self.assertIsNotNone(writer)
            writer.join(1)
            self.assertFalse(writer.is_alive())
            self.assertTrue(acquired.is_set())

    def test_report_uses_the_same_verified_close_predicate(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc).timestamp()
            _write_task(d, "closed", now)
            _write_task(d, "open", now, state="open")

            report = RETRO._section_tasks(d, 1)
            self.assertIn("1** verified task closures", report)
            self.assertIn("TASK__closed", report)
            self.assertNotIn("TASK__open", report)


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
        self.assertIn(
            "retro.py --count-closed-since",
            content,
            "the auto-trigger and report must share retro.py's verified-close predicate",
        )
        self.assertIn("HARNESS_DISABLE_RETRO", content,
                      "HARNESS_DISABLE_RETRO should be in self-improvement.md")
        self.assertIn("Auto-ran", content, "Auto-ran section reference should be present")

    def test_self_improvement_md_invokes_promote_learnings(self):
        """promote_learnings.py should still be invoked in self-improvement.md."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "run", "self-improvement.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("promote_learnings.py", content)

    def test_retro_threshold_shell_condition_executes_and_honors_disable(self):
        path = os.path.join(REPO_ROOT, "plugin", "skills", "run", "self-improvement.md")
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        condition = next(line for line in lines if line.startswith("if [ \"${_TASKS_SINCE"))
        enabled = subprocess.run(
            ["bash", "-c", f"_TASKS_SINCE=3; unset HARNESS_DISABLE_RETRO; {condition}\necho fired\nfi"],
            capture_output=True, text=True, check=False,
        )
        disabled = subprocess.run(
            ["bash", "-c", f"_TASKS_SINCE=3; HARNESS_DISABLE_RETRO=1; {condition}\necho fired\nfi"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.assertEqual(enabled.stdout.strip(), "fired")
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertEqual(disabled.stdout.strip(), "")


class TestRetroSafeIO(unittest.TestCase):
    def test_commit_metadata_is_filtered_before_markdown_rendering(self):
        outputs = [
            "abc123 commit",
            "Good User\n<script>alert(1)</script>\nBad`Author",
            "safe/path.py\nbad`path.md\n<html>.md",
        ]
        with mock.patch.object(RETRO, "_git", side_effect=outputs):
            report = RETRO._section_commits("/unused", 7)
        self.assertIn("Good User", report)
        self.assertIn("`safe/path.py`", report)
        self.assertNotIn("script", report)
        self.assertNotIn("html", report)
        self.assertNotIn("Bad`Author", report)
        self.assertNotIn("bad`path", report)

    def test_symlinked_learnings_is_ignored_without_disclosure(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            harness = Path(repo, "doc", "harness")
            harness.mkdir(parents=True)
            outside_ledger = Path(outside, "outside.jsonl")
            outside_ledger.write_text(json.dumps({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "type": "eureka",
                "key": "secret-key",
                "insight": "TOP_SECRET_VALUE",
            }) + "\n", encoding="utf-8")
            harness.joinpath("learnings.jsonl").symlink_to(outside_ledger)

            report = RETRO.generate(repo, 7)

            self.assertIn("(none in this period)", report)
            self.assertNotIn("TOP_SECRET_VALUE", report)
            self.assertNotIn("secret-key", report)

    def test_learning_report_renders_validated_metadata_not_insight(self):
        with tempfile.TemporaryDirectory() as repo:
            harness = Path(repo, "doc", "harness")
            harness.mkdir(parents=True)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = [
                {"ts": now, "type": "eureka", "key": "safe-key", "insight": "TOP_SECRET_VALUE"},
                {"ts": now, "type": "eureka", "key": "safe-key", "insight": "SECOND_SECRET"},
            ]
            harness.joinpath("learnings.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )

            report = RETRO.generate(repo, 7)

            self.assertIn("eureka (2)", report)
            self.assertIn("`safe-key` (2x)", report)
            self.assertNotIn("TOP_SECRET_VALUE", report)
            self.assertNotIn("SECOND_SECRET", report)

    def test_save_rejects_existing_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as outside:
            retros = Path(repo, "doc", "harness", "retros")
            retros.mkdir(parents=True)
            target = Path(outside, "outside.md")
            target.write_text("KEEP\n", encoding="utf-8")
            retros.joinpath("2026-09-01.md").symlink_to(target)

            with self.assertRaises(RuntimeError):
                RETRO._save_report(repo, "replacement\n", "2026-09-01")

            self.assertEqual(target.read_text(encoding="utf-8"), "KEEP\n")
            self.assertTrue(retros.joinpath("2026-09-01.md").is_symlink())

    def test_save_atomically_publishes_regular_report(self):
        with tempfile.TemporaryDirectory() as repo:
            Path(repo, "doc", "harness").mkdir(parents=True)
            path = RETRO._save_report(repo, "safe report\n", "2026-09-01")
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "safe report\n")


if __name__ == "__main__":
    unittest.main()
