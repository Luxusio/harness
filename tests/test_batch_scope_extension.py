"""Adapt real managed ownership through the public owner, preserving intent."""
import json
from pathlib import Path

from test_batch_state import RUN, _git, _head, task
from test_managed_wbs import setup_parent
from test_wbs import plan


def prepared(tmp_path, monkeypatch, slots=1, three=False):
    pool, call = setup_parent(tmp_path, monkeypatch)
    for name in ("a.txt", "b.txt", "c.txt"):
        (pool.repo / name).write_text(name + "\n")
    _git("add", "a.txt", "b.txt", "c.txt", cwd=pool.repo)
    _git("commit", "-qm", "seed independent inputs", cwd=pool.repo)
    if three:
        assert not call("plan", plan=plan([
            dict(id=k, title="Observable " + k, weight=1) for k in ("A", "B", "C")
        ])).get("isError")
    ownership = {k: [k.lower() + ".txt"] for k in (("A", "B", "C") if three else ("A", "B"))}
    response = call("dispatch", ownership=ownership, available_slots=slots)
    assert not response.get("isError"), response
    body = response["structuredContent"]
    pool.batch = body["batch_id"]
    handoff = next(h for h in body["handoffs"] if h["slug"].endswith("-A"))
    assert not call("dispatch", operation="bind", slug=handoff["slug"], worker_id="worker-a").get("isError")
    worktree = Path(handoff["worktree"])
    directory = task(worktree, handoff["slug"])
    return pool, call, handoff, worktree, directory


def extend(call, handoff, worktree, **changes):
    args = dict(operation="extend", slug=handoff["slug"], worker_id="worker-a",
                worker_stopped=True, expected_head=_head(worktree), expected_run_id=RUN,
                scopes=["b.txt"], reason="Newly discovered shared caller belongs to this outcome")
    args.update(changes)
    return call("dispatch", **args)


def test_public_scope_grant_preserves_intent_and_refills_independent_work(tmp_path, monkeypatch):
    pool, call, handoff, worktree, directory = prepared(tmp_path, monkeypatch, three=True)
    parent = pool.repo / "doc/harness/tasks/TASK__managed/WBS.json"
    original_intent = parent.read_bytes()
    original_control = (directory / "TASK.json").read_bytes()
    original_marker = (worktree / ".harness-batch-bootstrap").read_bytes()
    granted = extend(call, handoff, worktree, reason="x" * 2000)
    assert not granted.get("isError"), granted
    operation = granted["structuredContent"]["operation_result"]
    assert operation["handoff"]["scopes"] == ["a.txt", "b.txt"]
    state = json.loads(pool.state.read_text())
    owner = next(r for r in state["requests"] if r["slug"] == handoff["slug"])
    assert state["schema_version"] == 3
    assert owner["scopes"] == ["a.txt"]
    assert owner["extra_scopes"] == {"b.txt": str(pool.repo / "b.txt")}
    assert parent.read_bytes() == original_intent
    assert (directory / "TASK.json").read_bytes() == original_control
    assert (worktree / ".harness-batch-bootstrap").read_bytes() == original_marker
    assert not (directory / "RECEIPTS.jsonl").exists()
    recorded = pool.state.read_bytes()
    assert not extend(call, handoff, worktree, reason="x" * 2000).get("isError")
    assert pool.state.read_bytes() == recorded
    refill = call("dispatch", available_slots=2)["structuredContent"]
    assert {h["slug"].rsplit("-", 1)[1] for h in refill["handoffs"]} == {"C"}
    assert refill["reasons"][handoff["slug"][:-1] + "B"] == "retained scope ownership"
    for name in ("a.txt", "b.txt"):
        (worktree / name).write_text("implemented\n")
    _git("add", "a.txt", "b.txt", cwd=worktree)
    _git("commit", "-qm", "implement granted ownership", cwd=worktree)
    task(worktree, handoff["slug"], closed=True)  # isolated fixture, not production evidence
    result = dict(task_id=handoff["task_id"], worktree=str(worktree), branch=handoff["branch"],
                  commit=_head(worktree), verdict="closed", run_id=RUN)
    completed = call("dispatch", operation="complete", slug=handoff["slug"], worker_id="worker-a",
                     worker_stopped=True, result=result)
    assert not completed.get("isError"), completed
    assert not worktree.exists()
    refill = call("dispatch", available_slots=1)["structuredContent"]
    assert any(h["slug"].endswith("-B") for h in refill["handoffs"])


def test_scope_refusals_preserve_record_and_current_source(tmp_path, monkeypatch):
    pool, call, handoff, worktree, directory = prepared(tmp_path, monkeypatch)
    for changes in (dict(worker_stopped=False), dict(worker_id="foreign"),
                    dict(expected_head="0" * 40), dict(expected_run_id=RUN[:-1] + "2"),
                    dict(scopes=["../outside"]), dict(scopes=[str(pool.repo / "b.txt")]),
                    dict(scopes=[".git/config"]), dict(reason="x" * 2001)):
        before = pool.state.read_bytes()
        refusal = extend(call, handoff, worktree, **changes)
        assert refusal.get("isError"), refusal
        assert pool.state.read_bytes() == before
    a, b = worktree / "a.txt", worktree / "b.txt"
    original_a, original_b = a.read_bytes(), b.read_bytes()
    for flag in ("assume-unchanged", "skip-worktree"):
        _git("update-index", "--" + flag, "b.txt", cwd=worktree)
        b.write_text("hidden outside old ownership\n")
        before = pool.state.read_bytes()
        refusal = extend(call, handoff, worktree)
        assert refusal.get("isError") and "index flags" in refusal["structuredContent"]["error"], refusal
        assert pool.state.read_bytes() == before
        assert b.read_text() == "hidden outside old ownership\n"
        assert _git("ls-files", "-v", "b.txt", cwd=worktree).startswith("h " if flag == "assume-unchanged" else "S ")
        _git("update-index", "--no-" + flag, "b.txt", cwd=worktree)
        b.write_bytes(original_b)
    for staged, restored in ((False, False), (True, False), (True, True)):
        b.write_text("outside old ownership\n")
        if staged:
            _git("add", "b.txt", cwd=worktree)
        if restored:
            b.write_bytes(original_b)  # staged changes must not hide behind worktree bytes
        before = pool.state.read_bytes()
        refusal = extend(call, handoff, worktree)
        assert refusal.get("isError") and "prior changes exceed" in refusal["structuredContent"]["error"]
        assert pool.state.read_bytes() == before
        if staged:
            _git("restore", "--staged", "b.txt", cwd=worktree)
        b.write_bytes(original_b)
    extra = worktree / "new-extra.txt"
    extra.write_text("unowned prior work\n")
    before = pool.state.read_bytes()
    refusal = extend(call, handoff, worktree, scopes=["new-extra.txt"])
    assert refusal.get("isError") and "prior changes exceed" in refusal["structuredContent"]["error"]
    assert extra.read_text() == "unowned prior work\n" and pool.state.read_bytes() == before
    extra.unlink()
    _git("rm", "a.txt", cwd=worktree)
    _git("mv", "b.txt", "a.txt", cwd=worktree)
    refusal = extend(call, handoff, worktree)
    assert refusal.get("isError") and "prior changes exceed" in refusal["structuredContent"]["error"]
    _git("restore", "--staged", "a.txt", "b.txt", cwd=worktree)
    a.write_bytes(original_a)
    b.write_bytes(original_b)
    b.write_text("committed outside ownership\n")
    _git("add", "b.txt", cwd=worktree)
    _git("commit", "-qm", "unowned change fixture", cwd=worktree)
    before = pool.state.read_bytes()
    refusal = extend(call, handoff, worktree)
    assert refusal.get("isError") and "prior changes exceed" in refusal["structuredContent"]["error"]
    assert pool.state.read_bytes() == before and b.read_text() == "committed outside ownership\n"


def test_scope_conflict_and_replacement_worker_keep_grant_history(tmp_path, monkeypatch):
    pool, call, handoff, worktree, _ = prepared(tmp_path, monkeypatch, slots=2)
    before = pool.state.read_bytes()
    refused = extend(call, handoff, worktree)
    assert refused.get("isError") and "collides" in refused["structuredContent"]["error"]
    assert pool.state.read_bytes() == before
    granted = extend(call, handoff, worktree, scopes=["shared.txt"])
    assert not granted.get("isError"), granted
    grants = json.loads(pool.state.read_text())["requests"][0]["scope_grants"]
    reserved = call("dispatch", operation="resume", slug=handoff["slug"], worker_stopped=True)
    assert not reserved.get("isError"), reserved
    rebound = call("dispatch", operation="bind", slug=handoff["slug"], worker_id="replacement")
    assert not rebound.get("isError"), rebound
    state = json.loads(pool.state.read_text())
    assert state["requests"][0]["scope_grants"] == grants
    assert state["requests"][0]["worker_id"] == "replacement"
    assert state["requests"][0]["extra_scopes"] == {"shared.txt": str(pool.repo / "shared.txt")}


def test_same_path_changes_during_scope_grant_preserve_record(tmp_path, monkeypatch):
    import batch_state

    pool, call, handoff, worktree, _ = prepared(tmp_path, monkeypatch)
    owned = worktree / "a.txt"
    original_preflight = batch_state.batch_preflight.preflight

    for kind in ("working", "index", "untracked", "mode"):
        owned.write_text("first dirty contents\n")
        if kind == "untracked":
            _git("rm", "--cached", "a.txt", cwd=worktree)
        if kind == "index":
            _git("add", "a.txt", cwd=worktree)

        def mutate_after_path_observation(*args, **kwargs):
            report = original_preflight(*args, **kwargs)
            if kind == "mode":
                owned.chmod(owned.stat().st_mode ^ 0o100)
            else:
                owned.write_text("second dirty contents\n")
                if kind == "index":
                    _git("add", "a.txt", cwd=worktree)
                    owned.write_text("first dirty contents\n")
            return report

        before = pool.state.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(batch_state.batch_preflight, "preflight", mutate_after_path_observation)
            refused = extend(call, handoff, worktree)
        assert refused.get("isError") and "source changed" in refused["structuredContent"]["error"], refused
        assert pool.state.read_bytes() == before
        if kind in {"index", "untracked"}:
            _git("restore", "--staged", "a.txt", cwd=worktree)


def test_legacy_scope_migration_is_publication_only(tmp_path, monkeypatch):
    pool, call, handoff, worktree, _ = prepared(tmp_path, monkeypatch)
    legacy = json.loads(pool.state.read_text())
    legacy["schema_version"] = 1
    pool.state.write_text(json.dumps(legacy))  # isolated legacy-state fixture
    before = pool.state.read_bytes()
    assert not call("dispatch", operation="status").get("isError")
    assert pool.state.read_bytes() == before
    migrated = extend(call, handoff, worktree)
    assert not migrated.get("isError"), migrated
    assert json.loads(pool.state.read_text())["schema_version"] == 3
    legacy["requests"][0]["extra_scopes"] = {"b.txt": str(pool.repo / "b.txt")}
    pool.state.write_text(json.dumps(legacy))
    before = pool.state.read_bytes()
    refused = call("dispatch", operation="status")
    assert refused.get("isError"), refused
    assert pool.state.read_bytes() == before
