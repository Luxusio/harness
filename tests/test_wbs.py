"""WBS measures assertions over outcomes, never planning activity."""
import json
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))
import wbs


def node(key="A", weight=10, **extra):
    return dict(id=key, title="Result " + key, weight=weight,
                check=[sys.executable, "-c", "from pathlib import Path; assert Path('src.txt').read_text() == 'ok'"], **extra)


def plan(nodes):
    return "# Plan\n```wbs\n" + json.dumps(nodes) + "\n```\n"


def baseline(tmp_path, nodes=None, now=100):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("doc/harness/tasks/\n")
    (repo / "src.txt").write_text("ok")
    td = repo / "doc/harness/tasks/TASK__sample"
    td.mkdir(parents=True)
    publish(td, nodes or [node()], now=now)
    return repo, td


def publish(td, nodes, now=100, run="run"):
    text = plan(nodes)
    state = wbs.prepare(str(td), text, run, now=now)
    (td / "PLAN.md").write_text(text)
    (td / "WBS.json").write_text(json.dumps(state))
    return state


def progress(repo, td, now=100):
    return wbs.task_progress(str(td), str(repo), "run", now=now)


def check(repo, td, key="A", now=101, **kw):
    return wbs.verify(str(td), str(repo), "run", key, now=now, **kw)


def test_weighted_progress_requires_actual_checks_and_stales_with_source(tmp_path):
    repo, td = baseline(tmp_path, [node("A", 3), node("B", 7)])
    assert progress(repo, td)["percentage"] == 0
    assert check(repo, td)["passed"]
    assert progress(repo, td)["percentage"] == 30
    assert check(repo, td, "B")["progress"]["percentage"] == 100
    (repo / "src.txt").write_text("changed")
    assert progress(repo, td)["percentage"] == 0
    assert not check(repo, td)["passed"]
    assert progress(repo, td)["percentage"] == 0


def test_exact_two_hour_clock_ignores_noop_rechecks_and_splits(tmp_path):
    repo, td = baseline(tmp_path, [node("A", 4), node("B", 6)])
    check(repo, td, now=200)
    assert not progress(repo, td, 7399)["refinement_due"]
    check(repo, td, now=7300)
    assert progress(repo, td, 7400)["refinement_due"]
    state = publish(td, [node("A", 4), node("B", 6)], now=7350)
    assert state["last_progress_at"] == 200
    group = dict(id="B", title="Result B", weight=6)
    split = [node("A", 4), group, node("B1", 2, parent="B"), node("B2", 4, parent="B")]
    state = publish(td, split, now=7401)
    assert state["last_progress_at"] == 200
    assert progress(repo, td, 7401)["percentage"] == 40
    assert progress(repo, td, 7401)["refinement_due"]


def test_rewrites_cannot_manufacture_credit(tmp_path, mutation):
    import pytest
    repo, td = baseline(tmp_path)
    check(repo, td)
    candidate = [node()]
    if mutation == "delete":
        candidate = [node("B")]
    elif mutation == "weight":
        candidate[0]["weight"] = 99
    else:
        candidate[0].pop("check")
        candidate.append(node("B", parent="A"))
    before = (td / "WBS.json").read_bytes()
    with pytest.raises(ValueError):
        wbs.prepare(str(td), plan(candidate), "run")
    assert before == (td / "WBS.json").read_bytes()


def test_invalid_definitions_refuse(case):
    import pytest
    nodes = [node()]
    if case == "bool": nodes[0]["weight"] = True
    if case == "zero": nodes[0]["weight"] = 0
    if case == "duplicate": nodes += [node()]
    if case == "cycle": nodes += [node("B", depends_on=["A"])]; nodes[0]["depends_on"] = ["B"]
    if case == "parent_cycle": nodes = [node("A", parent="B"), node("B", parent="A")]
    if case == "file_instructions": nodes[0]["scopes"] = ["src.txt"]
    if case == "unknown": nodes[0]["depends_on"] = ["B"]
    if case == "split_weight": nodes = [dict(id="A", title="A", weight=10), node("B", 9, parent="A")]
    with pytest.raises(ValueError):
        wbs.validate(nodes)


def test_legacy_corrupt_and_foreign_run_do_not_claim_progress(tmp_path):
    repo, td = baseline(tmp_path)
    check(repo, td)
    assert wbs.task_progress(str(td), str(repo), "other")["percentage"] == 0
    (td / "WBS.json").write_text("broken")
    assert progress(repo, td)["percentage"] is None
    (td / "WBS.json").unlink()
    assert progress(repo, td)["status"] == "unbaselined"


def test_dependency_must_be_verified_and_work_pack_preserves_graph(tmp_path):
    import pytest
    repo, td = baseline(tmp_path, [node("A"), node("B", depends_on=["A"])])
    with pytest.raises(ValueError, match="dependencies"):
        check(repo, td, "B")
    pack = wbs.work_pack(str(td), str(repo), "run", ownership={"A": ["a.txt"], "B": ["b.txt"]})
    first, second = pack["requests"]
    assert second["depends_on"] == [first["slug"]]
    assert "park" in pack["next_action"]
    check(repo, td)
    assert check(repo, td, "B")["passed"]


def test_assertion_failure_timeout_and_input_mutation_earn_no_credit(tmp_path):
    for idx, script in enumerate(["raise SystemExit(1)", "import time; time.sleep(3)",
                                  "from pathlib import Path; Path('src.txt').write_text('new')"]):
        root = tmp_path / str(idx)
        root.mkdir()
        item = node()
        item["check"] = [sys.executable, "-c", script]
        repo, td = baseline(root, [item])
        assert not check(repo, td, timeout=1)["passed"]
        assert progress(repo, td)["percentage"] == 0


def test_stale_verified_leaf_can_be_refined_without_freezing_implementation(tmp_path):
    repo, td = baseline(tmp_path)
    check(repo, td)
    (repo / "src.txt").write_text("new")
    assert progress(repo, td)["percentage"] == 0
    state = publish(td, [dict(id="A", title="New detail", weight=10), node("A1", 4, parent="A"), node("A2", 6, parent="A")])
    assert state["last_progress_at"] == 101
    assert progress(repo, td)["percentage"] == 0


def test_discovered_outcomes_expand_scope_without_earning_progress(tmp_path):
    repo, td = baseline(tmp_path)
    check(repo, td)
    state = publish(td, [node(), node("B")], now=500)
    assert state["last_progress_at"] == 101
    assert progress(repo, td)["percentage"] == 50
    assert progress(repo, td)["scope_revision"] == 2


def test_invalid_measurement_never_releases_ready_outcomes(tmp_path):
    repo, td = baseline(tmp_path)
    state = wbs.read_state(str(td))
    assert wbs.ready_outcomes(state, dict(status="invalid", percentage=None)) == []


def test_oversized_state_refuses_before_publishing_or_losing_repairability(tmp_path):
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    repo, td = baseline(tmp_path)
    (td / "WBS.json").unlink()
    def call(action, **extra):
        return server._call_in_repo(str(repo), "wbs", dict(action=action, wbs_id="sample", **extra))
    assert not call("open").get("isError")
    assert not call("plan", plan=plan([node()])).get("isError")
    before = {name: (td / name).read_bytes() for name in ("WBS.json", "PLAN.md", "TASK.json")}
    large = node()
    large["check"] = [sys.executable, "-c", "#" + "x" * 1048300]
    assert len(plan([large]).encode()) < 1024 * 1024  # PLAN fits while combined WBS state does not.
    rejected = call("plan", plan=plan([large]))
    assert rejected.get("isError"), rejected
    assert "storage budget" in rejected["structuredContent"]["error"]
    assert before == {name: (td / name).read_bytes() for name in before}
    assert call("context")["structuredContent"]["progress"]["status"] == "measured"
    assert not call("plan", plan=plan([node()])).get("isError")


def test_dispatch_bounds_combined_evidence_not_only_the_pack(tmp_path, monkeypatch):
    import pytest
    repo, td = baseline(tmp_path, [node("A"), node("B")])
    import work_pack
    import batch_state
    monkeypatch.setattr(batch_state, "main_boundary", lambda repo: None)
    monkeypatch.setattr(work_pack, "make_spec", lambda *args: dict(batch_id="test", requests=[], padding="x" * 1050000))
    before = (td / "WBS.json").read_bytes()
    with pytest.raises(ValueError, match="storage budget"):
        wbs.work_pack(str(td), str(repo), "run", bind=True, ownership={"A": ["a.txt"], "B": ["b.txt"]})
    assert (td / "WBS.json").read_bytes() == before


def test_successful_check_also_stops_redirected_children(tmp_path):
    import pytest
    import os
    import time
    repo, td = baseline(tmp_path)
    script = "import subprocess; from pathlib import Path; p=subprocess.Popen(['sleep','30'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True)"
    item = node()
    item["check"] = [sys.executable, "-c", script]
    publish(td, [item])
    result = check(repo, td)
    assert result["passed"]
    pid = int(result["output"].strip())
    # Reaped by the host init or left only as a zombie, never a live writer.
    for _ in range(20):
        path = Path("/proc", str(pid), "stat")
        if not path.exists() or path.read_text().split()[2] == "Z":
            break
        time.sleep(0.01)
    else:
        os.kill(pid, 9)
        pytest.fail("assertion child survived successful verification")


def test_scope_symlinks_fail_closed(tmp_path):
    repo, td = baseline(tmp_path)
    check(repo, td)
    (repo / "src.txt").unlink()
    outside = tmp_path / "secret"
    outside.write_text("ok")
    (repo / "src.txt").symlink_to(outside)
    assert progress(repo, td)["percentage"] is None


def test_rolling_plan_needs_no_file_layout_or_initial_assertion(tmp_path):
    import pytest
    rough = dict(id="A", title="Reader can recover a failed import", weight=10)
    repo, td = baseline(tmp_path, [rough])
    assert progress(repo, td)["percentage"] == 0
    with pytest.raises(ValueError, match="assertion"):
        check(repo, td)
    refined = dict(rough, title="Reader can retry after a transient import failure", check=node()["check"])
    state = publish(td, [refined], now=900)
    assert state["last_progress_at"] == 100
    assert check(repo, td)["passed"]
    changed = dict(refined, check=[sys.executable, "-c", "raise SystemExit(1)"])
    publish(td, [changed])
    assert progress(repo, td)["percentage"] == 0


def test_mcp_plan_bundle_and_explicit_check(tmp_path):
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    repo, td = baseline(tmp_path)
    # Start/plan through actual tool owner rather than forging control evidence.
    (td / "WBS.json").unlink()
    result = server._call_in_repo(str(repo), "task_start", {"task_id": "sample"})
    assert not result.get("isError"), result
    result = server._call_in_repo(str(repo), "write_plan", {"task_id": "sample", "plan": plan([node()])})
    assert not result.get("isError"), result
    result = server._call_in_repo(str(repo), "task_wbs", {"task_id": "sample", "action": "verify", "leaf_id": "A"})
    assert not result.get("isError"), result
    assert result["structuredContent"]["progress"]["percentage"] == 100
    assert set(json.loads((td / "TASK.json").read_text())) == {"run_id", "execution_mode", "required_lenses", "close_receipt_fingerprint"}


def test_one_public_tree_preserves_existing_execution_run_and_gates(tmp_path):
    from test_harness_mcp_server import HarnessMcpServerTests, harness_server
    server = HarnessMcpServerTests()
    repo, td = baseline(tmp_path)
    (td / "WBS.json").unlink()
    def call(action, **extra):
        return server._call_in_repo(str(repo), "wbs", dict(action=action, wbs_id="WBS__sample", **extra))
    assert [tool["name"] for tool in harness_server.list_tools()] == ["wbs"]
    opened = call("open")
    assert not opened.get("isError"), opened
    run = opened["structuredContent"]["run_id"]
    assert call("open")["structuredContent"]["run_id"] == run
    assert not call("plan", plan=plan([node()])).get("isError")
    assert call("next")["structuredContent"]["ready"] == ["A"]
    assert call("check", node_id="A")["structuredContent"]["passed"]
    context = call("context")["structuredContent"]
    assert context["wbs_id"] == "WBS__sample" and context["progress"]["percentage"] == 100
    assert context["tree"][0]["id"] == "A"
    assert call("next")["structuredContent"]["ready"] == []
    assert not call("verify").get("isError")
    assert call("close").get("isError")  # check PASS never substitutes for independent review/QA.
    assert call("context", plan="unexpected").get("isError")
    assert not call("block", blocked_reason="Waiting for external fixture service", unblock_condition="Restore fixture service").get("isError")
    assert call("context")["structuredContent"]["task_context"]["status"] == "blocked"
    assert call("open")["structuredContent"]["run_id"] == run


def test_only_wbs_open_and_context_bind_hook_results(name, encoded):
    from codex_hook_registration import _task_result
    body = dict(task_dir="/tmp/fixture/TASK__x", task_id="TASK__x", run_id="run")
    for action in ("open", "context", "plan", "check", "dispatch", "verify", "close", "block", "next"):
        args = dict(action=action, wbs_id="x")
        payload = dict(tool_name=name, tool_input=json.dumps(args) if encoded else args,
                       tool_response=dict(structuredContent=body))
        observed = _task_result(json.dumps(payload).encode())
        assert bool(observed[0]) == (action in {"open", "context"})
        payload["tool_response"]["isError"] = True
        assert _task_result(json.dumps(payload).encode()) == ("", "", "")


def test_public_dispatch_performs_parent_handoff_without_another_lifecycle(tmp_path, monkeypatch):
    from test_batch_state import Pool
    from test_harness_mcp_server import HarnessMcpServerTests
    from _lib import native_wbs_is_synced
    pool, server = Pool(tmp_path, monkeypatch), HarnessMcpServerTests()
    def call(action, **extra):
        return server._call_in_repo(str(pool.repo), "wbs", dict(action=action, **extra))
    opened = call("open", objective="Deliver one unified outcome tree")
    assert not opened.get("isError"), opened
    root = opened["structuredContent"]["wbs_id"]
    assert native_wbs_is_synced(str(pool.repo), "Deliver one unified outcome tree")
    nodes = [dict(id="A", title="First observable result", weight=1),
             dict(id="B", title="Second observable result", weight=1)]
    assert not call("plan", wbs_id=root, plan=plan(nodes)).get("isError")
    result = call("dispatch", wbs_id=root, ownership={"A": ["a.txt"], "B": ["b.txt"]})
    assert not result.get("isError"), result
    assert result["structuredContent"]["execution"] == "awaiting_worktrees"
    assert call("context", wbs_id=root)["structuredContent"]["task_context"]["status"] == "blocked"
    assert call("open", wbs_id=root).get("isError")
    assert call("next", wbs_id=root)["structuredContent"]["ready"] == []
    assert not (pool.repo / "doc/harness/goals/current.json").exists()


def test_standalone_parent_waits_for_real_isolated_pool_integration(tmp_path, monkeypatch):
    from test_batch_state import Pool, task, _git
    from test_harness_mcp_server import HarnessMcpServerTests
    pool, server = Pool(tmp_path, monkeypatch), HarnessMcpServerTests()
    def call(name, **args):
        return server._call_in_repo(str(pool.repo), name, args)
    assert not call("task_start", task_id="parent").get("isError")
    nodes = [dict(id=key, title="Outcome " + key, weight=1,
                  check=[sys.executable, "-c", "from pathlib import Path; assert Path('" + key.lower() + ".txt').read_text() == 'done'"])
             for key in ("A", "B")]
    assert not call("write_plan", task_id="parent", plan=plan(nodes)).get("isError")
    result = call("task_wbs", task_id="parent", action="dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]})
    assert not result.get("isError"), result
    pack = result["structuredContent"]
    pool.batch = pack["batch_id"]
    context = call("task_context", task_id="parent")["structuredContent"]["task_context"]
    assert not context["source_write_allowed"]
    assert "integrated WBS work pack" in context["missing_for_close"]
    assert call("task_start", task_id="parent").get("isError")
    assert not call("task_blocked", task_id="parent", blocked_reason="Waiting for isolated WBS leads to complete their worktree handoff.",
                    unblock_condition="Integrate and close the exact bound batch, then resume parent for combined verification.").get("isError")
    pool.init(pack["requests"], owner="TASK__parent")
    pool.claim()
    for index, request in enumerate(pack["requests"]):
        slug, worker = request["slug"], "worker-" + str(index)
        worktree, branch = pool.bind(slug, worker)
        assert worktree != pool.repo
        (worktree / request["scopes"][0]).write_text("done")
        _git("add", "-A", cwd=worktree)
        _git("commit", "-qm", slug, "--trailer", "Harness-Task: TASK__" + slug, cwd=worktree)
        task(worktree, slug, closed=True)
        from test_batch_state import RUN
        pool.cli(*pool.result(slug, worktree, branch, "closed", worker, run_id=RUN))
    for request in pack["requests"]:
        pool.cli("finish", "--slug", request["slug"])
        assert call("task_start", task_id="parent").get("isError")
    pool.cli("close")
    assert not call("task_start", task_id="parent").get("isError")
    for key in ("A", "B"):
        result = call("task_wbs", task_id="parent", action="verify", leaf_id=key)
        assert not result.get("isError"), result
        assert result["structuredContent"]["passed"]
    assert result["structuredContent"]["progress"]["percentage"] == 100

    # Integrated work must not freeze future rolling refinement or assertions.
    nodes[0]["title"] = "Refined outcome A after integrated feedback"
    result = call("write_plan", task_id="parent", plan=plan(nodes))
    assert not result.get("isError"), result



def pytest_generate_tests(metafunc):
    cases = {'test_rewrites_cannot_manufacture_credit': [('mutation', ['delete', 'weight', 'split_done'])],
     'test_invalid_definitions_refuse': [('case',
                                          ['bool',
                                           'zero',
                                           'duplicate',
                                           'cycle',
                                           'parent_cycle',
                                           'file_instructions',
                                           'unknown',
                                           'split_weight'])],
     'test_only_wbs_open_and_context_bind_hook_results': [('name',
                                                           ['wbs',
                                                            'mcp__harness__wbs',
                                                            'mcp__plugin_harness_harness__wbs']),
                                                          ('encoded', [False, True])]}
    for argument, values in cases.get(metafunc.function.__name__, []):
        metafunc.parametrize(argument, values)


def test_unit_outcome_can_refine_after_two_hours_without_earning_credit(tmp_path):
    repo, td = baseline(tmp_path, [node(weight=1)])
    assert progress(repo, td, 7300)["refinement_due"]
    publish(td, [dict(id="A", title="Result A", weight=1),
                 node("A1", 0.1, parent="A"), node("A2", 0.2, parent="A"),
                 node("A3", 0.7, parent="A")], now=7300)
    pending = progress(repo, td, 7300)
    assert pending["percentage"] == 0 and pending["refinement_due"]
    assert pending["scope_revision"] == 1 and pending["total_weight"] == 1
    assert check(repo, td, "A1", now=7301)["progress"]["percentage"] == 10
    assert check(repo, td, "A2", now=7302)["progress"]["percentage"] == 30
    final = check(repo, td, "A3", now=7303)["progress"]
    assert final["percentage"] == 100
    assert final["verified_weight"] == final["total_weight"] == 1


def test_fractional_weights_reject_nonfinite_or_nonconserving_values():
    import pytest
    for weight in (float("nan"), float("inf"), -0.1, 0, True):
        with pytest.raises(ValueError, match="positive finite"):
            wbs.validate([node(weight=weight)])
    with pytest.raises(ValueError, match="conserve"):
        wbs.validate([dict(id="A", title="A", weight=1),
                      node("B", 0.5, parent="A"), node("C", 0.5000000001, parent="A")])


def test_tiny_pending_weight_never_rounds_to_completion_or_hides_stall(tmp_path):
    repo, td = baseline(tmp_path, [node("A", 1), node("B", 1e-30)])
    partial = check(repo, td, "A", now=200)["progress"]
    assert partial["percentage"] < 100
    assert progress(repo, td, 7400)["refinement_due"]
    final = check(repo, td, "B", now=7401)["progress"]
    assert final["percentage"] == 100 and final["last_progress_at"] == 7401


def test_ambient_git_index_cannot_hide_changes_to_ignored_tracked_source(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    (repo / ".gitignore").write_text("doc/harness/tasks/\nsrc.txt\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-f", "src.txt"], cwd=repo, check=True)
    real_source = wbs.fingerprint(str(repo))
    alternate = tmp_path / "alternate-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate))
    subprocess.run(["git", "read-tree", "--empty"], cwd=repo, check=True)
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    assert wbs.fingerprint(str(repo)) == real_source
    assert check(repo, td)["progress"]["percentage"] == 100
    (repo / "src.txt").write_text("broken")
    assert wbs.fingerprint(str(repo)) != real_source
    assert progress(repo, td)["percentage"] == 0
    assert not check(repo, td)["passed"]


def test_retired_aliases_fail_over_actual_mcp_protocol(tmp_path, monkeypatch):
    from test_harness_mcp_server import harness_server
    replies = []
    server = harness_server.McpServer()
    monkeypatch.setattr(server, '_reply', lambda msg_id, result: replies.append((msg_id, result)))
    names = ['goal_start', 'goal_context', 'goal_add_task', 'goal_next_task', 'goal_finish',
             'task_start', 'task_context', 'task_wbs', 'write_plan', 'task_verify', 'task_close', 'task_blocked']
    before = list(tmp_path.iterdir())
    for index, name in enumerate(names):
        server.handle_request(dict(jsonrpc='2.0', id=index, method='tools/call',
                                   params=dict(name=name, arguments=dict(task_id='TASK__never'))))
    assert len(replies) == len(names)
    assert all(result.get('isError') and 'Unknown tool' in result['structuredContent']['error']
               for _, result in replies)
    assert list(tmp_path.iterdir()) == before
    assert set(harness_server.TOOLS) == {tool['name'] for tool in harness_server.TOOL_DEFS} == {'wbs'}
    server.close()


def test_public_migration_is_explicit_and_owner_authorized(tmp_path, monkeypatch):
    from test_harness_mcp_server import HarnessMcpServerTests
    from test_goal_migration import row
    path = row(tmp_path)
    owner = HarnessMcpServerTests()
    from test_codex_hook_wrappers import _load
    _load("_lib")  # Hook tests share the canonical writer authority, never reload it.
    blocked = owner._call_in_repo(str(tmp_path), 'wbs', dict(action='open', wbs_id='sample'))
    assert blocked.get('isError') and 'migrate' in blocked['structuredContent']['error']
    preview = owner._call_in_repo(str(tmp_path), 'wbs', dict(action='migrate'))
    assert not preview.get('isError'), preview
    assert path.exists() and preview['structuredContent']['status'] == 'preview'
    refused = owner._call_in_repo(str(tmp_path), 'wbs', dict(action='migrate', apply='true'))
    assert refused.get('isError') and path.exists()
    applied = owner._call_in_repo(str(tmp_path), 'wbs', dict(action='migrate', apply=True))
    assert not applied.get('isError'), applied
    assert applied['structuredContent']['status'] == 'complete' and not path.exists()
    assert not list(tmp_path.rglob('TASK.json'))
