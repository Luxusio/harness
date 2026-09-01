"""AC-002 regression: stop_gate.py permits a durable task_blocked state.

The direct blocker path publishes a valid BLOCKED.md through task_blocked for a
legitimate paused-with-blocker state. Only that durable blocked task status—not
a lens-level BLOCKED_ENV receipt—permits the next Stop event silently.
"""

import io
import json
import os
import subprocess
import sys
import tempfile


SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "..", "plugin", "scripts")
SCRIPTS = os.path.abspath(SCRIPTS)


def _setup_task_dir(repo_root: str, verdict: str) -> str:
    """Create a minimal active task dir with the given runtime_verdict."""
    tasks_root = os.path.join(repo_root, "doc", "harness", "tasks")
    task_dir = os.path.join(tasks_root, "TASK__test_blocked_env")
    os.makedirs(task_dir, exist_ok=True)
    with open(os.path.join(repo_root, "doc", "harness", "manifest.yaml"), "w") as f:
        f.write("type: test\n")
    with open(os.path.join(task_dir, "TASK.json"), "w") as f:
        json.dump({
            "run_id": "0198c349-5800-7000-8000-000000000001",
            "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "close_receipt_fingerprint": None,
        }, f)
    if verdict == "BLOCKED_ENV":
        with open(os.path.join(task_dir, "BLOCKED.md"), "w") as f:
            f.write("# BLOCKED\n\nEnvironment unavailable.\n")
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
    """Durable blocked task status -> no block payload (silent allow)."""
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
        assert "task_blocked directly" in reason, \
            "direct task_blocked guidance missing from reason"
        assert "stop-judge" not in reason, \
            "deprecated agent routing leaked back into stop_gate reason"


if __name__ == "__main__":
    test_blocked_env_silent_allow()
    test_pending_blocks_with_no_cancel_push()
    print("OK")
