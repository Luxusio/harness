"""Real linked-checkout task publication and locking guards."""
import json
import subprocess
from unittest import mock

LEAD = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"

def worktrees(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(main), *args], check=True, capture_output=True)
    git("init")
    manifest = main / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: library\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "initial")
    lead = tmp_path / "lead"
    sibling = tmp_path / "sibling"
    git("worktree", "add", "-b", "lead", str(lead))
    git("worktree", "add", "-b", "sibling", str(sibling))
    return main, lead, sibling

def test_marker_writers_lock_session_before_task_control_in_deferred_routes(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from test_worktree_workspace import _setup, _call, harness_server
    main, lead = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    real_session = harness_server.active_session_transaction
    real_control = harness_server.task_control_transaction
    held = []
    observed = []

    @contextmanager
    def session(root):
        with real_session(root):
            held.append(root)
            try:
                yield
            finally:
                held.pop()

    @contextmanager
    def control(task):
        assert any(str(task).startswith(root + "/") for root in held), (task, held)
        observed.append(str(task))
        with real_control(task):
            yield

    with mock.patch.object(harness_server, "active_session_transaction", side_effect=session), mock.patch.object(harness_server, "task_control_transaction", side_effect=control):
        for workspace in (None, str(lead)):
            args = {"task_id": "TASK__locks"}
            if workspace:
                args["workspace"] = workspace
            assert not _call(main, "task_start", args).get("isError")
            assert not _call(main, "task_context", args).get("isError")
            assert _call(main, "task_close", args).get("isError")  # no verification yet
            assert not _call(main, "task_blocked", dict(args, blocked_reason="park", unblock_condition="resume")).get("isError")
    assert {str(main / "doc/harness/tasks/TASK__locks"), str(lead / "doc/harness/tasks/TASK__locks")} <= set(observed)

def test_task_start_rollback_cannot_replace_a_new_conflict_fence(tmp_path, monkeypatch):
    from test_worktree_workspace import _setup, _call, harness_server
    main, _ = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_SESSION_ID", LEAD)
    marker = main / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
    fence = {"session_id": LEAD, "conflicts": [], "conflicts_overflow": True}

    def publish_then_fail(*args, **kwargs):
        # Simulate a fence becoming current after the rollback snapshot. The
        # production session lock prevents a concurrent hook from doing this.
        marker.write_text(json.dumps(fence))
        raise RuntimeError("publication failed")

    with mock.patch.object(harness_server, "write_active_marker", side_effect=publish_then_fail):
        result = _call(main, "task_start", {"task_id": "TASK__rollback_fence"})
    assert result.get("isError"), result
    assert json.loads(marker.read_text()) == fence
