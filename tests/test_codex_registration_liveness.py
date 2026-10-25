"""Registration under real linked-root cost, bounded stages and hook feedback."""
import contextlib
import io
import json
import time
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


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


def test_lifecycle_readiness_retains_warning_until_live_generation_confirmed(registration, monkeypatch):
    from test_worktree_workspace import _call, _payload, harness_server
    _, _, main, _, _, data = registration
    warnings = iter(["collector not confirmed yet", ""])
    monkeypatch.setattr(harness_server, "receipt_capability_warning", lambda: next(warnings, ""))
    result = _payload(_call(main, "task_start", {"task_id": data["task_id"]}))
    assert result["watcher_status"]["registration_present"] is True
    assert result["watcher_status"]["receipts_recordable"] is None
    assert any(w["code"] == "RECEIPT_HOOKS_UNAVAILABLE" for w in result["warnings"])
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
        monkeypatch.setattr(owner, "resolve_session_task_binding", lambda *_, **kw: dict(task_dir=str(main / "doc/harness/tasks/TASK__registration_liveness"), run_id="run"))
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
    def register(raw, *, budget_seconds, authentication_budget_seconds, status_out):
        assert raw == payload and 0 < budget_seconds <= 0.25
        assert authentication_budget_seconds == 0
        if raised:
            raise OSError("private details")
        status_out.update(status="failed", stage="workspace binding discovery", reason="workspace binding discovery: deadline exhausted")
        return False
    monkeypatch.setattr(hook, "register_task_result", register)
    monkeypatch.setattr(pre, "_update_diagnostics", lambda raw, update: seen.append(update))
    with contextlib.redirect_stdout(io.StringIO()) as output:
        hook._register_task_result(payload, deadline=time.monotonic() + hook.DEADLINE_MARGIN_SECONDS + 0.25)
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


@pytest.mark.parametrize("module", ["hook_pre_tool_use", "hook_session_start"])
def test_registration_budget_intersects_parent_startup_time(tmp_path, monkeypatch, module):
    import importlib
    hook = importlib.import_module(module)
    from test_codex_hook_wrappers import _BytesStdin
    payload = json.dumps(dict(cwd=str(tmp_path), tool_name="collaboration.spawn_agent"))
    margin = 1.0 if module == "hook_pre_tool_use" else 0.2
    monkeypatch.setattr(hook, "_IMPORT_SECONDS", hook.HOOK_TIMEOUT_SECONDS - margin - 0.15)
    calls = []
    def restore(*args, budget_seconds, **kwargs):
        calls.append(budget_seconds)
        return False
    monkeypatch.setattr(hook, "restore_watcher_registration", restore)
    monkeypatch.setattr(hook.sys, "stdin", _BytesStdin(payload))
    if module == "hook_session_start":
        monkeypatch.setattr(hook, "_run", lambda *_, **kw: b"")
    assert hook.main() == 0
    assert len(calls) == 1 and 0 < calls[0] <= 0.15


def test_unreadable_marker_is_unavailable_at_production_open_boundary(registration, monkeypatch):
    import _lib as lib
    owner, _, main, thread, _, _ = registration
    marker = str(main / "doc/harness/tasks/.active_sessions" / (thread + ".json"))
    original = os.open
    def opening(path, *args, **kwargs):
        if os.fspath(path) == marker:
            raise PermissionError("private path details")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(lib.os, "open", opening)
    status = {}
    payload = json.dumps(dict(cwd=str(main), session_id=thread)).encode()
    assert not owner.restore_watcher_registration(payload, status_out=status)
    assert status["status"] == "failed" and status["stage"] == "workspace binding discovery"
    assert "PermissionError" in status["reason"] and "private path" not in status["reason"]


@pytest.mark.parametrize("lost_at", [1, 2, 3])
def test_post_tool_empty_registry_at_each_publication_read_is_visible(registration, monkeypatch, lost_at):
    import hook_post_tool_use as hook
    owner, watcher, main, _, payload, _ = registration
    roots = watcher.workspace_roots(str(main))
    calls = []
    def registry(*args):
        calls.append(True)
        return [] if len(calls) >= lost_at else roots
    monkeypatch.setattr(owner, "workspace_roots", registry)
    monkeypatch.setattr(hook, "register_task_result", owner.register_task_result)
    status = {}
    assert not owner.register_task_result(payload, status_out=status)
    assert status["status"] == "failed" and status["stage"] == "workspace registry discovery"
    calls.clear()
    with contextlib.redirect_stdout(io.StringIO()) as output:
        hook._register_task_result(payload, deadline=time.monotonic() + hook.TOTAL_BUDGET_SECONDS)
    assert "workspace registry discovery" in output.getvalue()


def test_persistent_capability_warning_is_disproved_by_valid_current_run_receipt():
    from test_receipt_watcher_fail_closed import _task_with_receipt, _server
    server = _server()
    with _task_with_receipt() as (task, run):
        with mock.patch.object(server, "receipt_capability_warning", return_value="collector not confirmed"), \
             mock.patch.object(server, "_server_runtime", return_value="claude"), \
             mock.patch.object(server, "_SERVER", None), \
             mock.patch.object(server, "_diagnostics_for_this_session", return_value={}) as diagnostics:
            status = server._watcher_status(task_dir=task, run_id=run)
            assert status["receipt_capability_warning"] == "" and status["receipts_recordable"] is True
            diagnostics.return_value = {"last_watcher_error": "live collector failed"}
            status = server._watcher_status(task_dir=task, run_id=run)
            assert status["receipt_capability_warning"] == "" and status["receipts_recordable"] is False


def test_post_tool_slow_diagnostics_cannot_hide_failure_or_exceed_deadline():
    from conftest import SCRIPTS_DIR
    code = '''
import sys,time,json
sys.path.insert(0,sys.argv[1])
import hook_post_tool_use as hook
import hook_pre_tool_use as pre
def failure(*args,**kwargs):
    kwargs['status_out'].update(status='failed',reason='registration rejected')
    return False
hook.register_task_result=failure
pre._update_diagnostics=lambda *a:time.sleep(1.3)
started=time.monotonic()
hook._register_task_result(b'{}',deadline=started+0.2)
print()
print(json.dumps({'elapsed':time.monotonic()-started}))
'''
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS_DIR)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    notice, timing = map(json.loads, result.stdout.splitlines())
    assert "failed" in notice["hookSpecificOutput"]["additionalContext"]
    assert timing["elapsed"] < 0.7


def test_deadline_interrupted_diagnostic_write_cleans_owned_temp(tmp_path, monkeypatch):
    import _lib as lib
    import codex_hook_registration as owner
    original = os.write
    def slow_write(*args):
        time.sleep(0.2)
        return original(*args)
    monkeypatch.setattr(lib.os, "write", slow_write)
    path = tmp_path / "diagnostic.json"
    assert not owner._call_with_deadline(lambda: lib.write_json_diagnostics(str(path), {"status": "failed"}), time.monotonic() + 0.03)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("slow_stage", ["failure diagnostics", "live host", "success diagnostics", "unknown diagnostics"])
def test_pre_tool_slow_advisory_work_is_bounded_and_failure_visible(slow_stage):
    from conftest import SCRIPTS_DIR
    code = '''
import sys,time,json,io
sys.path.insert(0,sys.argv[1])
import hook_pre_tool_use as hook
stage=sys.argv[2]
hook.HOOK_TIMEOUT_SECONDS=1.4
hook._IMPORT_SECONDS=0
hook.sys.stdin=io.TextIOWrapper(io.BytesIO(b'{"tool_name":"collaboration.spawn_agent"}'))
def restore(*a,**kw):
    kw['status_out'].update(status='not_applicable' if stage=='unknown diagnostics' else 'failed',reason='registration rejected')
    return stage in ('live host','success diagnostics')
hook.restore_watcher_registration=restore
hook._watcher_host_live=lambda *a: (time.sleep(10) or True) if stage=='live host' else True
hook._update_diagnostics=lambda *a:time.sleep(10)
hook._observed_registration_failure=lambda *a:False
started=time.monotonic()
hook.main()
print(json.dumps({'elapsed':time.monotonic()-started}))
'''
    # Startup includes real source imports; the child separately bounds hook work.
    result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS_DIR), slow_stage], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["elapsed"] < 1.9
    if slow_stage in {"failure diagnostics", "live host"}:
        assert "receipt watcher registration failed" in result.stderr


@pytest.mark.parametrize("kind", ["neutral", "partial", "mismatch", "stale"])
def test_strict_binding_distinguishes_neutral_corrupt_and_stale_markers(registration, kind):
    import _lib as lib
    _, _, main, thread, _, _ = registration
    path = main / "doc/harness/tasks/.active_sessions" / (thread + ".json")
    marker = json.loads(path.read_text())
    if kind == "neutral":
        marker = {"session_id": thread, "updated": marker.get("updated")}
    elif kind == "partial":
        marker.pop("task_dir")
    elif kind == "mismatch":
        marker["task_id"] = "TASK__wrong"
    else:
        marker["run_id"] = "stale-run"
    path.write_text(json.dumps(marker))
    if kind in {"partial", "mismatch"}:
        with pytest.raises(RuntimeError, match="binding malformed"):
            lib.resolve_session_task_binding(str(main), thread, strict=True)
    else:
        assert lib.resolve_session_task_binding(str(main), thread, strict=True) == {}


def test_conflict_helpers_reuse_locked_registry_and_reject_unavailable_discovery(registration, monkeypatch):
    from test_codex_lifecycle_watcher import _write_exact_session_binding
    owner, watcher, main, thread, payload, data = registration
    roots = watcher.workspace_roots(str(main))
    other = roots[1]
    other_task, other_run = _write_exact_session_binding(Path(other), thread)
    conflicts = [dict(task_dir=data["task_dir"], task_id=data["task_id"], run_id=data["run_id"]),
                 dict(task_dir=os.path.join(other, "doc/harness/tasks", other_task), task_id=other_task, run_id=other_run)]
    marker_path = main / "doc/harness/tasks/.active_sessions" / (thread + ".json")
    marker_path.write_text(json.dumps(dict(session_id=thread, conflicts=conflicts)))
    # Only initial discoveries and the locked refresh are available. Helpers
    # must use that locked snapshot instead of treating another [] as no peers.
    calls = []
    def registry(*a):
        calls.append(True)
        return roots if len(calls) <= 3 else []
    monkeypatch.setattr(owner, "workspace_roots", registry)
    status = {}
    assert not owner.register_task_result(payload, status_out=status)
    assert len(calls) == 3
    assert status["status"] == "not_applicable" and "conflicting" in status["reason"]
    assert len(json.loads(marker_path.read_text())["conflicts"]) == 2
    assert not watcher.registrations(str(main))
    for helper in (owner._live_conflicts, owner._conflict_task_dirs):
        with pytest.raises(RuntimeError, match="registry unavailable"):
            helper(str(main), dict(conflicts=conflicts))


def test_mcp_focus_uses_locked_registry_without_false_overflow(registration, monkeypatch):
    from test_worktree_workspace import _call, harness_server as server
    _, watcher, main, thread, _, data = registration
    roots = watcher.workspace_roots(str(main))
    monkeypatch.setattr(server, "_task_roots", lambda args: (str(main), str(main)))
    monkeypatch.setattr(server, "_current_session_identity", lambda root: thread)
    monkeypatch.setattr(server, "_server_runtime", lambda: "codex")
    calls = []
    def registry(*args):
        calls.append(True)
        return roots if len(calls) <= 2 else []
    monkeypatch.setattr(server, "_codex_workspace_roots", registry)
    # The helper must receive the locked snapshot, not discover a third time.
    helper = server._codex_live_conflicts
    def conflicts(root, marker, *, roots=None):
        assert roots
        return helper(root, marker, roots=roots)
    monkeypatch.setattr(server, "_codex_live_conflicts", conflicts)
    result = _call(main, "wbs", {"action": "context", "wbs_id": data["task_id"]})
    assert not result.get("isError"), result
    assert result["structuredContent"]["task_id"] == data["task_id"]
    assert len(calls) == 2


@pytest.mark.skipif(sys.platform != "linux", reason="forked actor and POSIX registration locks")
def test_parent_proof_preparation_does_not_block_another_linked_actor(registration, monkeypatch):
    """A slow real child proof must not monopolize repository binding locks."""
    import multiprocessing
    from test_codex_worktree_receipts import LEAD
    owner, watcher, main, root_id, root_payload, data = registration
    context = multiprocessing.get_context("fork")
    entered, release = context.Event(), context.Event()
    results = context.Queue()
    original_spool = watcher.tempfile.SpooledTemporaryFile
    original_session = owner.active_session_transaction
    original_receipt = owner.receipt_stream_transaction

    def child():
        os.environ["CODEX_THREAD_ID"] = LEAD
        watcher._PARENT_SPAWN_CACHE.clear()
        locks = []
        proof_lock_depths = []

        @contextlib.contextmanager
        def tracked(factory, *args, **kwargs):
            with factory(*args, **kwargs) as value:
                locks.append(True)
                try:
                    yield value
                finally:
                    locks.pop()

        def slow_proof(*args, **kwargs):
            proof_lock_depths.append(len(locks))
            entered.set()
            if not release.wait(8):
                raise RuntimeError("test coordinator did not release proof")
            return original_spool(*args, **kwargs)

        owner.active_session_transaction = lambda *a, **kw: tracked(original_session, *a, **kw)
        owner.receipt_stream_transaction = lambda *a, **kw: tracked(original_receipt, *a, **kw)
        watcher.tempfile.SpooledTemporaryFile = slow_proof
        payload = json.dumps(dict(cwd=str(main), session_id=LEAD,
            tool_name="mcp__harness__wbs", tool_input={"action": "open"},
            tool_response={"structuredContent": data})).encode()
        status = {}
        try:
            success = owner.register_task_result(payload, budget_seconds=2, status_out=status)
            results.put((success, status, proof_lock_depths))
        except BaseException as error:
            results.put((False, {"error": repr(error)}, proof_lock_depths))

    actor = context.Process(target=child)
    actor.start()
    try:
        assert entered.wait(5), "child never reached the real parent-history reader"
        status = {}
        # This executes real marker, conflict, registry and watcher admission.
        # The other process remains paused in its actual ancestry reader.
        concurrent = owner.register_task_result(root_payload, budget_seconds=0.7, status_out=status)
        release.set()
        actor.join(10)
        assert not actor.is_alive(), "child registration did not finish"
        success, child_status, lock_depths = results.get(timeout=2)
        assert concurrent, status
        assert success, child_status
        assert lock_depths == [0], "ancestry was reread or scanned inside shared publication locks"
        root_state = json.loads(watcher._state_path(str(main), root_id).read_text())
        child_state = json.loads(watcher._state_path(str(main), LEAD).read_text())
        assert root_state["run_id"] == child_state["run_id"] == data["run_id"]
        assert child_state["task_id"] == data["task_id"]
    finally:
        release.set()
        actor.join(2)
        if actor.is_alive():
            actor.terminate()
            actor.join(2)
        results.close()


@pytest.mark.parametrize("change", ["append", "rewrite", "replace", "missing"])
def test_cache_only_parent_proof_refuses_generation_changes_without_reading(registration, monkeypatch, change):
    from test_codex_worktree_receipts import LEAD
    owner, watcher, main, _, _, _ = registration
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    watcher._PARENT_SPAWN_CACHE.clear()
    assert owner.authenticate_native_session(LEAD, str(main)) == str(main)
    parent = watcher._find_rollout(registration[3])
    assert parent is not None
    original = parent.read_bytes()
    if change == "append":
        with parent.open("ab") as handle:
            handle.write(b'{"type":"unrelated"}\n')
    elif change == "rewrite":
        # Same size: a size-only cache must not authorize the changed parent.
        parent.write_bytes(original.replace(b'"thread_source": "user"', b'"thread_source": "fake"'))
        assert parent.stat().st_size == len(original)
    elif change == "replace":
        replacement = parent.with_suffix(".replacement")
        replacement.write_bytes(original)
        os.replace(replacement, parent)
    else:
        watcher._PARENT_SPAWN_CACHE.clear()
    with mock.patch.object(watcher.tempfile, "SpooledTemporaryFile", side_effect=AssertionError("full ancestry read during publication")):
        with watcher._cached_parent_proofs():
            assert owner.authenticate_native_session(LEAD, str(main)) == ""
    # Exiting the restricted invocation must allow genuine fresh preparation.
    if change == "rewrite":
        parent.write_bytes(original)
    assert owner.authenticate_native_session(LEAD, str(main)) == str(main)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_cache_only_proof_mode_resets_after_exception(registration, monkeypatch, error_type):
    from test_codex_worktree_receipts import LEAD
    owner, watcher, main, _, _, _ = registration
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    watcher._PARENT_SPAWN_CACHE.clear()
    with pytest.raises(error_type):
        with watcher._cached_parent_proofs():
            assert owner.authenticate_native_session(LEAD, str(main)) == ""
            raise error_type("cancel publication")
    assert owner.authenticate_native_session(LEAD, str(main)) == str(main)


@pytest.mark.parametrize("explicit_workspace", [False, True])
def test_prepared_native_registration_keeps_future_only_offset(registration, monkeypatch, explicit_workspace):
    from test_codex_worktree_receipts import LEAD
    from test_worktree_workspace import _call, harness_server as server
    owner, watcher, main, _, _, data = registration
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    watcher._PARENT_SPAWN_CACHE.clear()
    locks, history_reads = [], []
    original_spool = watcher.tempfile.SpooledTemporaryFile

    @contextlib.contextmanager
    def tracked(factory, *args, **kwargs):
        with factory(*args, **kwargs) as value:
            locks.append(True)
            try:
                yield value
            finally:
                locks.pop()

    for module in (server, owner):
        for name in ("active_session_transaction", "receipt_stream_transaction"):
            factory = getattr(module, name)
            monkeypatch.setattr(module, name, lambda *a, _factory=factory, **kw: tracked(_factory, *a, **kw))

    def read_history(*args, **kwargs):
        history_reads.append(len(locks))
        assert not locks, "public native open scanned ancestry under focus/receipt locks"
        return original_spool(*args, **kwargs)

    monkeypatch.setattr(watcher.tempfile, "SpooledTemporaryFile", read_history)
    # Native binding is an internal owner invoked by the public MCP boundary.
    args = {"action": "open", "wbs_id": data["task_id"]}
    if explicit_workspace:
        args["workspace"] = str(main)
    response = _call(main, "wbs", args)
    assert not response.get("isError"), response
    assert history_reads == [0]
    state_path = watcher._state_path(str(main), LEAD)
    before = json.loads(state_path.read_text())
    rollout = watcher._find_rollout(LEAD)
    with rollout.open("ab") as handle:
        handle.write(b'{"type":"unrelated"}\n')
    args["action"] = "context"
    response = _call(main, "wbs", args)
    assert not response.get("isError"), response
    after = json.loads(state_path.read_text())
    assert (before["offset"], before["registered_at"]) == (after["offset"], after["registered_at"])


@pytest.mark.parametrize("public_entry", [False, True])
def test_native_authentication_ceiling_also_covers_public_workspace_admission(registration, monkeypatch, public_entry):
    from test_codex_worktree_receipts import LEAD
    from test_worktree_workspace import _call
    owner, watcher, main, _, _, data = registration
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setattr(owner, "AUTHENTICATION_BUDGET_SECONDS", 0.02)
    original = owner._coordinator_identity

    def slow_identity(*args, **kwargs):
        time.sleep(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, "_coordinator_identity", slow_identity)
    started = time.monotonic()
    if public_entry:
        _call(main, "wbs", {"action": "context", "wbs_id": data["task_id"], "workspace": str(main)})
    else:
        assert owner.authenticate_native_session(LEAD, str(main)) == ""
    assert time.monotonic() - started < 0.5
    assert not watcher._state_path(str(main), LEAD).exists()
    assert not (main / "doc/harness/tasks/.active_sessions" / (LEAD + ".json")).exists()
