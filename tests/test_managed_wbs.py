"""Public WBS worktree ownership, admission and completion with real Git."""
from pathlib import Path

from test_batch_state import Pool, RUN, task, _git, _head
from test_wbs import plan


def setup_parent(tmp_path, monkeypatch):
    from test_harness_mcp_server import HarnessMcpServerTests
    pool, server = Pool(tmp_path, monkeypatch), HarnessMcpServerTests()

    def call(action, **args):
        return server._call_in_repo(str(pool.repo), "wbs", dict(action=action, wbs_id="managed", **args))

    assert not call("open").get("isError")
    nodes = [dict(id=k, title="Observable result " + k, weight=1) for k in ("A", "B")]
    assert not call("plan", plan=plan(nodes)).get("isError")
    return pool, call


def test_public_mcp_owns_creation_and_stopped_completion(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, available_slots=2)
    assert not prepared.get("isError"), prepared
    first = prepared["structuredContent"]
    assert len(first["handoffs"]) == 2
    repeat = call("dispatch", available_slots=0)
    assert not repeat.get("isError"), repeat
    assert {x["worktree"] for x in repeat["structuredContent"]["handoffs"]} == {x["worktree"] for x in first["handoffs"]}
    assert call("context")["structuredContent"]["pending_worktrees"]["pools"]
    assert call("open").get("isError")
    for index, handoff in enumerate(first["handoffs"]):
        slug, worktree = handoff["slug"], Path(handoff["worktree"])
        worker = "actual-native-worker-" + str(index)
        assert not call("dispatch", operation="bind", slug=slug, worker_id=worker).get("isError")
        (worktree / ("a.txt" if index == 0 else "b.txt")).write_text("implemented\n")
        # Never stage the owner-managed retention marker.
        _git("add", "a.txt" if index == 0 else "b.txt", cwd=worktree)
        _git("commit", "-qm", slug, "--trailer", "Harness-Task: " + handoff["task_id"], cwd=worktree)
        task(worktree, slug, closed=True)  # isolated fixture only
        actual = dict(task_id=handoff["task_id"], worktree=str(worktree), branch=handoff["branch"],
                      commit=_head(worktree), verdict="closed", run_id=RUN, blocked_reason=None)
        assert call("dispatch", operation="complete", slug=slug, worker_id=worker, result=actual).get("isError")
        assert worktree.exists()
        done = call("dispatch", operation="complete", slug=slug, worker_id=worker, result=actual, worker_stopped=True)
        assert not done.get("isError"), done
        assert not worktree.exists()
        assert not _git("branch", "--list", handoff["branch"], cwd=pool.repo)
        assert (pool.repo / "doc/harness/archive/batch" / handoff["task_id"] / "TASK.json").is_file()
        replay = call("dispatch", operation="complete", slug=slug, worker_id=worker, result=actual, worker_stopped=True)
        assert not replay.get("isError"), replay
        changed = dict(actual, commit="0" * 40)
        assert call("dispatch", operation="complete", slug=slug, worker_id=worker, result=changed, worker_stopped=True).get("isError")
    assert done["structuredContent"]["status"] == "closed"
    assert not call("open").get("isError")
    assert not call("context")["structuredContent"]["pending_worktrees"]["pools"]


def test_invalid_dispatch_arguments_do_not_park_or_create(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    for args in (dict(operation="unknown"), dict(operation="status", ownership={}),
                 dict(available_slots=True), dict(operation="complete", slug="x", worker_id="x", result={}),
                 dict(operation="resume", slug="x", worker_stopped=1),
                 dict(operation="prepare", recovery_commit="a" * 40),
                 dict(operation="recover", slug="x", worker_stopped=True, recovery_commit="HEAD")):
        assert call("dispatch", **args).get("isError")
        assert call("context")["structuredContent"]["task_context"]["status"] == "open"
    assert not (pool.repo / "doc/harness/runtime/batches").exists()


def test_public_conflict_recovery_requires_exact_acknowledged_tip(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, available_slots=1)
    assert not prepared.get("isError"), prepared
    handoff, = prepared["structuredContent"]["handoffs"]
    slug, worktree = handoff["slug"], Path(handoff["worktree"])
    assert not call("dispatch", operation="bind", slug=slug, worker_id="actual-worker").get("isError")
    (worktree / "a.txt").write_text("lead\n")
    _git("add", "a.txt", cwd=worktree)
    _git("commit", "-qm", "lead", "--trailer", "Harness-Task: " + handoff["task_id"], cwd=worktree)
    task(worktree, slug, closed=True)
    actual = dict(task_id=handoff["task_id"], worktree=str(worktree), branch=handoff["branch"],
                  commit=_head(worktree), verdict="closed", run_id=RUN)
    (pool.repo / "a.txt").write_text("destination\n")
    _git("add", "a.txt", cwd=pool.repo)
    _git("commit", "-qm", "destination", cwd=pool.repo)
    refused = call("dispatch", operation="complete", slug=slug, worker_id="actual-worker", result=actual, worker_stopped=True)
    assert not refused.get("isError"), refused
    assert refused["structuredContent"]["status"] == "recovery-required"
    destination = _head(pool.repo)
    # Simulate the reviewed recovery task resolving the original conflict.
    _git("rebase", "--strategy-option=theirs", destination, cwd=worktree)
    resolved = _head(worktree)
    assert resolved != actual["commit"]
    assert call("dispatch", operation="recover", slug=slug, worker_stopped=True).get("isError")
    assert call("dispatch", operation="recover", slug=slug, worker_stopped=True, recovery_commit="a" * 40).get("isError")
    done = call("dispatch", operation="recover", slug=slug, worker_stopped=True, recovery_commit=resolved)
    assert not done.get("isError"), done
    assert not worktree.exists()
    assert _head(pool.repo) == resolved
    assert (pool.repo / "a.txt").read_text() == "lead\n"


def test_public_complete_retains_unique_ignored_content(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, available_slots=1)
    assert not prepared.get("isError"), prepared
    handoff, = prepared["structuredContent"]["handoffs"]
    slug, worktree = handoff["slug"], Path(handoff["worktree"])
    assert not call("dispatch", operation="bind", slug=slug, worker_id="actual-worker").get("isError")
    (worktree / "a.txt").write_text("lead\n")
    _git("add", "a.txt", cwd=worktree)
    _git("commit", "-qm", "lead", "--trailer", "Harness-Task: " + handoff["task_id"], cwd=worktree)
    task(worktree, slug, closed=True)
    actual = dict(task_id=handoff["task_id"], worktree=str(worktree), branch=handoff["branch"],
                  commit=_head(worktree), verdict="closed", run_id=RUN)
    exclude = pool.repo / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "\nprecious.local\n")
    precious = worktree / "precious.local"
    precious.write_bytes(b"unique ignored user data\x00\xff")
    marker = (worktree / ".harness-batch-bootstrap").read_bytes()
    kept = call("dispatch", operation="complete", slug=slug, worker_id="actual-worker", result=actual, worker_stopped=True)
    assert not kept.get("isError"), kept
    body = kept["structuredContent"]
    assert body["status"] in {"kept", "recovery-required"}, body
    assert body["operation_result"]["integrated_tip"]
    assert precious.read_bytes() == b"unique ignored user data\x00\xff"
    assert (worktree / ".harness-batch-bootstrap").read_bytes() == marker
    assert _git("branch", "--list", handoff["branch"], cwd=pool.repo)
    assert call("open").get("isError")


def test_failed_parent_park_has_no_worktree_effect(tmp_path, monkeypatch):
    from test_harness_mcp_server import harness_server
    pool, call = setup_parent(tmp_path, monkeypatch)
    monkeypatch.setattr(harness_server, "handle_task_blocked", lambda args: harness_server._err("fixture park refusal"))
    refused = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]})
    assert refused.get("isError"), refused
    assert not (pool.repo / "doc/harness/runtime/batches").exists()
    assert len(_git("worktree", "list", "--porcelain", cwd=pool.repo).split("worktree ")) == 2


def test_schema_keeps_single_wbs_tool_with_lifecycle_operations():
    from test_harness_mcp_server import harness_server
    tools = harness_server.list_tools()
    assert [x["name"] for x in tools] == ["wbs"]
    properties = tools[0]["inputSchema"]["properties"]
    assert set(properties["operation"]["enum"]) == {"prepare", "status", "bind", "complete", "resume", "recover", "extend"}
    assert properties["worker_stopped"]["type"] == "boolean"
    assert properties["scopes"]["minItems"] == 1
    assert properties["expected_head"]["type"] == "string"
    assert properties["expected_run_id"]["type"] == "string"
    assert properties["reason"]["maxLength"] == 2000



def test_public_dirty_original_survives_prepare_integrate_and_dispose(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    shared = pool.repo / 'shared.txt'
    shared.write_text('staged user version\n')
    _git('add', 'shared.txt', cwd=pool.repo)
    shared.write_text('unstaged user version\n')
    note = pool.repo / 'local\nnotes.txt'
    note.write_bytes(b'untracked user bytes\x00\xff')
    original_index = _git('ls-files', '--stage', '-z', cwd=pool.repo).split('\0')
    original_status = _git('status', '--porcelain=v1', '-z', cwd=pool.repo)
    prepared = call('dispatch', ownership={'A': ['a.txt'], 'B': ['b.txt']}, available_slots=2)
    assert not prepared.get('isError'), prepared
    results = []
    for index, handoff in enumerate(prepared['structuredContent']['handoffs']):
        worker = 'dirty-public-worker-' + str(index)
        slug, tree = handoff['slug'], Path(handoff['worktree'])
        assert not call('dispatch', operation='bind', slug=slug, worker_id=worker).get('isError')
        rel = 'a.txt' if index == 0 else 'b.txt'
        (tree / rel).write_text('implemented\n')
        _git('add', rel, cwd=tree)
        _git('commit', '-qm', slug, '--trailer', 'Harness-Task: ' + handoff['task_id'], cwd=tree)
        task(tree, slug, closed=True)  # isolated existing fixture only
        result = dict(task_id=handoff['task_id'], worktree=str(tree), branch=handoff['branch'],
                      commit=_head(tree), verdict='closed', run_id=RUN)
        results.append((handoff, worker, result))
    for handoff, worker, result in results:
        done = call('dispatch', operation='complete', slug=handoff['slug'], worker_id=worker,
                    result=result, worker_stopped=True)
        assert not done.get('isError'), done
        assert not Path(result['worktree']).exists()
        assert not _git('branch', '--list', handoff['branch'], cwd=pool.repo)
        assert all(entry in _git('ls-files', '--stage', '-z', cwd=pool.repo).split('\0')
                   for entry in original_index)
        assert _git('status', '--porcelain=v1', '-z', cwd=pool.repo) == original_status
        assert shared.read_text() == 'unstaged user version\n'
        assert _git('show', ':shared.txt', cwd=pool.repo) == 'staged user version'
        assert note.read_bytes() == b'untracked user bytes\x00\xff'
    assert done['structuredContent']['status'] == 'closed'


def test_public_scope_extension_preserves_unrelated_dirty_original(tmp_path, monkeypatch):
    import json
    from test_batch_scope_extension import prepared, extend
    pool, call, handoff, worktree, directory = prepared(tmp_path, monkeypatch)
    # Newly granted ownership may not cover pending user edits.
    requested = pool.repo / 'b.txt'
    baseline = requested.read_bytes()
    requested.write_text('user change in requested scope\n')
    state_before = pool.state.read_bytes()
    refused = extend(call, handoff, worktree)
    assert refused.get('isError'), refused
    assert 'overlap' in refused['structuredContent']['error']
    assert pool.state.read_bytes() == state_before
    assert requested.read_text() == 'user change in requested scope\n'
    requested.write_bytes(baseline)  # restore this isolated test's fixture only
    shared = pool.repo / 'shared.txt'
    shared.write_text('staged unrelated\n')
    _git('add', 'shared.txt', cwd=pool.repo)
    shared.write_text('unstaged unrelated\n')
    note = pool.repo / 'notes\nlocal.txt'
    note.write_bytes(b'untracked unrelated\x00\xff')
    status = _git('status', '--porcelain=v1', '-z', cwd=pool.repo)
    index = _git('ls-files', '--stage', '-z', cwd=pool.repo)
    original_task = (directory / 'TASK.json').read_bytes()
    original_intent = (pool.repo / 'doc/harness/tasks/TASK__managed/WBS.json').read_bytes()
    granted = extend(call, handoff, worktree)
    assert not granted.get('isError'), granted
    assert not extend(call, handoff, worktree).get('isError')
    owner = next(item for item in json.loads(pool.state.read_text())['requests'] if item['slug'] == handoff['slug'])
    assert owner['scopes'] == ['a.txt']
    assert owner['extra_scopes'] == {'b.txt': str(pool.repo / 'b.txt')}
    assert (directory / 'TASK.json').read_bytes() == original_task
    assert (pool.repo / 'doc/harness/tasks/TASK__managed/WBS.json').read_bytes() == original_intent
    assert _git('status', '--porcelain=v1', '-z', cwd=pool.repo) == status
    assert _git('ls-files', '--stage', '-z', cwd=pool.repo) == index
    assert shared.read_text() == 'unstaged unrelated\n'
    assert note.read_bytes() == b'untracked unrelated\x00\xff'


def test_public_dirty_ownership_overlap_refuses_before_parent_park(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    parent = pool.repo / 'doc/harness/tasks/TASK__managed'
    original = {name: (parent / name).read_bytes() for name in ('TASK.json', 'WBS.json')}
    user = pool.repo / 'a.txt'
    user.write_bytes(b'uncommitted user implementation\x00\xff')
    refused = call('dispatch', ownership={'A': ['a.txt'], 'B': ['b.txt']}, available_slots=2)
    assert refused.get('isError'), refused
    assert 'overlap' in refused['structuredContent']['error']
    assert all((parent / name).read_bytes() == raw for name, raw in original.items())
    assert not (parent / 'BLOCKED.md').exists()
    assert not (pool.repo / 'doc/harness/runtime/batches').exists()
    assert user.read_bytes() == b'uncommitted user implementation\x00\xff'
