"""Native-format fixtures: nested coordinators and registered Git worktrees."""
import json
import subprocess
from unittest import mock

def pytest_generate_tests(metafunc):
    if "fence_kind" in metafunc.fixturenames:
        metafunc.parametrize("fence_kind", ["normal", "overflow"])
    if "corruption" in metafunc.fixturenames:
        metafunc.parametrize("corruption", ["depth", "parent", "session", "path", "spawn_output", "missing_activity", "prompt_only"])
    if "field" in metafunc.fixturenames:
        metafunc.parametrize("field,value", [("depth", 1), ("parent_thread_id", ROOT), ("agent_path", "/root/sibling/qa_cli")])


ROOT = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
LEAD = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
LENS = "019f82a6-ce64-75a3-b01d-92f7b0b4fe70"


def native_tree(home, cwd):
    from test_codex_lifecycle_watcher import _rollout_path, _write_jsonl, _child_events, _spawn_events
    root = _rollout_path(home, ROOT)
    lead = _rollout_path(home, LEAD)
    lens = _rollout_path(home, LENS)
    root_events = [{"type": "session_meta", "payload": {
        "id": ROOT, "session_id": ROOT, "cwd": str(cwd), "thread_source": "user",
    }}] + _spawn_events(ROOT, LEAD, "lead", "/root/lead", "call_lead_spawn")
    lead_events = _child_events(ROOT, LEAD, "/root/lead", str(cwd))
    lens_events = _child_events(LEAD, LENS, "/root/lead/qa_cli", str(cwd), "VERDICT: PASS\nPassed.")
    meta = lens_events[0]["payload"]
    meta["session_id"] = ROOT
    meta["source"]["subagent"]["thread_spawn"]["depth"] = 2
    lens_events[4]["payload"]["author"] = "/root/lead"
    _write_jsonl(root, root_events)
    _write_jsonl(lead, lead_events)
    _write_jsonl(lens, lens_events)
    return root, lead, lens


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


def test_nested_coordinator_and_lens_emit_only_bound_worktree_receipts(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _load, _write_exact_session_binding, _spawn_events, _snapshot, _delivery
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    main, lead, sibling = worktrees(tmp_path)
    _, lead_rollout, _ = native_tree(home, main)
    task_id, run_id = _write_exact_session_binding(lead, LEAD)
    assert mod.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    assert mod.registrations(str(main)) == []
    assert mod.registrations(str(sibling)) == []
    assert len(mod.registrations(str(lead))) == 1
    assert set(mod.workspace_roots(str(lead))) == {str(main), str(lead), str(sibling)}
    final = "VERDICT: PASS\nPassed."
    receipts = []
    watcher = mod.Watcher(str(lead), LEAD, session_cwd=str(main))
    events = _spawn_events(LEAD, LENS, "qa_cli", "/root/lead/qa_cli", "call_nested_lens")
    with lead_rollout.open("a") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    with mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda task, receipt: receipts.append((task, receipt)) or receipt), mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _: _snapshot([r for _, r in receipts])):
        for event in events:
            watcher.feed(event)
        wrong_recipient = _delivery("/root/lead/qa_cli", final)
        watcher.feed(wrong_recipient)
        assert len(receipts) == 1
        delivery = _delivery("/root/lead/qa_cli", final)
        delivery["payload"]["recipient"] = "/root/lead"
        watcher.feed(delivery)
    assert [r["event"] for _, r in receipts] == ["started", "completed"]
    assert receipts[-1][1]["verdict"] == "PASS"
    assert {task for task, _ in receipts} == {str(lead / "doc/harness/tasks" / task_id)}
    assert not (sibling / "doc/harness/tasks").exists()


def test_nested_coordinator_rejects_unproven_ancestry(tmp_path, monkeypatch, corruption):
    from test_codex_lifecycle_watcher import _load, _write_jsonl
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    root, lead, _ = native_tree(home, tmp_path)
    events = [json.loads(line) for line in lead.read_text().splitlines()]
    meta = events[0]["payload"]
    if corruption == "depth":
        meta["source"]["subagent"]["thread_spawn"]["depth"] = 2
    elif corruption == "parent":
        meta["parent_thread_id"] = LENS
    elif corruption == "session":
        meta["session_id"] = LEAD
    elif corruption == "path":
        meta["agent_path"] = "/root/sibling"
    else:
        parent_events = [json.loads(line) for line in root.read_text().splitlines()]
        if corruption == "spawn_output":
            parent_events[-1]["payload"]["output"] = {"agent_id": "/root/sibling"}
        elif corruption == "missing_activity":
            del parent_events[2]
        else:
            parent_events = parent_events[:1] + [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "spawn lead /root/lead"}]}}]
        _write_jsonl(root, parent_events)
    _write_jsonl(lead, events)
    assert not mod._root_meta(lead, LEAD, str(tmp_path))


def test_separate_native_worktree_root_registration(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _load, _write_exact_session_binding
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    main, lead, _ = worktrees(tmp_path)
    native_tree(home, lead)
    task_id, run_id = _write_exact_session_binding(lead, ROOT)
    assert mod.ensure(str(lead), ROOT, task_id=task_id, run_id=run_id)
    assert mod.registrations(str(lead))[0]["session_cwd"] == str(lead)
    assert not mod.ensure(str(main), ROOT, task_id=task_id, run_id=run_id)


def test_task_result_workspace_echo_and_one_binding(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _write_exact_session_binding
    from test_codex_hook_wrappers import _load as load_hook
    mod = load_hook("codex_hook_registration")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    main, lead, sibling = worktrees(tmp_path)
    task_id, run_id = _write_exact_session_binding(lead, LEAD)
    # Existing helper creates a marker: remove it so PostToolUse owns first bind.
    (lead / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json").unlink()
    payload = {"cwd": str(main), "thread_id": LEAD, "tool_name": "mcp__harness__task_start", "tool_input": {"workspace": str(lead)}, "tool_response": {"structuredContent": {"task_id": task_id, "task_dir": str(lead / "doc/harness/tasks" / task_id), "run_id": run_id, "workspace": str(sibling)}}}
    with mock.patch.object(mod, "_ensure_with_deadline", return_value=True):
        assert not mod.register_task_result(json.dumps(payload).encode())
        payload["tool_response"]["structuredContent"]["workspace"] = str(lead)
        assert mod.register_task_result(json.dumps(payload).encode())
        sibling_task, sibling_run = _write_exact_session_binding(sibling, ROOT)
        payload["tool_input"]["workspace"] = str(sibling)
        payload["tool_response"]["structuredContent"] = {"task_id": sibling_task, "task_dir": str(sibling / "doc/harness/tasks" / sibling_task), "run_id": sibling_run, "workspace": str(sibling)}
        assert not mod.register_task_result(json.dumps(payload).encode())
    fence = json.loads((sibling / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json").read_text())
    assert len(fence["conflicts"]) == 2
    with mock.patch.object(mod, "_ensure_with_deadline", return_value=True):
        assert not mod.restore_watcher_registration(json.dumps({"cwd": str(main), "thread_id": LEAD}).encode())
    assert not mod.resolve_session_task_binding(str(lead), LEAD)
    assert not mod.resolve_session_task_binding(str(sibling), LEAD)
    (lead / "doc/harness/tasks" / task_id / "BLOCKED.md").write_text("blocked\n")
    with mock.patch.object(mod, "_ensure_with_deadline", return_value=True):
        assert mod.register_task_result(json.dumps(payload).encode())
    assert mod.resolve_session_task_binding(str(sibling), LEAD)["run_id"] == sibling_run


def test_shared_manager_caps_workers_across_worktrees(tmp_path):
    from test_codex_lifecycle_watcher import _load
    mod = _load()
    main, lead, sibling = worktrees(tmp_path)
    def registrations(root):
        if root == str(main):
            return []
        return [{"thread_id": LEAD if root == str(lead) else LENS,
                 "rollout": "/fixture-rollout", "offset": 0, "registered_at": 1.0}]
    manager = mod.WatcherManager(str(main), max_workers=1)
    seen = []
    def watch(root, *args, stop_event, **kwargs):
        seen.append(root)
        stop_event.wait(5)
        return 0
    with mock.patch.object(mod, "registrations", side_effect=registrations), mock.patch.object(mod, "watch", side_effect=watch):
        assert manager.scan_once() == 1
        assert manager.scan_once() == 0
        manager.stop()
    assert len(seen) == 1
    assert seen[0] in {str(lead), str(sibling)}


def test_nested_lens_rejects_sibling_or_wrong_depth(tmp_path, monkeypatch, field, value):
    from test_codex_lifecycle_watcher import _load, _write_jsonl
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    _, _, lens = native_tree(home, tmp_path)
    events = [json.loads(line) for line in lens.read_text().splitlines()]
    events[0]["payload"]["source"]["subagent"]["thread_spawn"][field] = value
    _write_jsonl(lens, events)
    assert mod._child_status(LENS, LEAD, "/root/lead/qa_cli", str(tmp_path))[0] == "invalid"


def test_worktree_registration_rejects_removed_git_binding(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _load, _write_exact_session_binding
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    main, lead, _ = worktrees(tmp_path)
    native_tree(home, main)
    task_id, run_id = _write_exact_session_binding(lead, LEAD)
    assert mod.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    (lead / ".git").unlink()
    assert mod.registrations(str(lead)) == []
    assert not mod.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)


def test_cached_parent_proof_rechecks_content_generation(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _load, _write_jsonl
    import os
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    root, lead, _ = native_tree(home, tmp_path)
    with mock.patch.object(mod, "_spawn_call", wraps=mod._spawn_call) as parser:
        assert mod._root_meta(lead, LEAD, str(tmp_path))
        calls = parser.call_count
        assert mod._root_meta(lead, LEAD, str(tmp_path))
        assert parser.call_count == calls
        before = root.stat()
        events = [json.loads(line) for line in root.read_text().splitlines()]
        events[-1]["payload"]["output"] = json.dumps({"agent_id": "/root/evil"})
        _write_jsonl(root, events)
        os.utime(root, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert not mod._root_meta(lead, LEAD, str(tmp_path))
        assert parser.call_count > calls


def test_pre_spawn_host_check_routes_unique_binding_to_worktree(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_codex_lifecycle_watcher import _write_exact_session_binding
    mod = load_hook("hook_pre_tool_use")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    main, lead, sibling = worktrees(tmp_path)
    _write_exact_session_binding(lead, LEAD)
    payload = json.dumps({"cwd": str(main), "thread_id": LEAD}).encode()
    with mock.patch.object(mod, "registration_host_live", return_value=True) as live:
        assert mod._watcher_host_live(payload)
        live.assert_called_once_with(str(lead), LEAD)
        live.reset_mock()
        mismatch = json.dumps({"cwd": str(main), "thread_id": LEAD, "session_id": ROOT}).encode()
        assert not mod._watcher_host_live(mismatch)
        live.assert_not_called()
        _write_exact_session_binding(sibling, LEAD)
        assert not mod._watcher_host_live(payload)
        live.assert_not_called()


def test_real_codex_handler_refuses_worktree_to_main_focus(tmp_path, monkeypatch):
    from test_worktree_workspace import _setup, _call, _payload
    from test_codex_hook_wrappers import _load as load_hook
    hook = load_hook("codex_hook_registration")
    import codex_lifecycle_watcher as watcher
    main, lead = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    native_tree(tmp_path / "codex", main)

    def result_payload(args, result):
        return json.dumps({"cwd": str(main), "thread_id": LEAD,
            "tool_name": "mcp__harness__task_start", "tool_input": args,
            "tool_response": {"structuredContent": _payload(result)}}).encode()

    args = {"task_id": "TASK__lead", "workspace": str(lead)}
    result = _call(main, "task_start", args)
    assert not result.get("isError"), result
    assert hook.register_task_result(result_payload(args, result), budget_seconds=3)
    assert watcher.registrations(str(lead))
    main_args = {"task_id": "TASK__main"}
    result = _call(main, "task_start", main_args)
    assert result.get("isError"), result
    assert "another worktree" in _payload(result)["error"]
    assert not hook.resolve_session_task_binding(str(main), LEAD)
    assert not (main / "doc/harness/tasks/TASK__main").exists()
    context = _call(main, "task_context", main_args)
    assert context.get("isError"), context
    assert not hook.resolve_session_task_binding(str(main), LEAD)
    assert hook.resolve_session_task_binding(str(lead), LEAD)
    assert watcher.registrations(str(lead))


def test_all_preexisting_workspace_bindings_are_fenced_and_revoked(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_codex_lifecycle_watcher import _write_exact_session_binding
    hook = load_hook("codex_hook_registration")
    import codex_lifecycle_watcher as watcher
    main, lead, sibling = worktrees(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    native_tree(tmp_path / "codex", main)
    for root in (main, lead, sibling):
        task_id, run_id = _write_exact_session_binding(root, LEAD)
        assert watcher.ensure(str(root), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    generation = watcher._registration_generation(watcher.registrations(str(lead))[0])
    try:
        watcher._require_task_binding(str(lead), LEAD, generation, str(main))
    except watcher._BindingUnavailable:
        pass
    else:
        raise AssertionError("conflicting workspace binding retained receipt authority")
    payload = json.dumps({"cwd": str(main), "thread_id": LEAD,
        "tool_name": "mcp__harness__task_start", "tool_input": {"workspace": str(lead)},
        "tool_response": {"structuredContent": {"task_dir": str(lead / "doc/harness/tasks" / task_id),
            "task_id": task_id, "run_id": run_id, "workspace": str(lead)}}}).encode()
    assert not hook.register_task_result(payload)
    for root in (main, lead, sibling):
        assert not hook.resolve_session_task_binding(str(root), LEAD)
        assert not watcher.registrations(str(root))
        assert len(hook.read_active_session_marker(str(root), LEAD)["conflicts"]) == 3
    assert not hook.restore_watcher_registration(json.dumps({"cwd": str(main), "thread_id": LEAD}).encode())


def test_parent_append_during_start_retries_original_spawn(tmp_path, monkeypatch):
    _assert_parent_append_retry(tmp_path, monkeypatch, completion=False)


def test_parent_append_during_completion_retains_original_delivery(tmp_path, monkeypatch):
    _assert_parent_append_retry(tmp_path, monkeypatch, completion=True)


def _assert_parent_append_retry(tmp_path, monkeypatch, *, completion):
    from test_codex_lifecycle_watcher import _load, _spawn_events, _delivery, _snapshot, _active_binding
    mod = _load()
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    root, _, _ = native_tree(home, tmp_path)
    task = tmp_path / "doc/harness/tasks/TASK__race"
    task.mkdir(parents=True)
    watcher = mod.Watcher(str(tmp_path), LEAD)
    receipts = []
    events = _spawn_events(LEAD, LENS, "qa_cli", "/root/lead/qa_cli", "call_race_lens")
    delivery = _delivery("/root/lead/qa_cli", "VERDICT: PASS\nPassed.")
    delivery["payload"]["recipient"] = "/root/lead"
    appended = False
    real_activity = mod._spawn_activity

    def append_once(event):
        nonlocal appended
        result = real_activity(event)
        if result and result[1] == LEAD and not appended:
            appended = True
            with root.open("a") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
        return result

    with mock.patch.object(mod, "_active_task_binding_for_session", return_value=_active_binding(task)), mock.patch.object(mod, "record_subagent_receipt", side_effect=lambda _, receipt: receipts.append(receipt) or receipt), mock.patch.object(mod, "receipt_snapshot", side_effect=lambda _: _snapshot(receipts)):
        if completion:
            for event in events:
                watcher.feed(event)
            assert len(receipts) == 1
            # Force a cold proof, then append again during that scan.
            with root.open("a") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
        with mock.patch.object(mod, "_spawn_activity", side_effect=append_once):
            if completion:
                watcher.feed(delivery)
            else:
                for event in events:
                    watcher.feed(event)
                # Delivery may arrive while its start awaits a stable ancestry proof.
                watcher.feed(delivery)
        item = watcher.calls["call_race_lens"]
        assert appended
        assert not item.get("invalid")
        assert not item.get("completed")
        assert len(receipts) == (1 if completion else 0)
        assert item["root_final"] == "VERDICT: PASS\nPassed."
        watcher.retry()
    assert [receipt["event"] for receipt in receipts] == ["started", "completed"]
    assert receipts[-1]["verdict"] == "PASS"


def _conflict_task(root, name):
    from test_codex_lifecycle_watcher import _write_task_control, RUN_ID
    task = root / "doc/harness/tasks" / name
    task.mkdir(parents=True, exist_ok=True)
    _write_task_control(task)
    return {"task_dir": str(task), "task_id": name, "run_id": RUN_ID}


def _task_hook_payload(root, task):
    return json.dumps({"cwd": str(root), "thread_id": LEAD,
        "tool_name": "mcp__harness__task_start",
        "tool_response": {"structuredContent": task}}).encode()


def test_three_successful_results_remain_fenced_after_only_one_is_parked(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_worktree_workspace import _call
    hook = load_hook("codex_hook_registration")
    main, lead, _ = worktrees(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    tasks = [_conflict_task(main, "TASK__" + name) for name in ("a", "b", "c")]
    with mock.patch.object(hook, "_ensure_with_deadline", return_value=True):
        assert hook.register_task_result(_task_hook_payload(main, tasks[0]))
        assert not hook.register_task_result(_task_hook_payload(main, tasks[1]))
        assert not hook.register_task_result(_task_hook_payload(main, tasks[2]))
        assert len(hook.read_active_session_marker(str(main), LEAD)["conflicts"]) == 3
        (main / "doc/harness/tasks/TASK__b/BLOCKED.md").write_text("blocked\n")
        assert not hook.register_task_result(_task_hook_payload(main, tasks[0]))
        assert not hook.resolve_session_task_binding(str(main), LEAD)
        before = hook.read_active_session_marker(str(main), LEAD)
        foreign_marker = lead / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
        foreign_marker.parent.mkdir(parents=True, exist_ok=True)
        foreign_marker.write_text(json.dumps(before))
        result = _call(main, "task_start", {"task_id": "TASK__a"})
        assert result.get("isError"), result
        assert hook.read_active_session_marker(str(main), LEAD) == before
        (main / "doc/harness/tasks/TASK__c/BLOCKED.md").write_text("blocked\n")
        # A sole survivor can produce a result, but eager focus preserves fences.
        result = _call(main, "task_context", {"task_id": "TASK__a"})
        assert not result.get("isError"), result
        assert not hook.resolve_session_task_binding(str(main), LEAD)
        assert "conflicts" in hook.read_active_session_marker(str(main), LEAD)
        assert hook.register_task_result(_task_hook_payload(main, tasks[0]))
        assert hook.resolve_session_task_binding(str(main), LEAD)["task_dir"] == tasks[0]["task_dir"]


def test_conflict_union_overflow_stays_fenced_for_hook_and_eager_main(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_worktree_workspace import _call
    hook = load_hook("codex_hook_registration")
    main, _, _ = worktrees(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    conflicts = [_conflict_task(main, f"TASK__overflow_{index}")
                 for index in range(hook.MAX_BINDING_CONFLICTS)]
    candidate = _conflict_task(main, "TASK__overflow_next")
    marker = main / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"session_id": LEAD, "conflicts": conflicts}))
    assert not hook.register_task_result(_task_hook_payload(main, candidate))
    overflow = hook.read_active_session_marker(str(main), LEAD)
    assert overflow["conflicts_overflow"] is True and overflow["conflicts"] == []
    assert marker.stat().st_size < 1024
    assert not hook.register_task_result(_task_hook_payload(main, candidate))
    overflow = hook.read_active_session_marker(str(main), LEAD)
    assert not hook.restore_watcher_registration(json.dumps({"cwd": str(main), "thread_id": LEAD}).encode())
    for tool in ("task_start", "task_context"):
        result = _call(main, tool, {"task_id": candidate["task_id"]})
        assert result.get("isError"), result
        assert hook.read_active_session_marker(str(main), LEAD) == overflow
    assert not hook.resolve_session_task_binding(str(main), LEAD)


def test_oversized_existing_conflict_fences_cannot_be_read_as_empty(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    hook = load_hook("codex_hook_registration")
    main, _, _ = worktrees(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    candidate = _conflict_task(main, "TASK__candidate")
    oversized = ([candidate] * (hook.MAX_BINDING_CONFLICTS + 1),
                 [dict(candidate, retained="x" * hook.MAX_CONFLICT_BYTES), candidate])
    marker = main / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
    marker.parent.mkdir()
    for raw in oversized:
        marker.write_text(json.dumps({"session_id": LEAD, "conflicts": raw}))
        assert not hook.register_task_result(_task_hook_payload(main, candidate))
        assert hook.read_active_session_marker(str(main), LEAD)["conflicts_overflow"] is True
        assert not hook.resolve_session_task_binding(str(main), LEAD)


def test_overflow_elsewhere_blocks_old_worktree_registration_recovery(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_codex_lifecycle_watcher import _write_exact_session_binding
    hook = load_hook("codex_hook_registration")
    import codex_lifecycle_watcher as watcher
    main, lead, _ = worktrees(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    native_tree(tmp_path / "codex", main)
    task_id, run_id = _write_exact_session_binding(lead, LEAD)
    assert watcher.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    generation = watcher._registration_generation(watcher.registrations(str(lead))[0])
    marker = main / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"session_id": LEAD, "conflicts": [], "conflicts_overflow": True}))
    for cwd in (main, lead):
        with mock.patch.object(hook, "_ensure_with_deadline", return_value=True) as ensure:
            assert not hook.restore_watcher_registration(json.dumps({"cwd": str(cwd), "thread_id": LEAD}).encode())
            ensure.assert_not_called()
    try:
        watcher._require_task_binding(str(lead), LEAD, generation, str(main))
    except watcher._BindingUnavailable:
        pass
    else:
        raise AssertionError("overflow fence elsewhere retained receipt authority")


def test_partial_fence_recovery_discards_ambiguous_history_and_old_generation(tmp_path, monkeypatch):
    from test_codex_hook_wrappers import _load as load_hook
    from test_worktree_workspace import _call
    from test_codex_lifecycle_watcher import (
        _write_exact_session_binding, _spawn_events, _delivery, _child_events,
        _write_jsonl, _rollout_path, _snapshot,
    )
    hook = load_hook("codex_hook_registration")
    import codex_lifecycle_watcher as watcher
    main, lead, _ = worktrees(tmp_path)
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_SESSION_ID", LEAD)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setenv("CODEX_HOME", str(home))
    _, lead_rollout, _ = native_tree(home, main)
    task_id, run_id = _write_exact_session_binding(lead, LEAD)
    assert watcher.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    old = watcher.registrations(str(lead))[0]
    old_generation = watcher._registration_generation(old)
    current = {"task_dir": str(lead / "doc/harness/tasks" / task_id), "task_id": task_id,
               "run_id": run_id, "workspace": str(lead)}
    other = _conflict_task(main, "TASK__other")
    with mock.patch.object(hook, "invalidate_registration", side_effect=RuntimeError("interrupted")):
        assert not hook.register_task_result(_task_hook_payload(main, other))
    assert "conflicts" in hook.read_active_session_marker(str(main), LEAD)
    assert hook.resolve_session_task_binding(str(lead), LEAD)
    assert watcher.registrations(str(lead))[0]["offset"] == old["offset"]

    def append(events):
        with lead_rollout.open("a") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    final = "VERDICT: PASS\nPassed."
    delivery = _delivery("/root/lead/qa_cli", final)
    delivery["payload"]["recipient"] = "/root/lead"
    append(_spawn_events(LEAD, LENS, "qa_cli", "/root/lead/qa_cli", "call_ambiguous_lens") + [delivery])
    parked = _call(main, "task_blocked", {"task_id": "TASK__other",
        "blocked_reason": "park conflicting task", "unblock_condition": "resume separately"})
    assert not parked.get("isError"), parked
    assert "conflicts" in hook.read_active_session_marker(str(main), LEAD)
    assert not hook.restore_watcher_registration(json.dumps({"cwd": str(main), "thread_id": LEAD}).encode())
    assert not watcher.ensure(str(lead), LEAD, session_cwd=str(main), task_id=task_id, run_id=run_id)
    try:
        watcher._require_task_binding(str(lead), LEAD, old_generation, str(main))
    except watcher._BindingUnavailable:
        pass
    else:
        raise AssertionError("parked conflict revived a surviving registration")
    recovery = json.loads(_task_hook_payload(main, current))
    recovery["tool_input"] = {"workspace": str(lead)}
    recovery = json.dumps(recovery).encode()
    real_invalidate = hook.invalidate_registration
    with mock.patch.object(hook, "invalidate_registration", side_effect=lambda root, thread: False if root == str(lead) else real_invalidate(root, thread)), mock.patch.object(hook, "clear_binding_conflict_fence", wraps=hook.clear_binding_conflict_fence) as clear:
        assert not hook.register_task_result(recovery)
        clear.assert_not_called()
    assert "conflicts" in hook.read_active_session_marker(str(main), LEAD)
    assert not hook.restore_watcher_registration(json.dumps({"cwd": str(lead), "thread_id": LEAD}).encode())
    assert hook.register_task_result(recovery, budget_seconds=3)
    new = watcher.registrations(str(lead))[0]
    assert new["offset"] == lead_rollout.stat().st_size > old["offset"]
    assert "conflicts" not in hook.read_active_session_marker(str(main), LEAD)
    try:
        watcher._require_task_binding(str(lead), LEAD, old_generation, str(main))
    except watcher._RegistrationChanged:
        pass
    else:
        raise AssertionError("recovery preserved an ambiguous old offset")

    receipts = []
    def observe(registration):
        return watcher.watch(str(lead), LEAD, str(lead_rollout), registration["offset"],
            session_cwd=str(main), task_id=task_id, run_id=run_id, idle_seconds=0.5,
            expected_generation=watcher._registration_generation(registration))
    with mock.patch.object(watcher, "record_subagent_receipt", side_effect=lambda _, receipt: receipts.append(receipt) or receipt), mock.patch.object(watcher, "receipt_snapshot", side_effect=lambda _: _snapshot(receipts)):
        assert observe(old) == 0
        assert observe(new) == 0
        assert receipts == []
        fresh_id = "019f82a6-ce64-75a3-b01d-92f7b0b4fe71"
        fresh_path = "/root/lead/qa_cli_fresh"
        child = _child_events(LEAD, fresh_id, fresh_path, str(main), final)
        child[0]["payload"]["session_id"] = ROOT
        child[0]["payload"]["source"]["subagent"]["thread_spawn"]["depth"] = 2
        child[4]["payload"]["author"] = "/root/lead"
        _write_jsonl(_rollout_path(home, fresh_id), child)
        delivery = _delivery(fresh_path, final)
        delivery["payload"]["recipient"] = "/root/lead"
        append(_spawn_events(LEAD, fresh_id, "qa_cli_fresh", fresh_path, "call_fresh_lens") + [delivery])
        assert observe(new) == 0
    assert [receipt["event"] for receipt in receipts] == ["started", "completed"]
    assert receipts[-1]["verdict"] == "PASS"
    assert all(receipt["agent_id"] == fresh_path for receipt in receipts)


def test_real_close_preserves_normal_and_overflow_fences(tmp_path, monkeypatch, fence_kind):
    from test_worktree_workspace import _setup, _call, _payload
    from test_harness_mcp_server import _record_receipt_fixture
    from test_codex_hook_wrappers import _load as load_hook
    hook = load_hook("codex_hook_registration")
    main, _ = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setenv("CODEX_THREAD_ID", LEAD)
    monkeypatch.setenv("CODEX_SESSION_ID", LEAD)
    started = _call(main, "task_start", {"task_id": "TASK__close_fence"})
    assert not started.get("isError"), started
    task = main / "doc/harness/tasks/TASK__close_fence"
    assert not _call(main, "write_plan", {"task_id": task.name, "plan": "# Plan\n\nSmall.\n", "required_lenses": ["review-code", "qa-cli"]}).get("isError")
    for agent, lens, final in (("review-fence", "harness:code-reviewer", "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"), ("qa-fence", "harness:qa-cli", "VERDICT: PASS")):
        for event in ("started", "completed"):
            _record_receipt_fixture(str(task), {"agent_id": agent, "agent_type": lens,
                "event": event, "verdict": "PASS" if event == "completed" else "",
                "summary": final if event == "completed" else "",
                "source": "claude_hook", "runtime_id": f"claude:{LEAD}:{agent}"})
    current = {"task_dir": str(task), "task_id": task.name,
               "run_id": json.loads((task / "TASK.json").read_text())["run_id"]}
    other = _conflict_task(main, "TASK__close_other")
    fence = {"session_id": LEAD, "conflicts": [current, other]}
    if fence_kind == "overflow":
        fence.update(conflicts=[], conflicts_overflow=True)
    marker = main / "doc/harness/tasks/.active_sessions" / f"{LEAD}.json"
    marker.write_text(json.dumps(fence))
    result = _call(main, "task_close", {"task_id": task.name})
    assert not result.get("isError"), result
    assert _payload(result)["closed"] is True
    assert json.loads(marker.read_text()) == fence
    assert not hook.restore_watcher_registration(json.dumps({"cwd": str(main), "thread_id": LEAD}).encode())


def test_marker_writers_lock_session_before_receipts_in_deferred_routes(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from test_worktree_workspace import _setup, _call, harness_server
    main, lead = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    real_session = harness_server.active_session_transaction
    real_receipt = harness_server.receipt_stream_transaction
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
    def receipt(task):
        assert any(str(task).startswith(root + "/") for root in held), (task, held)
        observed.append(str(task))
        with real_receipt(task):
            yield

    with mock.patch.object(harness_server, "active_session_transaction", side_effect=session), mock.patch.object(harness_server, "receipt_stream_transaction", side_effect=receipt):
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
