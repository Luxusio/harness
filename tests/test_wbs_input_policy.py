"""Control reads, explicit verification inputs, local parking and resume rollback."""
import json
import os
import ctypes
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

from test_wbs import baseline, check, node, plan, progress, wbs


def policy_plan(nodes, policy):
    return plan(nodes) + "\n```wbs-inputs\n" + json.dumps(policy) + "\n```\n"


def publish(td, nodes, policy):
    text = policy_plan(nodes, policy)
    state = wbs.prepare(str(td), text, "run", now=100)
    (td / "PLAN.md").write_text(text)
    (td / "WBS.json").write_text(wbs.encode_state(state))


def public(tmp_path, nodes=None):
    from test_harness_mcp_server import HarnessMcpServerTests
    repo, td = baseline(tmp_path)
    (td / "WBS.json").unlink()
    server = HarnessMcpServerTests()
    def call(action, **kwargs):
        return server._call_in_repo(str(repo), "wbs", dict(action=action, wbs_id="sample", **kwargs))
    opened = call("open")["structuredContent"]
    assert not call("plan", plan=plan(nodes or [node()])).get("isError")
    return repo, td, call, opened


def test_context_next_resume_plan_and_dispatch_do_not_hash_source(tmp_path, monkeypatch):
    repo, td, call, opened = public(tmp_path, [node("A"), node("B")])
    def forbidden(*args, **kwargs):
        pytest.fail("control operation attempted source measurement")
    monkeypatch.setattr(wbs, "fingerprint", forbidden)
    for action in ("context", "next", "open"):
        result = call(action)
        assert not result.get("isError"), result
        assert result["structuredContent"]["progress"]["status"] == "unmeasured"
        assert result["structuredContent"]["progress"]["percentage"] is None
    assert not call("plan", plan=policy_plan([node("A"), node("B")],
                    dict(source=["src.txt"], deny=["mise.toml", "doc/HANDOFF.md", "target/release/examples/runtime_bench"]))).get("isError")
    state = wbs.read_state(str(td))
    assert wbs.work_pack(str(td), str(repo), opened["run_id"], ownership={"A": ["src.txt"], "B": ["other.txt"]})
    assert call("open")["structuredContent"]["run_id"] == opened["run_id"]


def test_denied_and_excluded_candidates_are_filtered_before_resolve_or_open(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    denied = repo / "mise.toml"
    denied.symlink_to(tmp_path / "outside-secret")
    publish(td, [node()], dict(source=["src.txt"], deny=["mise.toml"]))
    actual_resolve, actual_open = Path.resolve, os.open
    def resolve(path, *args, **kwargs):
        assert path != denied, "forbidden path resolved"
        return actual_resolve(path, *args, **kwargs)
    def opening(path, *args, **kwargs):
        assert os.fspath(path) not in {"mise.toml", str(denied)}, "forbidden file opened"
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(os, "open", opening)
    assert check(repo, td)["passed"]
    assert progress(repo, td)["percentage"] == 100


@pytest.mark.parametrize("policy", [dict(source=["src.txt"], deny=["src.txt"]),
                                   dict(source=["src.txt"], exclude=["src.txt"]),
                                   dict(source=["missing-input"]),
                                   dict(deny=["private"])])
def test_required_input_unavailable_is_not_run_without_partial_credit(tmp_path, monkeypatch, policy):
    repo, td = baseline(tmp_path)
    publish(td, [node()], policy)
    original = wbs._execute
    def execute(argv, *args, **kwargs):
        assert argv[0] == "git", "assertion ran without required inputs"
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(wbs, "_execute", execute)
    before = (td / "WBS.json").read_bytes()
    result = check(repo, td)
    assert result["passed"] is None and result["execution"] == "not_run"
    assert result["progress"]["percentage"] is None
    assert (td / "WBS.json").read_bytes() == before


@pytest.mark.skipif(sys.platform != "linux", reason="kernel file-access observer requires inotify")
@pytest.mark.parametrize("source,deny,available", [
    (["src.txt"], ["private"], True),
    (["src.txt"], [".gitignore"], True),
    (["private"], ["private/.gitignore"], False),
    (None, ["private"], False),
])
def test_denied_git_metadata_is_never_opened_in_subprocesses(tmp_path, source, deny, available):
    repo, td = baseline(tmp_path)
    secret = repo / "private"
    secret.mkdir()
    ignore = secret / ".gitignore"
    ignore.write_text("[core]\n    editor = forbidden\n")
    # Git startup can read arbitrary includes in addition to nested ignore files.
    with (repo / ".git/config").open("a") as config:
        config.write('\n[include]\n    path = ../private/.gitignore\n')
    (repo / ".gitignore").write_text("unused\n")
    policy = dict(deny=deny)
    if source is not None:
        policy["source"] = source
    publish(td, [node()], policy)
    libc = ctypes.CDLL(None, use_errno=True)
    observer = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    assert observer >= 0
    try:
        for path in (ignore, repo / ".gitignore"):
            assert libc.inotify_add_watch(observer, os.fsencode(path), 0x21) >= 0  # ACCESS | OPEN
        result = check(repo, td)
        if available:
            assert result["passed"] is True
        else:
            assert result["execution"] == "not_run" and result["passed"] is None
        with pytest.raises(BlockingIOError):
            os.read(observer, 4096)
    finally:
        os.close(observer)


def test_explicit_directories_hash_ignored_inputs_without_git(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    source = repo / "source"
    source.mkdir()
    (source / "build-input.bin").write_bytes(b"first")
    (repo / ".gitignore").write_text("*.bin\n")
    policy = dict(source=["source"], deny=["private"])
    monkeypatch.setattr(wbs, "_execute", lambda *a, **k: pytest.fail("explicit source invoked Git"))
    before = wbs.fingerprint(repo, policy)
    (source / "build-input.bin").write_bytes(b"second")
    assert before != wbs.fingerprint(repo, policy)
    monkeypatch.setattr(wbs, "MAX_LISTING_BYTES", 2)
    with pytest.raises(ValueError, match="listing"):
        wbs.fingerprint(repo, policy)


@pytest.mark.parametrize("path", ["../secret", "/tmp/secret", "src/../file", "src/", "src//file", "C:/secret", "src\\file"])
def test_invalid_policy_is_rejected_lexically(tmp_path, monkeypatch, path):
    repo, td = baseline(tmp_path)
    monkeypatch.setattr(wbs, "fingerprint", lambda *a, **k: pytest.fail("plan hashed source"))
    before = (td / "WBS.json").read_bytes()
    with pytest.raises(ValueError, match="canonical"):
        wbs.prepare(str(td), policy_plan([node()], dict(source=[path])), "run")
    assert (td / "WBS.json").read_bytes() == before


def test_policy_change_preserves_records_but_never_promotes_old_proof(tmp_path):
    repo, td = baseline(tmp_path)
    assert check(repo, td)["passed"]
    old = wbs.read_state(str(td))
    publish(td, [node()], dict(source=["src.txt"]))
    state = wbs.read_state(str(td))
    assert state["checks"] == old["checks"] and state["run_id"] == old["run_id"]
    assert progress(repo, td)["percentage"] == 0
    assert wbs.task_progress(str(td), str(repo), "run", measure=False)["historical_percentage"] == 0
    assert check(repo, td)["passed"]
    assert progress(repo, td)["percentage"] == 100
    (repo / "src.txt").write_text("changed")
    assert progress(repo, td)["percentage"] == 0


def test_source_and_artifact_budgets_are_separate_and_report_input(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    (repo / "release.zip").write_bytes(b"z" * 64)
    publish(td, [node()], dict(source=["src.txt"], artifacts=["release.zip"]))
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 8)
    monkeypatch.setattr(wbs, "MAX_ARTIFACT_BYTES", 128)
    assert check(repo, td)["passed"]
    monkeypatch.setattr(wbs, "MAX_ARTIFACT_BYTES", 32)
    result = check(repo, td)
    assert result["execution"] == "not_run" and result["passed"] is None
    assert "artifact" in result["measurement_error"] and "release.zip" in result["measurement_error"]
    assert "32" in result["measurement_error"] and result["progress"]["percentage"] is None


def test_policy_limits_actual_inputs_rather_than_all_listed_paths(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    for n in range(12):
        (repo / f"unrelated-{n}").write_text("x" * 64)
    publish(td, [node()], dict(source=["src.txt"]))
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_ENTRIES", 1)
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 8)
    assert check(repo, td)["passed"]


def test_unmeasured_history_cannot_authorize_close_and_verify_measures(tmp_path, monkeypatch):
    repo, td, call, _ = public(tmp_path)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    context = call("context")["structuredContent"]["progress"]
    assert context["percentage"] is None and context["historical_percentage"] == 100
    assert not any(row["verified"] for row in context["outcomes"])
    calls = []
    def measure(*args, **kwargs):
        calls.append(True)
        raise ValueError("required input denied")
    monkeypatch.setattr(wbs, "fingerprint", measure)
    call("verify")
    assert len(calls) == 1
    result = call("close")
    assert result.get("isError") and len(calls) == 2
    assert "verified WBS outcomes" in result["structuredContent"]["missing_for_close"]


def test_local_block_preserves_independent_next_and_does_not_revive_proofs(tmp_path):
    nodes = [node("A"), node("B", depends_on=["A"]), node("C")]
    repo, td, call, opened = public(tmp_path, nodes)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    assert call("check", node_id="B")["structuredContent"]["passed"]
    old = wbs.read_state(str(td))
    result = call("block", node_id="A", blocked_reason="External helper provenance unknown", unblock_condition="Verify helper origin")
    assert not result.get("isError"), result
    assert call("context")["structuredContent"]["task_context"]["status"] == "open"
    assert call("next")["structuredContent"]["ready"] == ["C"]
    assert wbs.read_state(str(td))["run_id"] == opened["run_id"]
    for key in ("A", "B"):
        proof = wbs.read_state(str(td))["checks"][key]
        assert proof["invalidated"] and {k: v for k, v in proof.items() if k != "invalidated"} == old["checks"][key]
    assert not call("open", node_id="A").get("isError")
    assert call("next")["structuredContent"]["ready"] == ["A", "C"]
    assert call("check", node_id="A")["structuredContent"]["passed"]
    assert call("next")["structuredContent"]["ready"] == ["B", "C"]


def test_resume_context_failure_restores_block_run_receipts_and_ownership(tmp_path, monkeypatch):
    from test_harness_mcp_server import harness_server
    repo, td, call, opened = public(tmp_path)
    assert not call("block", blocked_reason="Waiting for external fixture", unblock_condition="Fixture restored").get("isError")
    blocked = (td / "BLOCKED.md").read_bytes()
    control = (td / "TASK.json").read_bytes()
    wbs_bytes = (td / "WBS.json").read_bytes()
    tasks = repo / "doc/harness/tasks"
    def markers():
        paths = [tasks / ".active", *list((tasks / ".active_sessions").glob("*.json"))]
        return {str(p): p.read_bytes() for p in paths if p.is_file()}
    before = markers()
    receipt = td / "RECEIPTS.jsonl"
    receipts = receipt.read_bytes() if receipt.exists() else None
    with monkeypatch.context() as patch:
        patch.setattr(harness_server, "emit_compact_context", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("context failed after publication")))
        result = call("open")
        assert result.get("isError"), result
    assert (td / "BLOCKED.md").read_bytes() == blocked
    assert (td / "TASK.json").read_bytes() == control and (td / "WBS.json").read_bytes() == wbs_bytes
    assert markers() == before
    assert (receipt.read_bytes() if receipt.exists() else None) == receipts
    resumed = call("open")
    assert not resumed.get("isError"), resumed
    assert resumed["structuredContent"]["run_id"] == opened["run_id"]


def _scoped_node(key, sources, **extra):
    item = node(key, inputs={"source": sources}, **extra)
    item["check"] = [sys.executable, "-c", "from pathlib import Path; " +
                     "; ".join(f"assert Path({name!r}).read_text() == 'ok'" for name in sources)]
    return item


def test_unrelated_scope_preserves_checks_and_shared_inputs_invalidate_both(tmp_path):
    nodes = [_scoped_node("A", ["a.txt", "shared.txt"]),
             _scoped_node("B", ["b.txt", "shared.txt"])]
    repo, td = baseline(tmp_path, nodes)
    for name in ("a.txt", "b.txt", "shared.txt"):
        (repo / name).write_text("ok")
    assert check(repo, td, "A")["passed"]
    assert check(repo, td, "B")["passed"]
    before_b = wbs.read_state(str(td))["checks"]["B"]
    (repo / "a.txt").write_text("changed")
    measured = progress(repo, td)
    assert measured["percentage"] == 50
    assert {row["id"] for row in measured["outcomes"] if row["verified"]} == {"B"}
    (repo / "notes.md").write_text("unrelated documentation")
    assert progress(repo, td)["percentage"] == 50
    assert wbs.read_state(str(td))["checks"]["B"] == before_b
    (repo / "shared.txt").write_text("changed")
    assert progress(repo, td)["percentage"] == 0


def test_scope_policy_change_does_not_promote_old_proof_and_dependencies_stale(tmp_path):
    nodes = [_scoped_node("A", ["a.txt"]),
             _scoped_node("B", ["b.txt"], depends_on=["A"])]
    repo, td = baseline(tmp_path, nodes)
    for name in ("a.txt", "b.txt", "extra.txt"):
        (repo / name).write_text("ok")
    assert check(repo, td, "A")["passed"]
    assert check(repo, td, "B")["passed"]
    nodes[0]["inputs"]["source"].append("extra.txt")
    text = plan(nodes)
    state = wbs.prepare(str(td), text, "run", now=110)
    (td / "PLAN.md").write_text(text)
    (td / "WBS.json").write_text(wbs.encode_state(state))
    assert progress(repo, td)["percentage"] == 0
    assert check(repo, td, "A")["passed"]
    assert progress(repo, td)["percentage"] == 100
    (repo / "a.txt").write_text("changed")
    assert progress(repo, td)["percentage"] == 0


def test_identical_scopes_are_measured_once_and_queries_never_rehash(tmp_path, monkeypatch):
    nodes = [_scoped_node(key, ["src.txt"]) for key in ("A", "B")]
    repo, td = baseline(tmp_path, nodes)
    calls = []
    original = wbs.fingerprint
    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(wbs, "fingerprint", observed)
    assert check(repo, td, "A")["passed"]
    assert len(calls) == 2  # One before and one after, not one per leaf.
    assert check(repo, td, "B")["passed"]
    before = len(calls)
    result = wbs.task_progress(str(td), str(repo), "run", measure=False)
    assert result["historical_percentage"] == 100 and result["percentage"] is None
    assert len(calls) == before


def test_unrelated_scope_losing_credit_does_not_reset_progress_clock(tmp_path, monkeypatch):
    nodes = [_scoped_node("A", ["a.txt"]), _scoped_node("B", ["b.txt"])]
    repo, td = baseline(tmp_path, nodes)
    for name in ("a.txt", "b.txt"):
        (repo / name).write_text("ok")
    assert check(repo, td, "A", now=101)["passed"]
    original = wbs._execute
    def concurrent_change(*args, **kwargs):
        result = original(*args, **kwargs)
        (repo / "a.txt").write_text("changed independently")
        return result
    monkeypatch.setattr(wbs, "_execute", concurrent_change)
    result = check(repo, td, "B", now=200)
    assert result["passed"] and result["progress"]["percentage"] == 50
    assert result["progress"]["last_progress_at"] == 101
    assert wbs.read_state(str(td))["last_progress_at"] == 101


def test_root_deny_cannot_be_removed_by_leaf_policy(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    (repo / "private.txt").write_text("never read this fixture")
    publish(td, [_scoped_node("A", ["private.txt"])],
            {"source": ["src.txt"], "deny": ["private.txt"]})
    actual_open = os.open
    def opening(path, *args, **kwargs):
        assert os.fspath(path) not in {"private.txt", str(repo / "private.txt")}
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", opening)
    monkeypatch.setattr(wbs, "_execute", lambda *a, **k: pytest.fail("forbidden input must block command"))
    result = check(repo, td)
    assert result["execution"] == "not_run"
    assert result["measurement_status"] == "unavailable"
    assert wbs.read_state(str(td))["checks"] == {}


def test_missing_unrelated_scope_does_not_block_independent_check_or_destroy_proof(tmp_path):
    nodes = [_scoped_node("A", ["a.txt"]), _scoped_node("B", ["b.txt"])]
    repo, td = baseline(tmp_path, nodes)
    (repo / "a.txt").write_text("ok")
    result = check(repo, td, "A")
    assert result["passed"] and result["execution"] == "completed"
    assert result["measurement_status"] == "measured"
    assert result["progress"]["percentage"] is None  # B cannot yet be measured.
    assert wbs.read_state(str(td))["checks"]["A"]["passed"]
    (repo / "b.txt").write_text("ok")
    assert check(repo, td, "B")["passed"]
    (repo / "b.txt").unlink()
    assert check(repo, td, "A")["passed"]
    saved = wbs.read_state(str(td))["checks"]["A"]
    (repo / "b.txt").write_text("ok")
    assert progress(repo, td)["percentage"] == 100
    assert wbs.read_state(str(td))["checks"]["A"] == saved
