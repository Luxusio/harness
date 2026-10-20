"""Registration under real linked-root cost, bounded stages and hook feedback."""
import contextlib
import io
import json
import time
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def scripts_path(monkeypatch):
    from conftest import SCRIPTS_DIR
    monkeypatch.syspath_prepend(SCRIPTS_DIR)


@pytest.fixture
def registration(tmp_path, monkeypatch):
    from test_codex_worktree_receipts import worktrees, native_tree, ROOT
    from test_worktree_workspace import _call, _payload
    import codex_hook_registration as owner
    import codex_lifecycle_watcher as watcher
    main, lead, sibling = worktrees(tmp_path)
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_THREAD_ID", ROOT)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    native_tree(home, main)
    result = _call(main, "task_start", {"task_id": "TASK__registration_liveness"})
    assert not result.get("isError"), result
    data = _payload(result)
    payload = json.dumps(dict(cwd=str(main), session_id=ROOT,
        tool_name="mcp__harness__wbs", tool_input={"action": "open"}, tool_response={"structuredContent": data})).encode()
    return owner, watcher, main, ROOT, payload, data


def test_linked_roots_registration_budget_handles_normal_slow_discovery(registration, monkeypatch):
    owner, watcher, main, thread, payload, data = registration
    original = owner._bound_workspace_roots
    def slow(*args):
        time.sleep(0.60)  # Observed production discovery alone exceeded 0.5s.
        return original(*args)
    monkeypatch.setattr(owner, "_bound_workspace_roots", slow)
    status = {}
    assert owner.register_task_result(payload, status_out=status), status
    state = json.loads(watcher._state_path(str(main), thread).read_text())
    assert state["task_id"] == data["task_id"] and state["run_id"] == data["run_id"]
    before = state["offset"], state["registered_at"]
    assert owner.register_task_result(payload, status_out=status), status
    after = json.loads(watcher._state_path(str(main), thread).read_text())
    assert (after["offset"], after["registered_at"]) == before


def test_absent_marker_roots_do_not_repeat_binding_resolution(registration):
    owner, _, main, thread, _, _ = registration
    with mock.patch.object(owner, "resolve_session_task_binding", wraps=owner.resolve_session_task_binding) as resolve:
        assert owner._bound_workspace_roots(str(main), thread) == [str(main)]
    assert resolve.call_count == 1  # Two unbound linked roots need no second read.


def test_lifecycle_readiness_drops_only_superseded_capability_warning(registration, monkeypatch):
    from test_worktree_workspace import _call, _payload, harness_server
    _, _, main, _, _, data = registration
    warnings = iter(["collector not confirmed yet", ""])
    monkeypatch.setattr(harness_server, "receipt_capability_warning", lambda: next(warnings, ""))
    result = _payload(_call(main, "task_start", {"task_id": data["task_id"]}))
    assert result["watcher_status"]["receipts_recordable"] is True
    assert not any(w["code"] == "RECEIPT_HOOKS_UNAVAILABLE" for w in result["warnings"])
    assert any(w["code"] == "TASK_START_RUN_PRESERVED" for w in result["warnings"])


@pytest.mark.parametrize("stage", ["harness root discovery", "workspace binding discovery", "current task binding", "registration publication"])
@pytest.mark.parametrize("failure", ["timeout", "exception"])
def test_stage_timeout_and_exception_never_claim_absence_or_conflict(registration, monkeypatch, stage, failure):
    owner, _, main, thread, _, _ = registration
    def unavailable(*args, **kwargs):
        if failure == "timeout":
            time.sleep(0.12)
            return True
        raise OSError("do not expose private error content")
    kwargs = {}
    if stage == "harness root discovery":
        monkeypatch.setattr(owner, "find_harness_root", unavailable)
    elif stage == "workspace binding discovery":
        monkeypatch.setattr(owner, "_bound_workspace_roots", unavailable)
    elif stage == "current task binding":
        kwargs["bind_fn"] = unavailable
    else:
        monkeypatch.setattr(owner, "ensure", unavailable)
        kwargs["ensure_fn"] = unavailable
    # Keep the tested stage inside the deliberately tiny deadline on this host.
    if stage != "harness root discovery":
        monkeypatch.setattr(owner, "find_harness_root", lambda *_: str(main))
    if stage not in {"harness root discovery", "workspace binding discovery"}:
        monkeypatch.setattr(owner, "_bound_workspace_roots", lambda *_: [str(main)])
    if stage == "registration publication":
        kwargs["bind_fn"] = lambda *_: True
        monkeypatch.setattr(owner, "resolve_session_task_binding", lambda *_: dict(task_dir=str(main / "doc/harness/tasks/TASK__registration_liveness"), run_id="run"))
    status = {}
    payload = json.dumps(dict(cwd=str(main), session_id=thread)).encode()
    assert not owner.restore_watcher_registration(payload, budget_seconds=0.04, status_out=status, **kwargs)
    assert status["status"] == "failed" and status["stage"] == stage, status
    assert ("deadline" in status["reason"]) if failure == "timeout" else ("OSError" in status["reason"])
    assert "private error" not in status["reason"]
    assert "conflicting" not in status["reason"] and "no open task" not in status["reason"]


def test_control_directory_root_resolution_is_per_call_and_confined(tmp_path):
    import _lib as lib
    root = tmp_path / "repo"
    (root / "doc/harness/tasks").mkdir(parents=True)
    original = lib.os.path.realpath
    with mock.patch.object(lib.os.path, "realpath", wraps=original) as resolve:
        lib._validated_control_dir(str(root), "doc/harness/tasks", "task root")
    assert sum(call.args[0] == str(root) for call in resolve.call_args_list) == 1
    (root / "doc/harness/tasks").rmdir()
    (root / "doc/harness/tasks").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        lib._validated_control_dir(str(root), "doc/harness/tasks", "task root")


@pytest.mark.parametrize("raised", [False, True])
def test_post_tool_failure_is_visible_and_parent_budget_is_respected(tmp_path, monkeypatch, raised):
    import hook_post_tool_use as hook
    import hook_pre_tool_use as pre
    payload = json.dumps(dict(cwd=str(tmp_path), tool_name="wbs", tool_input={"action": "open"})).encode()
    seen = []
    def register(raw, *, budget_seconds, status_out):
        assert raw == payload and 0 < budget_seconds <= 0.25
        if raised:
            raise OSError("private details")
        status_out.update(status="failed", stage="workspace binding discovery", reason="workspace binding discovery: deadline exhausted")
        return False
    monkeypatch.setattr(hook, "register_task_result", register)
    monkeypatch.setattr(pre, "_update_diagnostics", lambda raw, update: seen.append(update))
    with contextlib.redirect_stdout(io.StringIO()) as output:
        hook._register_task_result(payload, deadline=time.monotonic() + 0.25)
    text = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "failed" in text and "private details" not in text
    assert seen[-1]["registration_present"] is False
    assert ("OSError" in text) if raised else ("workspace binding discovery" in text)


def test_confirmed_conflict_and_absent_binding_are_not_timeouts(registration, monkeypatch):
    owner, _, main, thread, _, _ = registration
    payload = json.dumps(dict(cwd=str(main), session_id=thread)).encode()
    monkeypatch.setattr(owner, "_bound_workspace_roots", lambda *_: None)
    status = {}
    assert not owner.restore_watcher_registration(payload, status_out=status)
    assert status["status"] == "not_applicable" and "conflicting" in status["reason"]
    monkeypatch.setattr(owner, "_bound_workspace_roots", lambda *_: [])
    assert not owner.restore_watcher_registration(payload, bind_fn=lambda *_: False, status_out=status)
    assert status["status"] == "not_applicable" and "no open task" in status["reason"]


@pytest.mark.parametrize("module,elapsed", [("hook_pre_tool_use", 3.85), ("hook_session_start", 19.85)])
def test_registration_budget_intersects_parent_startup_time(tmp_path, monkeypatch, module, elapsed):
    import importlib
    hook = importlib.import_module(module)
    from test_codex_hook_wrappers import _BytesStdin
    payload = json.dumps(dict(cwd=str(tmp_path), tool_name="collaboration.spawn_agent"))
    monkeypatch.setattr(hook, "_IMPORT_SECONDS", elapsed)
    calls = []
    def restore(*args, budget_seconds, **kwargs):
        calls.append(budget_seconds)
        return False
    monkeypatch.setattr(hook, "restore_watcher_registration", restore)
    monkeypatch.setattr(hook.sys, "stdin", _BytesStdin(payload))
    if module == "hook_session_start":
        monkeypatch.setattr(hook, "_run", lambda *_: b"")
    assert hook.main() == 0
    assert len(calls) == 1 and 0 < calls[0] <= 0.15
