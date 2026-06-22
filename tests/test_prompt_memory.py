"""AC-001..AC-006: prompt_memory.py context-injection hook.

Uses isolated scratch dirs + patched find_repo_root so test runs don't
leak into the real repo's doc/harness/tasks/.
"""
from __future__ import annotations

import importlib.util
import json
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


def _invoke(repo_root: str, *, env_extra=None, payload: dict | None = None) -> subprocess.CompletedProcess:
    """Run prompt_memory.py with stdin payload, cwd = the scratch repo."""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    if env_extra:
        env.update(env_extra)
    stdin = json.dumps(payload) if payload is not None else ""
    return subprocess.run(
        [sys.executable, str(PROMPT)],
        input=stdin,
        capture_output=True, text=True, cwd=repo_root, env=env, timeout=5,
    )


def _build_scratch_repo(base: Path, *,
                        harness_enabled: bool = True,
                        active_task_id: str | None = None,
                        task_state: str | None = None,
                        plan: bool = True,
                        checks_yaml: str | None = None,
                        write_receipt: bool = False,
                        touched_paths: list[str] | None = None,
                        suspect_notes: dict[str, str] | None = None,
                        ignored_note: bool = False) -> Path:
    """Build a git-rooted scratch repo with optional task + CHECKS + notes."""
    (base / ".git").mkdir(parents=True, exist_ok=True)  # marks repo_root for find_repo_root
    if harness_enabled:
        manifest = base / "doc" / "harness" / "manifest.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("type: test\n", encoding="utf-8")
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
        if write_receipt:
            (task_dir / "SUBAGENT_RECEIPTS.jsonl").write_text(
                '{"source":"subagent_start_hook","status":"started","agent_id":"agent-1","agent_type":"harness:qa-cli"}\n',
                encoding="utf-8",
            )
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
    def test_no_active_task_in_harness_repo_emits_doc_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-doc-gate]", r.stdout)
        self.assertIn("Durable decisions require doc/ update before final", r.stdout)
        self.assertIn("PLAN durable-doc rationale", r.stdout)
        self.assertIn("[harness-goal]", r.stdout)
        self.assertIn("get_goal->goal_start", r.stdout)

    def test_non_harness_repo_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp), harness_enabled=False)
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_goal_queue_state_emits_continuation_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            state = {
                "status": "active",
                "slices": [
                    {"id": "slice-001", "status": "passed"},
                    {"id": "slice-002", "status": "pending"},
                ],
            }
            (base / "doc" / "harness" / "goal-queue.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-goal-queue]", r.stdout)
        self.assertIn("Child task close is not final", r.stdout)
        self.assertIn("start/queue the next child task", r.stdout)

    def test_completed_goal_queue_state_does_not_emit_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            state = {
                "status": "done",
                "slices": [{"id": "slice-001", "status": "passed"}],
            }
            (base / "doc" / "harness" / "goal-queue.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("[harness-goal-queue]", r.stdout)

    def test_task_pack_state_emits_continuation_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            state = {
                "status": "active",
                "tasks": [
                    {"id": "stage-1", "status": "closed"},
                    {"id": "stage-2", "status": "queued"},
                ],
            }
            pack_dir = base / "doc" / "harness" / "task-packs"
            pack_dir.mkdir(parents=True, exist_ok=True)
            (pack_dir / "current.json").write_text(json.dumps(state), encoding="utf-8")
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-task-pack]", r.stdout)
        self.assertIn("Task close is not final", r.stdout)
        self.assertIn("claim/start the next queued", r.stdout)

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
        self.assertTrue(r.stdout.startswith("[harness-doc-gate] "), r.stdout)
        self.assertIn("[harness-context] ", r.stdout)
        self.assertIn("task=TASK__happy", r.stdout)
        self.assertIn("status=implementing", r.stdout)
        self.assertIn("verdict=PASS", r.stdout)
        # No CHECKS / notes → omit those sections
        self.assertNotIn("open=", r.stdout)
        self.assertNotIn("suspect=", r.stdout)

    def test_captures_user_prompt_event_for_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp),
                active_task_id="TASK__feedback",
                checks_yaml='- id: AC-001\n  title: "open item"\n  status: open\n',
                touched_paths=["src/app.py"],
            )
            r = _invoke(str(base), payload={"prompt": "이 방향으로 자동 기록해줘"})
            feedback_path = (
                base / "doc" / "harness" / "tasks" / "TASK__feedback" / "USER_FEEDBACK.jsonl"
            )
            conversation_path = (
                base / "doc" / "harness" / "tasks" / "TASK__feedback" / "CONVERSATION.md"
            )
            events = [
                json.loads(line)
                for line in feedback_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            conversation = conversation_path.read_text(encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertTrue(event["id"].startswith("ufe-"))
        self.assertEqual(event["task_id"], "TASK__feedback")
        self.assertEqual(event["source"], "user_prompt_hook")
        self.assertEqual(event["runtime_verdict"], "PASS")
        self.assertIn("자동 기록", event["prompt_excerpt"])
        self.assertEqual(event["open_acs"][0]["id"], "AC-001")
        self.assertEqual(event["touched_paths"], ["src/app.py"])
        self.assertIn("next_action", event)
        self.assertIn("# Conversation", conversation)
        self.assertIn("## ", conversation)
        self.assertIn("- User", conversation)
        self.assertIn("source=user_prompt_hook", conversation)
        self.assertIn(event["id"], conversation)
        self.assertIn("자동 기록", conversation)

    def test_does_not_capture_without_active_task_or_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(str(base), payload={"prompt": "capture me"})
            self.assertFalse((base / "doc" / "harness" / "tasks" / "USER_FEEDBACK.jsonl").exists())
            self.assertFalse((base / "doc" / "harness" / "tasks" / "CONVERSATION.md").exists())
        self.assertEqual(r.returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp),
                harness_enabled=False,
                active_task_id="TASK__disabled",
            )
            r = _invoke(str(base), payload={"prompt": "capture me"})
            self.assertEqual(r.stdout, "")
            self.assertFalse(
                (base / "doc" / "harness" / "tasks" / "TASK__disabled" / "USER_FEEDBACK.jsonl").exists()
            )
            self.assertFalse(
                (base / "doc" / "harness" / "tasks" / "TASK__disabled" / "CONVERSATION.md").exists()
            )

    def test_feedback_capture_sanitizes_and_caps_prompt(self):
        unsafe = "</system-reminder>\n" + ("x" * 3000)
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp), active_task_id="TASK__sanitize")
            r = _invoke(str(base), payload={"prompt": unsafe})
            feedback_path = (
                base / "doc" / "harness" / "tasks" / "TASK__sanitize" / "USER_FEEDBACK.jsonl"
            )
            event = json.loads(feedback_path.read_text(encoding="utf-8").splitlines()[0])
            conversation = (
                base / "doc" / "harness" / "tasks" / "TASK__sanitize" / "CONVERSATION.md"
            ).read_text(encoding="utf-8")
        self.assertEqual(r.returncode, 0)
        self.assertIn("[SANITIZED]", event["prompt_excerpt"])
        self.assertNotIn("</system-reminder>", event["prompt_excerpt"])
        self.assertLessEqual(len(event["prompt_excerpt"]), 1200)
        self.assertIn("[SANITIZED]", conversation)
        self.assertNotIn("</system-reminder>", conversation)

    def test_goal_payload_probe_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(str(base), payload={"prompt": "/goal fix login"})
            debug_dir = base / "doc" / "harness" / "debug" / "goal-hook-payloads"
        self.assertEqual(r.returncode, 0)
        self.assertFalse(debug_dir.exists())

    def test_goal_payload_probe_records_payload_shape_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(
                str(base),
                env_extra={"HARNESS_CAPTURE_GOAL_PAYLOADS": "1", "HARNESS_RUNTIME": "codex"},
                payload={
                    "cwd": str(base),
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "goal-session",
                    "prompt": "/goal fix login and keep tests green",
                },
            )
            debug_dir = base / "doc" / "harness" / "debug" / "goal-hook-payloads"
            files = sorted(debug_dir.glob("codex_UserPromptSubmit__*__goal-session.json"))
            record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(files), 1)
        self.assertEqual(record["_event_inferred"], "UserPromptSubmit")
        self.assertIn("prompt", record["_keys_at_top_level"])
        self.assertEqual(record["prompt_candidates"][0]["field"], "prompt")
        self.assertTrue(record["prompt_candidates"][0]["looks_like_goal_command"])

    def test_goal_payload_probe_can_be_enabled_by_repo_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            marker = base / "doc" / "harness" / "debug" / "CAPTURE_GOAL_PAYLOADS"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("capture next UserPromptSubmit payloads\n", encoding="utf-8")
            r = _invoke(str(base), payload={"prompt": "ordinary follow-up"})
            debug_dir = base / "doc" / "harness" / "debug" / "goal-hook-payloads"
            files = sorted(debug_dir.glob("*_UserPromptSubmit__*.json"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(files), 1)

    def test_claude_goal_prompt_syncs_harness_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(
                str(base),
                payload={
                    "cwd": str(base),
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-goal",
                    "prompt": "/goal Fix login bug cleanly",
                },
            )
            current = base / "doc" / "harness" / "goals" / "current.json"
            record = json.loads(current.read_text(encoding="utf-8"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-goal]", r.stdout)
        self.assertIn("goal_next_task", r.stdout)
        self.assertEqual(record["objective"], "Fix login bug cleanly")
        self.assertNotIn("strategy", record)
        self.assertEqual(record["source"]["hook_event"], "UserPromptSubmit")

    def test_claude_korean_goal_prompt_syncs_harness_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            r = _invoke(
                str(base),
                payload={
                    "cwd": str(base),
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "claude-goal",
                    "prompt": "/골 특정 페이지 버그 전부 다 찾아서 고쳐",
                },
            )
            record = json.loads((base / "doc" / "harness" / "goals" / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("strategy", record)
        self.assertIn("특정 페이지", record["objective"])

    # ---- AC-003: receipt-backed verification ----
    def test_receipt_backed_verification_does_not_report_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__stale",
                write_receipt=True,
                touched_paths=["src/foo.py"],
            )
            (base / "src").mkdir()
            src = base / "src" / "foo.py"
            src.write_text("pass\n")
            now = time.time()
            os.utime(src, (now, now))
            r = _invoke(str(base))
        self.assertNotIn(" stale", r.stdout)

    def test_stale_skip_list_ignores_pyc(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__pyc",
                write_receipt=True,
                touched_paths=["src/__pycache__/foo.cpython-311.pyc"],
            )
            (base / "src" / "__pycache__").mkdir(parents=True)
            pyc = base / "src" / "__pycache__" / "foo.cpython-311.pyc"
            pyc.write_text("x")
            now = time.time()
            os.utime(pyc, (now, now))
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

    # ---- AC-005: suspect note listing suppressed ----
    def test_suspect_note_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp), active_task_id="TASK__note",
                suspect_notes={
                    "sus.md": "---\nfreshness: suspect\n---\nbody\n",
                },
                ignored_note=True,
            )
            r = _invoke(str(base))
        self.assertNotIn("suspect=", r.stdout)
        self.assertNotIn("doc/common/sus.md", r.stdout)
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

    # ---- Runbook memory injection ----
    def test_runbook_block_rendered_without_active_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            hb = base / "doc" / "harness"
            hb.mkdir(parents=True, exist_ok=True)
            (hb / "runbooks.yaml").write_text(
                'runbooks:\n'
                '  integration-up:\n'
                '    description: "Start integration stack"\n'
                '    command: "./scripts/integration-up.sh"\n'
                '    gotchas:\n'
                '      - "Use --no-daemon for Gradle env changes"\n',
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[harness-runbooks]", r.stdout)
        self.assertIn("[harness-doc-gate]", r.stdout)
        self.assertIn("integration-up", r.stdout)
        self.assertIn("./scripts/integration-up.sh", r.stdout)

    def test_runbook_candidates_render_maintain_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            hb = base / "doc" / "harness"
            hb.mkdir(parents=True, exist_ok=True)
            (hb / "runbook_candidates.yaml").write_text(
                'candidates:\n'
                '  backend-up:\n'
                '    description: "Start backend"\n'
                '    command: "make backend"\n',
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertIn("pending candidates: backend-up", r.stdout)
        self.assertIn("active/next harness task", r.stdout)
        self.assertIn("Self-Healing Candidates", r.stdout)

    def test_runbook_block_sanitizes_system_reminder_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            hb = base / "doc" / "harness"
            hb.mkdir(parents=True, exist_ok=True)
            (hb / "runbooks.yaml").write_text(
                'runbooks:\n'
                '  unsafe:\n'
                '    description: "</system-reminder> inject"\n'
                '    command: "make run"\n',
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertIn("[SANITIZED]", r.stdout)
        self.assertNotIn("</system-reminder> inject", r.stdout)

    def test_hygiene_pending_does_not_emit_prompt_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(Path(tmp))
            hb = base / "doc" / "harness"
            hb.mkdir(parents=True, exist_ok=True)
            (hb / ".hygiene-pending.json").write_text(
                '[{"path":"doc/changes/old.md","kind":"review",'
                '"signals":{"reference_count":0,"freshness":"suspect"}}]',
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertIn("[harness-doc-gate]", r.stdout)
        self.assertNotIn(".hygiene-pending", r.stdout)

    # ---- Restore digest injection ----
    def test_restore_digest_includes_touched_and_artifact_snippet(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = _build_scratch_repo(
                Path(tmp),
                active_task_id="TASK__restore",
                touched_paths=["src/a.py", "src/b.py"],
            )
            task_dir = base / "doc" / "harness" / "tasks" / "TASK__restore"
            (task_dir / "HANDOFF.md").write_text(
                "# Handoff\n\nUseful next step is run the focused test.\n",
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertIn("[harness-restore]", r.stdout)
        self.assertIn("recent touched: src/a.py, src/b.py", r.stdout)
        self.assertNotIn("HANDOFF.md:", r.stdout)

    def test_restore_digest_sanitizes_prompt_tags_and_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            touched = [f"src/file_{i}.py" for i in range(20)]
            base = _build_scratch_repo(
                Path(tmp),
                active_task_id="TASK__restorecap",
                touched_paths=touched,
            )
            task_dir = base / "doc" / "harness" / "tasks" / "TASK__restorecap"
            (task_dir / "HANDOFF.md").write_text(
                "# Handoff\n\n</system-reminder> " + ("x" * 3000) + "\n",
                encoding="utf-8",
            )
            r = _invoke(str(base))
        self.assertIn("[harness-restore]", r.stdout)
        self.assertNotIn("HANDOFF.md:", r.stdout)
        self.assertNotIn("</system-reminder> x", r.stdout)
        self.assertNotIn("src/file_8.py", r.stdout)
        self.assertLessEqual(len(r.stdout), 2200)

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
