"""Exact-session resume preserves task identity and never borrows shared hints."""
from __future__ import annotations
import os
from unittest import mock
from test_harness_mcp_server import harness_server as server, harness_lib as lib, call_execution

SESSION_A = "a1b2c3d4-5e6f-7890-abcd-ef1234567890"
SESSION_B = "d1866f9e-1d00-4179-91f8-dc676f461d3e"
TASK_ID = "TASK__resume"

def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "doc/harness").mkdir(parents=True)
    (root / "doc/harness/manifest.yaml").write_text("version: 5\ntype: library\n")
    return root

def _call(repo, action, task=TASK_ID, sid=SESSION_A, **args):
    with mock.patch.object(server, "find_repo_root", return_value=str(repo)), mock.patch.dict(
        os.environ, {"HARNESS_SESSION_ID": sid, "CLAUDE_SESSION_ID": "", "HARNESS_RUNTIME": "claude"},
    ):
        return call_execution(server, action, {"task_id": task, **args})

def _task(repo, task=TASK_ID):
    return repo / "doc/harness/tasks" / task

def _marker(repo, sid=SESSION_A):
    return repo / "doc/harness/tasks/.active_sessions" / (sid + ".json")

def test_resume_binds_new_session_without_changing_control(tmp_path):
    repo = _repo(tmp_path)
    assert not _call(repo, "task_start").get("isError")
    control = (_task(repo) / "TASK.json").read_bytes()
    assert not _call(repo, "task_context", sid=SESSION_B).get("isError")
    assert (_task(repo) / "TASK.json").read_bytes() == control
    for sid in (SESSION_A, SESSION_B):
        assert lib.resolve_session_task_binding(str(repo), sid)["task_dir"] == str(_task(repo))
    assert not (repo / "doc/harness/tasks/.active").exists()

def test_reading_a_parked_task_does_not_steal_write_focus(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    assert not _call(repo, "task_blocked", blocked_reason="waiting", unblock_condition="resume").get("isError")
    assert not _marker(repo).exists()
    _call(repo, "task_context", sid=SESSION_B)
    assert lib.resolve_session_task_binding(str(repo), SESSION_B) == {}
    assert (_task(repo) / "BLOCKED.md").exists()

def test_a_session_holding_nothing_binds_on_resume(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    assert not _call(repo, "task_start", sid=SESSION_B).get("isError")
    assert lib.resolve_session_task_binding(str(repo), SESSION_B)["task_dir"] == str(_task(repo))

def test_a_refused_marker_write_is_reported_not_swallowed(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    with mock.patch.object(server, "write_active_marker", side_effect=OSError("marker unavailable")):
        result = _call(repo, "task_context", sid=SESSION_B)
    assert result.get("isError")
    assert "marker unavailable" in result["structuredContent"]["error"]
    assert lib.resolve_session_task_binding(str(repo), SESSION_A)

def test_reading_another_open_task_does_not_steal_write_focus(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    _call(repo, "task_start", task="TASK__other", sid=SESSION_B)
    before = _marker(repo).read_bytes()
    assert not _call(repo, "task_context", task="TASK__other").get("isError")
    assert _marker(repo).read_bytes() == before
    assert lib.resolve_session_task_binding(str(repo), SESSION_A)["task_dir"] == str(_task(repo))

def test_a_peek_without_identity_does_not_borrow_hint_or_legacy_focus(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    lib.write_session_hint(str(repo), SESSION_B)
    legacy = repo / "doc/harness/tasks/.active"
    legacy.write_text(str(_task(repo)))
    before = _marker(repo).read_bytes()
    assert not _call(repo, "task_context", sid="").get("isError")
    assert _marker(repo).read_bytes() == before
    assert not _marker(repo, SESSION_B).exists()
    assert not _marker(repo, "default").exists()
    assert legacy.read_text() == str(_task(repo))

def test_resuming_the_same_task_refreshes_a_rotated_run_id(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    first = lib.read_task_control(str(_task(repo)))["run_id"]
    assert not _call(repo, "task_start", sid=SESSION_B, fresh_run=True).get("isError")
    second = lib.read_task_control(str(_task(repo)))["run_id"]
    assert first != second
    assert lib.resolve_session_task_binding(str(repo), SESSION_A) == {}
    assert not _call(repo, "task_context").get("isError")
    assert lib.resolve_session_task_binding(str(repo), SESSION_A)["run_id"] == second

def test_a_legacy_pointer_to_an_unvalidatable_task_does_not_block_resume(tmp_path):
    repo = _repo(tmp_path)
    _call(repo, "task_start")
    legacy = repo / "doc/harness/tasks/.active"
    legacy.write_text("TASK__gone\n")
    assert not _call(repo, "task_start", sid=SESSION_B).get("isError")
    assert lib.resolve_session_task_binding(str(repo), SESSION_B)["task_dir"] == str(_task(repo))
    assert legacy.read_text() == "TASK__gone\n"
