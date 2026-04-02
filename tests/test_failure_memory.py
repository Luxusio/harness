import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))

from failure_memory import find_similar_failure, format_similar_failure_hint


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
