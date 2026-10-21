"""WBS packs consume real batch integration and archival evidence."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))
import work_pack

GOAL = "GOAL__parallel"
CHILD = "TASK__integration"


def request(*args, **kwargs):
    from test_batch_state import request as batch_request
    return batch_request(*args, **kwargs)


def pytest_generate_tests(metafunc):
    if "requests" in metafunc.fixturenames:
        metafunc.parametrize("requests", [[], [request("a")],
            [dict(request("a"), ignored_intent="change something else"), request("b")],
            [request("a", dependencies=["missing"])],
            [request("a", dependencies=["b"]), request("b", dependencies=["a"])],
            [request("a", "../escape"), request("b")],
            [request("a", "*.py"), request("b")], [request("integration"), request("b")]])
    if "change" in metafunc.fixturenames:
        metafunc.parametrize("change", ["ancestry", "branch", "archive_symlink", "run", "checkpoint", "harvest", "residual_path", "residual_ref", "residual_registration"])
    if "proof" in metafunc.fixturenames:
        metafunc.parametrize("proof", ["missing", "tampered"])
    if "used_identity" in metafunc.fixturenames:
        metafunc.parametrize("used_identity", ["task", "archive", "pool"])


def requests():
    return [request("a"), request("b")]


def pack(tmp_path, monkeypatch):
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    spec = work_pack.make_spec(str(pool.repo), CHILD, requests())
    pool.batch = spec["batch_id"]
    return pool, spec


def route(pool, spec):
    return work_pack.route(str(pool.repo), CHILD, spec)


def integrated(pool):
    from test_batch_state import complete
    pool.init(requests(), owner=CHILD)
    pool.claim()
    complete(pool)
    complete(pool, "b", "worker-b")
    pool.cli("finish", "--slug", "a")
    pool.cli("finish", "--slug", "b")
    pool.cli("close")


def test_missing_pack_routes_without_creating_state(tmp_path, monkeypatch):
    pool, spec = pack(tmp_path, monkeypatch)
    result = route(pool, spec)
    assert result["route"] == "batch" and result["batch_state"] == "missing"
    assert not pool.state.exists()
    assert work_pack.make_spec(str(pool.repo), CHILD, requests()) == spec
    assert work_pack.make_spec(str(pool.repo), "TASK__other", requests())["batch_id"] != spec["batch_id"]


def test_invalid_intake_refused(tmp_path, monkeypatch, requests):
    import pytest
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        work_pack.make_spec(str(pool.repo), CHILD, requests)


def test_new_pack_rejects_used_lead_identity(tmp_path, monkeypatch, used_identity):
    import pytest
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    if used_identity == "pool":
        pool.init(requests(), owner=CHILD)
    else:
        parent = "tasks" if used_identity == "task" else "archive/batch"
        directory = pool.repo / "doc/harness" / parent / "TASK__a"
        directory.mkdir(parents=True)
        (directory / "TASK.json").write_text('{"status":"closed"}\n')
    with pytest.raises(ValueError, match="identity already"):
        work_pack.make_spec(str(pool.repo), CHILD, requests())


def test_submodule_intent_preserved_without_derived_fields():
    items = [dict(request("a", "vendor/pkg"), submodules=["vendor/pkg"]), request("b")]
    spec = work_pack._spec(CHILD, items)
    assert spec["requests"] == items
    items[0]["submodules"].append("other")
    assert spec["requests"][0]["submodules"] == ["vendor/pkg"]


def test_pending_and_unrelated_pool_cannot_complete(tmp_path, monkeypatch):
    import pytest
    pool, spec = pack(tmp_path, monkeypatch)
    pool.init(requests(), owner=CHILD)
    assert route(pool, spec)["unfinished"] == {"a": "queued", "b": "queued"}
    with pytest.raises(ValueError, match="unfinished"):
        work_pack.require_integrated(str(pool.repo), CHILD, spec)
    changed = copy.deepcopy(spec)
    changed["requests"][0]["request"] = "Different intent"
    with pytest.raises(ValueError, match="intake differs"):
        route(pool, changed)


def test_integrated_pack_requires_archive_and_destination_proof(tmp_path, monkeypatch):
    import pytest
    pool, spec = pack(tmp_path, monkeypatch)
    integrated(pool)
    assert route(pool, spec)["route"] == "integration"
    work_pack.require_integrated(str(pool.repo), CHILD, spec)
    archive = pool.repo / "doc/harness/archive/batch/TASK__a/PLAN.md"
    archive.write_text("changed archived evidence")
    with pytest.raises(ValueError, match="archive fingerprint"):
        route(pool, spec)


def test_stored_queries_skip_archive_hash_but_authority_still_refuses_tampering(tmp_path, monkeypatch):
    import pytest
    import wbs
    import batch_state
    from test_managed_wbs import host_snapshot
    pool, call, td = mcp_integration(tmp_path, monkeypatch)
    state = wbs.read_state(str(td))
    # The first baseline preserves this real integrated pack in history.
    assert state.get("batch_history")
    actual = batch_state.fingerprint
    def forbidden(*args, **kwargs):
        pytest.fail("stored query freshly hashed archive")
    monkeypatch.setattr(batch_state, "fingerprint", forbidden)
    monkeypatch.setattr(wbs, "fingerprint", forbidden)
    for action in ("context", "next", "open"):
        result = call("wbs", dict(action=action, wbs_id=CHILD))
        assert not result.get("isError"), result
    text = (td / "PLAN.md").read_text()
    assert not call("wbs", dict(action="plan", wbs_id=CHILD, plan=text)).get("isError")
    spec = state["batch_history"][0]["batch"]
    stored = work_pack.route(str(pool.repo), CHILD, spec, measure=False)
    assert stored["route"] == "integration" and stored["proof_fresh"] is False
    # Also cover an active pack, independent of the historical projection.
    active = dict(state, batch=spec, batch_history=[])
    assert wbs.task_progress(str(td), str(pool.repo), state["run_id"], measure=False, _state=active)["integration"]["proof_fresh"] is False
    (td / "WBS.json").write_text(wbs.encode_state(active))
    assert wbs.work_pack(str(td), str(pool.repo), state["run_id"], host=host_snapshot())["route"] == "integration"
    (pool.repo / "doc/harness/archive/batch/TASK__a/PLAN.md").write_text("tampered archived content")
    assert work_pack.route(str(pool.repo), CHILD, spec, measure=False)["route"] == "integration"
    monkeypatch.setattr(batch_state, "fingerprint", actual)
    with pytest.raises(ValueError, match="archive fingerprint"):
        work_pack.require_integrated(str(pool.repo), CHILD, spec)
    with pytest.raises(ValueError, match="archive fingerprint"):
        wbs.require_integrated_for_task(str(pool.repo), CHILD)
    archive = pool.repo / "doc/harness/archive/batch/TASK__a"
    archive.rename(archive.with_name("moved"))
    with pytest.raises(ValueError, match="missing"):
        work_pack.route(str(pool.repo), CHILD, spec, measure=False)


def test_all_integrated_still_requires_batch_close(tmp_path, monkeypatch):
    from test_batch_state import complete
    pool, spec = pack(tmp_path, monkeypatch)
    pool.init(requests(), owner=CHILD)
    pool.claim()
    complete(pool)
    complete(pool, "b", "worker-b")
    pool.cli("finish", "--slug", "a")
    pool.cli("finish", "--slug", "b")
    assert route(pool, spec)["route"] == "batch"
    assert "Close the fully integrated batch" in route(pool, spec)["next_action"]


def test_abandoned_closed_pack_stays_unfinished(tmp_path, monkeypatch):
    pool, spec = pack(tmp_path, monkeypatch)
    pool.init(requests(), owner=CHILD)
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "not dispatched")
    pool.cli("abandon", "--slug", "b", "--worker-stopped", "--reason", "not dispatched")
    pool.cli("close")
    result = route(pool, spec)
    assert result["route"] == "batch" and result["unfinished"] == {"a": "abandoned", "b": "abandoned"}
    assert "cannot complete" in result["next_action"]


def test_halted_pool_routes_recovery(tmp_path, monkeypatch):
    pool, spec = pack(tmp_path, monkeypatch)
    pool.init(requests(), owner=CHILD)
    state = json.loads(pool.state.read_text())
    state["halted"] = True
    pool.state.write_text(json.dumps(state))
    assert "Recover" in route(pool, spec)["next_action"]


def test_integrated_labels_do_not_substitute_for_evidence(tmp_path, monkeypatch, change):
    import pytest
    from test_batch_state import _git
    pool, spec = pack(tmp_path, monkeypatch)
    integrated(pool)
    state = json.loads(pool.state.read_text())
    item = state["requests"][0]
    if change == "ancestry":
        _git("reset", "--hard", state["initial_head"], cwd=pool.repo)
    elif change == "branch":
        _git("checkout", "-qb", "elsewhere", cwd=pool.repo)
    elif change == "archive_symlink":
        archive = pool.repo / "doc/harness/archive/batch/TASK__a"
        target = archive.with_name("moved")
        archive.rename(target)
        archive.symlink_to(target, target_is_directory=True)
    elif change == "run":
        item["run_id"] = item["checkpoint"]["run_id"] = "0198c349-5800-7000-8000-000000000002"
        pool.state.write_text(json.dumps(state))
    elif change == "harvest":
        item["checkpoint"]["harvest"] = None
        pool.state.write_text(json.dumps(state))
    elif change == "residual_path":
        Path(item["worktree"]).mkdir()
    elif change == "residual_ref":
        _git("branch", item["branch"], cwd=pool.repo)
    elif change == "residual_registration":
        _git("worktree", "add", "--detach", item["worktree"], cwd=pool.repo)
        import shutil
        shutil.rmtree(item["worktree"])
    else:
        del item["checkpoint"]
        pool.state.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        route(pool, spec)


def test_symlinked_missing_state_parent_is_refused(tmp_path, monkeypatch):
    import pytest
    pool, spec = pack(tmp_path, monkeypatch)
    runtime = pool.repo / "doc/harness/runtime"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    runtime.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        route(pool, spec)


def mcp_integration(tmp_path, monkeypatch):
    from test_automatic_parallel_routing_contract import make_context, declare
    from test_harness_mcp_server import HarnessMcpServerTests

    context = make_context(tmp_path, monkeypatch)
    pool, call, _ = context
    declare(context)
    integrated(pool)
    started = call("task_start", {"task_id": CHILD})
    assert not started.get("isError"), started
    planned = call("write_plan", {"task_id": CHILD, "plan": '# Integration\n```wbs\n[{"id":"A","title":"Combined result","weight":1,"check":["true"]}]\n```\n',
                                  "required_lenses": ["review-code", "qa-cli"]})
    assert not planned.get("isError"), planned
    import wbs
    directory = pool.repo / "doc/harness/tasks" / CHILD
    state = wbs.read_state(str(directory))
    assert state["started_at"] > 0 and state["last_progress_at"] == state["started_at"]
    assert state["scope_revision"] == 1
    before_due = wbs.task_progress(str(directory), str(pool.repo), state["run_id"], now=state["started_at"] + 7199)
    assert before_due["percentage"] == 0 and not before_due["refinement_due"]
    assert wbs.task_progress(str(directory), str(pool.repo), state["run_id"], now=state["started_at"] + 7200)["refinement_due"]
    directory = pool.repo / "doc/harness/tasks" / CHILD
    # Established isolated receipt fixture, never production attestation.
    HarnessMcpServerTests()._write_subagent_receipt(str(directory))
    checked = call("wbs", {"action":"check", "wbs_id":CHILD, "node_id":"A"})
    assert not checked.get("isError"), checked
    verified = call("task_verify", {"task_id": CHILD})
    assert not verified.get("isError"), verified
    assert verified["structuredContent"]["runtime_verdict"] == "PASS"
    return pool, call, directory


def damage_pack_proof(pool, proof):
    if proof == "missing":
        pool.state.unlink()
    else:
        archive = pool.repo / "doc/harness/archive/batch/TASK__a/PLAN.md"
        archive.write_text("altered preserved evidence\n")


def test_mcp_completed_pack_closes_integration_child_and_goal(tmp_path, monkeypatch):
    pool, call, directory = mcp_integration(tmp_path, monkeypatch)
    closed = call("task_close", {"task_id": CHILD})
    assert not closed.get("isError"), closed


def test_mcp_close_refuses_damaged_pack_without_closing_child(tmp_path, monkeypatch, proof):
    pool, call, directory = mcp_integration(tmp_path, monkeypatch)
    goal_file = directory / "WBS.json"
    before = (directory / "TASK.json").read_bytes(), goal_file.read_bytes()
    damage_pack_proof(pool, proof)
    closed = call("task_close", {"task_id": CHILD})
    assert closed.get("isError"), closed
    assert "work pack" in closed["structuredContent"]["error"] or "work-pack" in closed["structuredContent"]["error"]
    assert ((directory / "TASK.json").read_bytes(), goal_file.read_bytes()) == before




def test_integrated_pool_cannot_be_replayed_under_another_owner(tmp_path, monkeypatch):
    import pytest
    pool, spec = pack(tmp_path, monkeypatch)
    integrated(pool)
    copied = dict(spec, task_id='TASK__other')
    with pytest.raises(ValueError, match='pool owner mismatch'):
        work_pack.route(str(pool.repo), 'TASK__other', copied)
    assert route(pool, spec)['route'] == 'integration'


def test_pool_owner_metadata_is_hook_protected():
    import prewrite_gate
    path = "doc/harness/runtime/batches/pool.json"
    assert prewrite_gate._is_protected_artifact(path)
    assert prewrite_gate._protected_denial(path)[0] == "batch-pool-runtime"
