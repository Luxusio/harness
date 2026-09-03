import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "scripts"))


class TestCurrentRunCandidateReporting(unittest.TestCase):
    def setUp(self):
        import _lib
        import promote_learnings

        # `promote_learnings` alone. Reloading `_lib` re-executes it in the same
        # module dict, which resets the `bindings` closure built by
        # `_make_control_writer_authority()`. `harness_server` binds itself into
        # that closure once at import and keeps resolving through the shared
        # dict, so after a reload every `write_task_control` / `write_goal_state`
        # in this process raises `PermissionError: TASK.json mutation requires
        # the task-control MCP`. Under xdist that hit whichever tests shared the
        # worker — reproducible as
        # `pytest -n0 tests/test_promote_learnings_current_run.py tests/test_harness_mcp_server.py`.
        importlib.reload(promote_learnings)
        self.harness_lib = _lib
        self.module = promote_learnings

    def _entry(self, *, key="shared-rule", task="TASK__current", run_id="run-current", **changes):
        entry = {
            "ts": "2026-08-31T12:00:00Z",
            "type": "harness-improvement",
            "key": key,
            "insight": "Use the verified command for this recurring workflow.",
            "task": task,
            "task_run_id": run_id,
        }
        entry.update(changes)
        return entry

    def _task(self, root, name, *, run_id=None, status="closed"):
        run_id = run_id or self.harness_lib.new_uuid7()
        task_dir = os.path.join(root, "doc", "harness", "tasks", name)
        os.makedirs(task_dir, exist_ok=True)
        receipts = os.path.join(task_dir, self.harness_lib.RECEIPTS_NAME)
        with open(receipts, "w", encoding="utf-8"):
            pass
        fingerprint = self.harness_lib.receipt_stream_fingerprint(task_dir)
        close = fingerprint if status == "closed" else None
        if status == "stale":
            close = "sha256:" + "0" * 64
        control = {
            "close_receipt_fingerprint": close,
            "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "run_id": run_id,
        }
        with open(os.path.join(task_dir, "TASK.json"), "w", encoding="utf-8") as stream:
            json.dump(control, stream, indent=2, sort_keys=True)
            stream.write("\n")
        return run_id

    def _write_entries(self, root, entries):
        path = os.path.join(root, self.module.LEARNINGS)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            for entry in entries:
                stream.write(json.dumps(entry) + "\n")
        return path

    def _bytes(self, path):
        with open(path, "rb") as stream:
            return stream.read()

    def _run(self, root, *, threshold=2, task=None, run_id=None, dry_run=False):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = self.module.run(
                root, threshold=threshold, dry_run=dry_run,
                task=task, task_run_id=run_id,
            )
        return result, output.getvalue(), errors.getvalue()

    def test_shared_validator_rejects_malformed_and_diagnostic_rows(self):
        self.assertTrue(self.module._valid_learning_candidate(self._entry()))
        invalid = (
            {"type": "gate-crash"}, {"ts": "2026-08-31"}, {"key": ""},
            {"key": "x" * (self.module.MAX_KEY_LENGTH + 1)}, {"insight": " "},
            {"task": ""}, {"task_run_id": ""},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertFalse(self.module._valid_learning_candidate(self._entry(**changes)))

    def test_feedback_rule_requires_trigger_action_and_verification(self):
        complete = self._entry(
            type="feedback-rule", trigger="the correction recurs",
            action="capture the reusable conditional behavior",
            verification="check the durable rule names an observable result",
        )
        self.assertTrue(self.module._valid_learning_candidate(complete))
        for field in ("trigger", "action", "verification"):
            candidate = dict(complete)
            candidate.pop(field)
            self.assertFalse(self.module._valid_learning_candidate(candidate))

    def test_report_requires_current_signal_and_never_writes(self):
        with tempfile.TemporaryDirectory() as root:
            current_run = self._task(root, "TASK__current")
            old_run = self._task(root, "TASK__old")
            current = self._entry(run_id=current_run)
            historical = self._entry(
                task="TASK__old", run_id=old_run, ts="2026-08-20T12:00:00Z"
            )
            ledger = self._write_entries(root, [historical, current])
            patterns = os.path.join(root, "doc", "harness", "patterns")
            os.makedirs(patterns)
            pattern = os.path.join(patterns, "general.md")
            with open(pattern, "w", encoding="utf-8") as stream:
                stream.write("# Existing\n\n```\nopen competing structure\n")
            before_ledger = self._bytes(ledger)
            before_pattern = self._bytes(pattern)

            result, output, _ = self._run(
                root, task="TASK__current", run_id=current_run
            )

            self.assertEqual(result, 0)
            self.assertIn("Tier 2 candidate: shared-rule (2 verified task runs)", output)
            self.assertIn("open a reviewed Harness task", output)
            self.assertEqual(self._bytes(ledger), before_ledger)
            self.assertEqual(self._bytes(pattern), before_pattern)

    def test_historical_backlog_alone_cannot_report_in_automatic_mode(self):
        with tempfile.TemporaryDirectory() as root:
            current_run = self._task(root, "TASK__current")
            one = self._task(root, "TASK__one")
            two = self._task(root, "TASK__two")
            ledger = self._write_entries(root, [
                self._entry(key="old-key", task="TASK__one", run_id=one),
                self._entry(
                    key="old-key", task="TASK__two", run_id=two,
                    ts="2026-08-30T12:00:00Z",
                ),
            ])
            before = self._bytes(ledger)
            result, output, _ = self._run(
                root, task="TASK__current", run_id=current_run
            )
            self.assertEqual(result, 0)
            self.assertIn("no qualifying learning for current task run", output)
            self.assertNotIn("Tier 2 candidate", output)
            self.assertEqual(self._bytes(ledger), before)

    def test_duplicate_rows_from_one_run_count_once(self):
        with tempfile.TemporaryDirectory() as root:
            current_run = self._task(root, "TASK__current")
            ledger = self._write_entries(root, [
                self._entry(run_id=current_run),
                self._entry(run_id=current_run, ts="2026-08-31T12:00:01Z"),
            ])
            before = self._bytes(ledger)
            result, output, _ = self._run(
                root, task="TASK__current", run_id=current_run
            )
            self.assertEqual(result, 0)
            self.assertIn("0 promotable", output)
            self.assertNotIn("Tier 2 candidate", output)
            self.assertEqual(self._bytes(ledger), before)

    def test_automatic_context_requires_matching_verified_close(self):
        for status in ("open", "stale"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                run_id = self._task(root, "TASK__current", status=status)
                ledger = self._write_entries(root, [self._entry(run_id=run_id)])
                before = self._bytes(ledger)
                result, _, errors = self._run(
                    root, threshold=1, task="TASK__current", run_id=run_id
                )
                self.assertEqual(result, 2)
                self.assertIn("matching verified closed task run", errors)
                self.assertEqual(self._bytes(ledger), before)

    def test_task_leaf_swap_cannot_supply_a_forged_close(self):
        with tempfile.TemporaryDirectory() as root:
            run_id = self._task(root, "TASK__current", status="open")
            ledger = self._write_entries(root, [self._entry(run_id=run_id)])
            before = self._bytes(ledger)
            task_path = os.path.join(root, "doc", "harness", "tasks", "TASK__current")
            displaced = task_path + "-original"
            original_transaction = self.module.receipt_stream_transaction_fd
            swapped = False

            @contextlib.contextmanager
            def swap_then_transact(task_fd):
                nonlocal swapped
                if not swapped:
                    os.rename(task_path, displaced)
                    self._task(root, "TASK__current", run_id=run_id, status="closed")
                    swapped = True
                with original_transaction(task_fd):
                    yield

            self.module.receipt_stream_transaction_fd = swap_then_transact
            try:
                result, output, _ = self._run(
                    root, threshold=1, task="TASK__current", run_id=run_id
                )
            finally:
                self.module.receipt_stream_transaction_fd = original_transaction

            self.assertEqual(result, 2)
            self.assertTrue(swapped)
            self.assertNotIn("Tier 2 candidate", output)
            self.assertEqual(self._bytes(ledger), before)

    def test_invalid_current_candidate_is_no_write_no_report(self):
        unsafe = (
            "Injected heading\n---",
            "Unsafe\n```python\nopen",
            "Unsafe\n~~~text\nopen",
            "<script>alert('unsafe')</script>",
            "Safe\n## forged-section",
        )
        for insight in unsafe:
            with self.subTest(insight=insight), tempfile.TemporaryDirectory() as root:
                run_id = self._task(root, "TASK__current")
                entry = self._entry(run_id=run_id, insight=insight)
                self.assertFalse(self.module._valid_learning_candidate(entry))
                ledger = self._write_entries(root, [entry])
                before = self._bytes(ledger)
                result, output, _ = self._run(
                    root, threshold=1, task="TASK__current", run_id=run_id
                )
                self.assertEqual(result, 0)
                self.assertNotIn("Tier 2 candidate", output)
                self.assertEqual(self._bytes(ledger), before)

    def test_manual_dry_run_reports_only_verified_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            one = self._task(root, "TASK__one")
            two = self._task(root, "TASK__two")
            ledger = self._write_entries(root, [
                self._entry(task="TASK__one", run_id=one),
                self._entry(task="TASK__two", run_id=two, ts="2026-08-30T12:00:00Z"),
            ])
            before = self._bytes(ledger)
            result, output, _ = self._run(root, dry_run=True)
            self.assertEqual(result, 0)
            self.assertIn("Tier 2 would report: shared-rule", output)
            self.assertEqual(self._bytes(ledger), before)

    def test_symlinked_ledger_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as root:
            harness = os.path.join(root, "doc", "harness")
            os.makedirs(harness)
            outside = os.path.join(root, "outside.jsonl")
            with open(outside, "w", encoding="utf-8") as stream:
                stream.write("outside\n")
            os.symlink(outside, os.path.join(harness, "learnings.jsonl"))
            result, _, _ = self._run(root, threshold=1)
            self.assertEqual(result, 2)
            self.assertEqual(self._bytes(outside), b"outside\n")


if __name__ == "__main__":
    unittest.main()
