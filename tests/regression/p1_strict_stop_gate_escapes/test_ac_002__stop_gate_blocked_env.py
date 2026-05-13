"""AC-002 regression: stop_gate.py permits stop when runtime_verdict=BLOCKED_ENV.

The stop-judge agent transitions runtime_verdict to BLOCKED_ENV via
write_critic_qa(lens="stop-judge") to signal a legitimate paused-with-blocker
state. The Stop hook must then permit the next stop event silently (no block
payload on stdout).
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap


SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "..", "plugin", "scripts")
SCRIPTS = os.path.abspath(SCRIPTS)


def _setup_task_dir(repo_root: str, verdict: str) -> str:
    """Create a minimal active task dir with the given runtime_verdict."""
    tasks_root = os.path.join(repo_root, "doc", "harness", "tasks")
    task_dir = os.path.join(tasks_root, "TASK__test_blocked_env")
    os.makedirs(task_dir, exist_ok=True)
    # Minimal TASK_STATE.yaml — 7 required fields per _lib.SCHEMA_FIELDS.
    state = textwrap.dedent(f"""\
        task_id: TASK__test_blocked_env
        status: implementing
        runtime_verdict: {verdict}
        touched_paths: []
        plan_session_state: closed
        closed_at: null
        updated: 2026-05-13T00:00:00Z
        """)
    with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
        f.write(state)
    # Plan.md presence prevents "PLAN.md missing" entry in missing_for_close.
    with open(os.path.join(task_dir, "PLAN.md"), "w") as f:
        f.write("# Test plan\n")
    # .active marker points at the task dir (absolute path per active-marker convention).
    with open(os.path.join(tasks_root, ".active"), "w") as f:
        f.write(task_dir + "\n")
    return task_dir


def _run_stop_gate(repo_root: str) -> tuple[int, str]:
    """Invoke stop_gate.py with stdin drained; return (exit_code, stdout)."""
    # Make stop_gate think this is the repo root by chdir + initing .git.
    os.makedirs(os.path.join(repo_root, ".git"), exist_ok=True)
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "stop_gate.py")],
        cwd=repo_root,
        input="",
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout


def test_blocked_env_silent_allow():
    """BLOCKED_ENV verdict -> stop_gate emits no block payload (silent allow)."""
    with tempfile.TemporaryDirectory() as repo:
        _setup_task_dir(repo, "BLOCKED_ENV")
        code, out = _run_stop_gate(repo)
        assert code == 0, f"stop_gate exit {code}"
        assert out.strip() == "", f"expected silent allow, got: {out!r}"


def test_pending_blocks_with_no_cancel_push():
    """PENDING verdict -> block payload, but reason must NOT mention cancel-push."""
    with tempfile.TemporaryDirectory() as repo:
        _setup_task_dir(repo, "pending")
        code, out = _run_stop_gate(repo)
        assert code == 0, f"stop_gate exit {code}"
        assert out.strip(), "expected block payload"
        payload = json.loads(out)
        assert payload.get("decision") == "block"
        # AC-001 regression: cancel-push escape must be absent from reason text.
        reason = payload.get("reason", "")
        assert "cancel the task" not in reason, \
            "cancel-push escape leaked back into stop_gate reason"
        assert "stop-judge" in reason, \
            "stop-judge agent guidance missing from reason"


if __name__ == "__main__":
    test_blocked_env_silent_allow()
    test_pending_blocks_with_no_cancel_push()
    print("OK")
