import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))

from failure_memory import (
    CASE_FILENAME,
    diff_failure_cases,
    find_similar_failure,
    find_similar_failures,
    format_similar_failure_hint,
    list_failure_cases,
    write_failure_case_snapshot,
)
from task_index import FAILURE_INDEX_FILENAME


class FailureMemoryTests(unittest.TestCase):
    def _make_task(
        self,
        root: Path,
        task_id: str,
        *,
        lane: str = "debug",
        verification_target: str = "src/api/users.py",
        fail_count: int = 0,
        runtime_verdict: str = "pending",
        request_text: str = "Fix users API persistence.",
        critic_summary: str = "",
        check_id: str = "AC-001",
        check_status: str = "failed",
    ) -> Path:
        task_dir = root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "TASK_STATE.yaml").write_text(
            "\n".join(
                [
                    f"task_id: {task_id}",
                    "status: implemented",
                    f"lane: {lane}",
                    f"runtime_verdict: {runtime_verdict}",
                    f"runtime_verdict_fail_count: {fail_count}",
                    f"verification_targets: [{verification_target}]",
                    "updated: 2026-04-01T00:00:00Z",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (task_dir / "REQUEST.md").write_text(request_text + "\n", encoding="utf-8")
        (task_dir / "CHECKS.yaml").write_text(
            "\n".join(
                [
                    "checks:",
                    f"  - id: {check_id}",
                    f"    status: {check_status}",
                    "    title: Users API persists updates",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        if critic_summary:
            (task_dir / "CRITIC__runtime.md").write_text(
                f"verdict: FAIL\nsummary: {critic_summary}\n",
                encoding="utf-8",
            )
        return task_dir

    def test_find_similar_failure_prefers_matching_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self._make_task(
                root,
                "TASK__current",
                runtime_verdict="FAIL",
                fail_count=1,
                critic_summary="users persistence still broken",
            )
            close_match = self._make_task(
                root,
                "TASK__close_match",
                fail_count=2,
                verification_target="src/api/users.py",
                critic_summary="same users persistence problem after reload",
            )
            far_match = self._make_task(
                root,
                "TASK__far_match",
                fail_count=2,
                verification_target="docs/README.md",
                request_text="Repair docs formatting.",
                critic_summary="markdown formatting issue",
                check_id="DOC-001",
            )

            match = find_similar_failure(str(current), tasks_dir=str(root))
            self.assertIsNotNone(match)
            self.assertEqual(match["task_id"], "TASK__close_match")
            self.assertIn("src", match.get("matching_paths", []))

    def test_find_similar_failures_returns_top_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self._make_task(
                root,
                "TASK__current",
                runtime_verdict="FAIL",
                fail_count=1,
                critic_summary="users persistence still broken",
            )
            self._make_task(
                root,
                "TASK__best",
                fail_count=2,
                verification_target="src/api/users.py",
                critic_summary="same users persistence problem after reload",
                check_id="AC-001",
            )
            self._make_task(
                root,
                "TASK__second",
                fail_count=1,
                verification_target="src/api/profile.py",
                request_text="Fix profile persistence after save.",
                critic_summary="profile persistence issue",
                check_id="AC-002",
            )

            matches = find_similar_failures(str(current), tasks_dir=str(root), limit=2)
            self.assertEqual(len(matches), 2)
            self.assertEqual(matches[0]["task_id"], "TASK__best")
            self.assertGreaterEqual(matches[0]["score"], matches[1]["score"])

    def test_write_failure_case_snapshot_writes_json_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = self._make_task(
                root,
                "TASK__case",
                runtime_verdict="FAIL",
                fail_count=2,
                critic_summary="users persistence still broken",
            )

            case_path = write_failure_case_snapshot(str(task_dir))
            self.assertTrue(case_path.endswith(CASE_FILENAME))
            self.assertTrue((task_dir / CASE_FILENAME).is_file())
            payload = json.loads((task_dir / CASE_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(payload["task_id"], "TASK__case")
            self.assertEqual(payload["runtime_verdict"], "FAIL")
            self.assertGreaterEqual(payload["failure_signals"], 2)

            failure_index = json.loads((root / FAILURE_INDEX_FILENAME).read_text(encoding="utf-8"))
            self.assertIn("TASK__case", failure_index.get("cases", {}))

    def test_list_failure_cases_and_diff_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_task(
                root,
                "TASK__a",
                runtime_verdict="FAIL",
                fail_count=2,
                critic_summary="users persistence still broken",
                check_id="AC-001",
            )
            self._make_task(
                root,
                "TASK__b",
                runtime_verdict="FAIL",
                fail_count=1,
                verification_target="src/api/users.py",
                critic_summary="users api still broken",
                check_id="AC-001",
            )

            cases = list_failure_cases(tasks_dir=str(root), limit=5)
            self.assertGreaterEqual(len(cases), 2)
            self.assertEqual(cases[0]["task_id"], "TASK__a")

            diff = diff_failure_cases("TASK__a", "TASK__b", tasks_dir=str(root))
            self.assertIsNotNone(diff)
            self.assertIn("AC-001", diff.get("shared_check_ids", []))
            self.assertIn("src", diff.get("shared_paths", []))

    def test_format_similar_failure_hint_includes_task_id(self):
        hint = format_similar_failure_hint(
            {
                "task_id": "TASK__prev",
                "matching_check_ids": ["AC-001"],
                "matching_paths": ["src", "api"],
                "excerpt": "persistence still fails after reload",
            }
        )
        self.assertIn("TASK__prev", hint)
        self.assertIn("AC-001", hint)

    def test_no_failure_history_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = self._make_task(root, "TASK__current")
            match = find_similar_failure(str(current), tasks_dir=str(root))
            self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
