"""Public WBS worktree ownership, admission and completion with real Git."""
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")

from test_batch_state import Pool, RUN, task, _git, _head
from test_wbs import plan


def setup_parent(tmp_path, monkeypatch):
    from test_harness_mcp_server import HarnessMcpServerTests
    pool, server = Pool(tmp_path, monkeypatch), HarnessMcpServerTests()

    def call(action, **args):
        if action == 'dispatch' and args.get('operation', 'prepare') == 'prepare' and 'host' not in args and 'available_slots' not in args:
            args['host'] = host_snapshot()
        return server._call_in_repo(str(pool.repo), "wbs", dict(action=action, wbs_id="managed", **args))

    assert not call("open").get("isError")
    nodes = [dict(id=k, title="Observable result " + k, weight=1) for k in ("A", "B")]
    assert not call("plan", plan=plan(nodes)).get("isError")
    from test_harness_mcp_server import _write_marker_fixture
    _write_marker_fixture(str(pool.repo), str(pool.repo / 'doc/harness/tasks/TASK__managed'), session_id='codex-prewrite-fixture')
    return pool, call


def test_public_mcp_owns_creation_and_stopped_completion(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, host=host_snapshot())
    assert not prepared.get("isError"), prepared
    first = prepared["structuredContent"]
    assert len(first["handoffs"]) == 2
    repeat = call("dispatch", host=host_snapshot(capacity=2))
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
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, host=host_snapshot())
    assert not prepared.get("isError"), prepared
    handoff = prepared["structuredContent"]["handoffs"][0]
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
    prepared = call("dispatch", ownership={"A": ["a.txt"], "B": ["b.txt"]}, host=host_snapshot())
    assert not prepared.get("isError"), prepared
    handoff = prepared["structuredContent"]["handoffs"][0]
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
    _git('config', 'merge.autoStash', 'true', cwd=pool.repo)
    shared = pool.repo / 'shared.txt'
    shared.write_text('staged user version\n')
    _git('add', 'shared.txt', cwd=pool.repo)
    shared.write_text('unstaged user version\n')
    note = pool.repo / 'local\nnotes.txt'
    note.write_bytes(b'untracked user bytes\x00\xff')
    original_index = _git('ls-files', '--stage', '-z', cwd=pool.repo).split('\0')
    original_status = _git('status', '--porcelain=v1', '-z', cwd=pool.repo)
    prepared = call('dispatch', ownership={'A': ['a.txt'], 'B': ['b.txt']}, host=host_snapshot())
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
    refused = call('dispatch', ownership={'A': ['a.txt'], 'B': ['b.txt']}, host=host_snapshot())
    assert refused.get('isError'), refused
    assert 'overlap' in refused['structuredContent']['error']
    assert all((parent / name).read_bytes() == raw for name, raw in original.items())
    assert not (parent / 'BLOCKED.md').exists()
    assert not (pool.repo / 'doc/harness/runtime/batches').exists()
    assert user.read_bytes() == b'uncommitted user implementation\x00\xff'


def host_snapshot(capacity=4, live=()):
    return dict(capacity=capacity, live_agents=[dict(id='coordinator', role='coordinator'), *live],
                reserved_slots=[], verification_reserve=1,
                spawn_available=True, continuation_available=True)


def test_public_dispatch_admission_and_completion_refill(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    nodes = [dict(id=k, title='Observable result ' + k, weight=1,
                  **({'depends_on': ['A']} if k == 'C' else {})) for k in ('A', 'B', 'C')]
    assert not call('plan', plan=plan(nodes)).get('isError')
    prepared = call('dispatch', host=host_snapshot(), ownership={k: [k.lower()+'.py'] for k in ('A', 'B', 'C')})
    assert not prepared.get('isError'), prepared
    handoffs = prepared['structuredContent']['handoffs']
    assert len(handoffs) == 2
    assert set(prepared['structuredContent']['execution_admission']['selected']) == {x['slug'] for x in handoffs}
    first, second = handoffs
    for n, item in enumerate(handoffs):
        assert not call('dispatch', operation='bind', slug=item['slug'], worker_id='worker-'+str(n)).get('isError')
    from test_codex_worktree_prewrite import check
    sibling = Path(second['worktree'])
    directory = task(sibling, second['slug'])
    from test_harness_mcp_server import _write_marker_fixture
    _write_marker_fixture(str(sibling), str(directory), session_id='codex-prewrite-fixture')
    assert check(pool.repo, sibling/'b.py') == {}
    assert check(pool.repo, sibling/'a.py').get('permissionDecision') == 'deny'
    worktree = Path(first['worktree'])
    (worktree / 'a.py').write_text('A reviewed and verified while B develops\n')
    _git('add', 'a.py', cwd=worktree)
    _git('commit', '-qm', 'A', '--trailer', 'Harness-Task: '+first['task_id'], cwd=worktree)
    task(worktree, first['slug'], closed=True)
    actual = dict(task_id=first['task_id'], worktree=str(worktree), branch=first['branch'],
                  commit=_head(worktree), verdict='closed', run_id=RUN)
    done = call('dispatch', operation='complete', slug=first['slug'], worker_id='worker-0',
                result=actual, worker_stopped=True)
    assert not done.get('isError'), done
    assert Path(second['worktree']).is_dir()
    pool.batch = prepared['structuredContent']['batch_id']
    for unavailable in ('spawn_available', 'continuation_available'):
        host = host_snapshot(live=[dict(id='worker-1', role='implementation')])
        host[unavailable] = False
        before = pool.state.read_bytes()
        deferred = call('dispatch', host=host)
        assert not deferred.get('isError'), deferred
        body = deferred['structuredContent']
        assert body['capability_deferred'] is True
        assert body['handoffs'] == [] and body['execution_admission']['selected'] == []
        assert body['reasons']['capability'][0][unavailable] is False
        assert pool.state.read_bytes() == before
        assert Path(second['worktree']).is_dir()
    refill = call('dispatch', host=host_snapshot(live=[dict(id='worker-1', role='implementation')]))
    assert not refill.get('isError'), refill
    third, = refill['structuredContent']['handoffs']
    assert refill['structuredContent']['execution_admission']['selected'] == [third['slug']]
    assert done['structuredContent']['refill_required'] is True
    assert third['slug'].endswith('-C')
    assert third['spawn_head'] == _head(pool.repo)
    pool.batch = prepared['structuredContent']['batch_id']
    state = pool.state.read_text()
    assert 'worker-1' in state and '"running"' in state


def test_public_direct_assignment_is_fresh_control_not_source_fingerprint(tmp_path, monkeypatch):
    import json
    import pytest
    import wbs
    from test_codex_worktree_prewrite import check
    pool, call = setup_parent(tmp_path, monkeypatch)
    nodes = [dict(id='A', title='A', weight=1), dict(id='B', title='B', weight=1, depends_on=['A'])]
    assert not call('plan', plan=plan(nodes)).get('isError')
    td = pool.repo / 'doc/harness/tasks/TASK__managed'
    assert 'deny' == check(pool.repo, pool.repo/'a.py').get('permissionDecision')
    control = json.loads((td/'TASK.json').read_bytes())
    control['execution_mode'] = 'micro'
    (td/'TASK.json').write_text(json.dumps(control))
    (td/'MAINTENANCE').write_text('Admission gate regression\n')
    assert 'deny' == check(pool.repo, pool.repo/'a.py').get('permissionDecision')
    assert check(pool.repo, pool.repo/'doc/harness/REQ__admission.md') == {}
    assignment = call('dispatch', ownership={'A': ['a.py'], 'B': ['b.py']}, host=host_snapshot())
    assert not assignment.get('isError'), assignment
    assert assignment['structuredContent']['execution_admission']['mode'] == 'direct'
    assert check(pool.repo, pool.repo/'a.py') == {}
    (pool.repo/'a.py').write_text('source changed\n')
    assert check(pool.repo, pool.repo/'a.py') == {}
    assert 'deny' == check(pool.repo, pool.repo/'b.py').get('permissionDecision')
    before = (td/'WBS.json').read_bytes()
    state = json.loads(before)
    for edit in ('nodes', 'checks', 'blocks', 'ownership'):
        changed = json.loads(before)
        if edit == 'nodes': changed['nodes'][0]['title'] = 'Changed intent'
        elif edit == 'checks': changed['checks']['A'] = {'passed': False}
        elif edit == 'blocks': changed['blocks'] = {'A': dict(reason='actual blocker', unblock_condition='restore capability')}
        else: changed['execution_admission']['ownership']['A'] = ['b.py']
        (td/'WBS.json').write_text(json.dumps(changed))
        with pytest.raises(ValueError, match='missing or stale'):
            wbs.require_source_assignment(str(pool.repo), 'TASK__managed', 'a.py')
    (td/'WBS.json').write_bytes(before)
    assert call('context')['structuredContent']['task_context']['routing']['orchestration_mode'] == 'direct'
    assert not call('block', blocked_reason='Observed prerequisite unavailable', unblock_condition='Restore that prerequisite').get('isError')
    assert call('context')['structuredContent']['task_context']['source_write_allowed'] is False


def _linked_direct(pool, worktree=None):
    from test_harness_mcp_server import HarnessMcpServerTests
    tree = worktree or pool.tmp / 'ordinary-linked'
    if worktree is None:
        _git('worktree', 'add', '-qb', 'ordinary-linked', str(tree), cwd=pool.repo)
    server = HarnessMcpServerTests()

    def call(action, **args):
        return server._call_in_repo(str(tree), 'wbs', dict(action=action, wbs_id='ordinary', **args))

    assert not call('open').get('isError')
    assert not call('plan', plan=plan([dict(id='A', title='Assigned source', weight=1)])).get('isError')
    from test_harness_mcp_server import _write_marker_fixture
    _write_marker_fixture(str(tree), str(tree / 'doc/harness/tasks/TASK__ordinary'), session_id='codex-prewrite-fixture')
    return tree, tree / 'doc/harness/tasks/TASK__ordinary', call


def _admit_linked_direct(call):
    assigned = call('dispatch', ownership={'A': ['a.py']}, host=host_snapshot())
    assert not assigned.get('isError'), assigned
    assert assigned['structuredContent']['execution_admission']['mode'] == 'direct'


def test_ordinary_linked_direct_requires_fresh_assignment_and_scope(tmp_path, monkeypatch):
    import json
    import wbs
    from test_codex_worktree_prewrite import check
    pool = Pool(tmp_path, monkeypatch)
    tree, directory, call = _linked_direct(pool)
    assert not (pool.repo / 'doc/harness/runtime/batches').exists()
    assert check(pool.repo, tree / 'a.py').get('permissionDecision') == 'deny'
    _admit_linked_direct(call)
    assert wbs.require_source_assignment(str(tree), 'TASK__ordinary', 'a.py')['mode'] == 'direct'
    assert check(pool.repo, tree / 'a.py') == {}
    from test_prewrite_gate_dormant import _invoke_payload
    with monkeypatch.context() as runtime:
        runtime.setenv('HARNESS_RUNTIME', 'codex')
        runtime.delenv('HARNESS_DISABLE_SCOPE_LOCK', raising=False)
        written = _invoke_payload(str(tree), dict(cwd=str(tree), session_id='codex-prewrite-fixture',
                                  tool_name='Write', tool_input=dict(file_path=str(tree / 'a.py'), content='x = 1\n')))
    assert written.returncode == 0 and not written.stderr, written.stderr
    assert not written.stdout.strip(), written.stdout
    denied = check(pool.repo, tree / 'outside.py')
    assert denied.get('permissionDecision') == 'deny'
    assert 'outside direct execution ownership' in denied['permissionDecisionReason']
    original = (directory / 'WBS.json').read_bytes()
    for mutation in ('run', 'definition'):
        state = json.loads(original)
        if mutation == 'run':
            state['execution_admission']['run_id'] = RUN
        else:
            state['nodes'][0]['title'] = 'Changed accepted intent'
        (directory / 'WBS.json').write_text(json.dumps(state))
        denied = check(pool.repo, tree / 'a.py')
        assert denied.get('permissionDecision') == 'deny'
        assert 'missing or stale' in denied['permissionDecisionReason']
    (directory / 'WBS.json').write_bytes(original)
    assert check(pool.repo, tree / 'a.py') == {}
    assert not (pool.repo / 'doc/harness/runtime/batches').exists()


def test_ordinary_linked_direct_with_unrelated_batch(tmp_path, monkeypatch):
    import wbs
    from test_batch_state import request
    from test_codex_worktree_prewrite import check
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request('unrelated')])
    pool.claim()
    before = pool.state.read_bytes()
    tree, _, call = _linked_direct(pool)
    _admit_linked_direct(call)
    assert wbs.require_source_assignment(str(tree), 'TASK__ordinary', 'a.py')['mode'] == 'direct'
    assert check(pool.repo, tree / 'a.py') == {}
    assert check(pool.repo, tree / 'outside.py').get('permissionDecision') == 'deny'
    assert pool.state.read_bytes() == before


@pytest.mark.parametrize('stage', ['reserved', 'creation-intent'])
def test_managed_checkout_cannot_escape_through_foreign_direct_task(tmp_path, monkeypatch, stage):
    import json
    import wbs
    import batch_worktrees as managed
    from test_batch_worktrees import setup, call as prepare
    from test_codex_worktree_prewrite import check
    pool, spec = setup(tmp_path, monkeypatch)
    if stage == 'creation-intent':
        original = managed._call

        def stop(repo, spec, directory, records, command, **kwargs):
            if command == 'bootstrap':
                raise RuntimeError('after Git creation before bootstrap binding')
            return original(repo, spec, directory, records, command, **kwargs)

        with monkeypatch.context() as fault:
            fault.setattr(managed, '_call', stop)
            with pytest.raises(RuntimeError, match='after Git creation'):
                prepare(pool, spec, available_slots=1)
        owner = json.loads(pool.state.read_bytes())['requests'][0]
        assert not owner.get('worktree')
        tree = Path(owner['creation_intent']['worktree'])
    else:
        handoff, = prepare(pool, spec, available_slots=1)['handoffs']
        tree = Path(handoff['worktree'])
    task(tree)
    with pytest.raises(ValueError, match='managed lead is not currently bound'):
        wbs.require_source_assignment(str(tree), 'TASK__a', 'a.py')
    tree, directory, call = _linked_direct(pool, tree)
    _admit_linked_direct(call)
    before = pool.state.read_bytes()
    controls = {name: (directory / name).read_bytes() for name in ('TASK.json', 'WBS.json')}
    denied = check(pool.repo, tree / 'a.py')
    assert denied.get('permissionDecision') == 'deny'
    assert 'managed' in denied['permissionDecisionReason']
    assert pool.state.read_bytes() == before
    assert all((directory / name).read_bytes() == raw for name, raw in controls.items())


@pytest.mark.parametrize('ancestor', ['writable-main', 'foreign-main', 'symlink-runtime'])
def test_ordinary_linked_direct_ignores_main_ancestors_without_batch_metadata(tmp_path, monkeypatch, ancestor):
    import os
    import shutil
    import subprocess
    import wbs
    from test_codex_worktree_prewrite import check
    from test_prewrite_gate_dormant import _invoke_payload
    pool = Pool(tmp_path, monkeypatch)
    tree, directory, call = _linked_direct(pool)
    _admit_linked_direct(call)
    batches = pool.repo / 'doc/harness/runtime/batches'
    original = pool.repo.stat()
    elevated = []
    if ancestor == 'foreign-main':
        if os.getuid() != 0:
            sudo = shutil.which('sudo')
            if not sudo or subprocess.run([sudo, '-n', 'true'], capture_output=True).returncode:
                pytest.skip('changing isolated main-root ownership requires root or passwordless sudo')
            elevated = [sudo, '-n']
    controls = {name: (directory / name).read_bytes() for name in ('TASK.json', 'WBS.json')}
    try:
        if ancestor == 'symlink-runtime':
            external = tmp_path / 'external-runtime'
            external.mkdir()
            batches.parent.symlink_to(external, target_is_directory=True)
        else:
            # Make main itself the nearest existing ancestor; the linked tree
            # retains its own manifest and admitted task throughout the check.
            (pool.repo / 'doc').rename(tmp_path / 'main-doc')
            if ancestor == 'writable-main':
                pool.repo.chmod(0o777)
            else:
                foreign_uid = 0 if os.getuid() != 0 else 1
                subprocess.run([*elevated, 'chown', str(foreign_uid), str(pool.repo)],
                               capture_output=True, text=True, check=True)
                assert pool.repo.stat().st_uid != os.getuid()
        with pytest.raises(FileNotFoundError):
            batches.lstat()
        assert wbs.require_source_assignment(str(tree), 'TASK__ordinary', 'a.py')['mode'] == 'direct'
        assert check(tree, tree / 'a.py') == {}
        denied = check(tree, tree / 'outside.py')
        assert denied.get('permissionDecision') == 'deny'
        assert 'outside direct execution ownership' in denied['permissionDecisionReason']
        with monkeypatch.context() as runtime:
            runtime.setenv('HARNESS_RUNTIME', 'codex')
            runtime.delenv('HARNESS_DISABLE_SCOPE_LOCK', raising=False)
            written = _invoke_payload(str(tree), dict(cwd=str(tree), session_id='codex-prewrite-fixture',
                                      tool_name='Write', tool_input=dict(file_path=str(tree / 'a.py'), content='x = 1\n')))
        assert written.returncode == 0 and not written.stderr, written.stderr
        assert not written.stdout.strip(), written.stdout
        with pytest.raises(FileNotFoundError):
            batches.lstat()
        assert all((directory / name).read_bytes() == raw for name, raw in controls.items())
    finally:
        if ancestor == 'foreign-main':
            subprocess.run([*elevated, 'chown', f'{original.st_uid}:{original.st_gid}', str(pool.repo)],
                           capture_output=True, text=True, check=True)
        pool.repo.chmod(original.st_mode & 0o7777)


def test_ordinary_linked_direct_rejects_inaccessible_batch_ancestor(tmp_path, monkeypatch):
    import os
    import wbs
    from test_codex_worktree_prewrite import check
    if os.getuid() == 0:
        pytest.skip('root bypasses the real directory search-permission check')
    pool = Pool(tmp_path, monkeypatch)
    tree, directory, call = _linked_direct(pool)
    _admit_linked_direct(call)
    batches = pool.repo / 'doc/harness/runtime/batches'
    batches.parent.mkdir()
    original_mode = batches.parent.stat().st_mode & 0o7777
    controls = {name: (directory / name).read_bytes() for name in ('TASK.json', 'WBS.json')}
    try:
        batches.parent.chmod(0)
        with pytest.raises(PermissionError):
            batches.lstat()
        with pytest.raises(PermissionError):
            wbs.require_source_assignment(str(tree), 'TASK__ordinary', 'a.py')
        denied = check(tree, tree / 'a.py')
        assert denied.get('permissionDecision') == 'deny'
        assert 'Permission denied' in denied['permissionDecisionReason']
        assert all((directory / name).read_bytes() == raw for name, raw in controls.items())
    finally:
        batches.parent.chmod(original_mode)
    assert not batches.exists()


@pytest.mark.parametrize('unsafe', ['file', 'dangling-symlink', 'ancestor', 'writable-runtime'])
def test_ordinary_linked_direct_rejects_unsafe_batch_metadata(tmp_path, monkeypatch, unsafe):
    import os
    from test_codex_worktree_prewrite import check
    pool = Pool(tmp_path, monkeypatch)
    tree, directory, call = _linked_direct(pool)
    _admit_linked_direct(call)
    batches = pool.repo / 'doc/harness/runtime/batches'
    if unsafe == 'ancestor':
        external = tmp_path / 'external-runtime'
        external.mkdir()
        (external / 'batches').mkdir()
        batches.parent.symlink_to(external, target_is_directory=True)
        evidence = batches.parent
    else:
        batches.parent.mkdir()
        if unsafe == 'file':
            batches.write_bytes(b'precious invalid batch directory\x00\xff')
        elif unsafe == 'dangling-symlink':
            batches.symlink_to(tmp_path / 'missing-batches', target_is_directory=True)
        else:
            batches.mkdir()
            batches.parent.chmod(0o777)
        evidence = batches.parent if unsafe == 'writable-runtime' else batches
    content = (os.readlink(evidence) if evidence.is_symlink() else
               sorted(os.listdir(evidence)) if evidence.is_dir() else evidence.read_bytes())
    before = os.lstat(evidence)
    controls = {name: (directory / name).read_bytes() for name in ('TASK.json', 'WBS.json')}
    assert check(pool.repo, tree / 'a.py').get('permissionDecision') == 'deny'
    after = os.lstat(evidence)
    assert (after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == (
        before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
    assert (os.readlink(evidence) if evidence.is_symlink() else
            sorted(os.listdir(evidence)) if evidence.is_dir() else evidence.read_bytes()) == content
    assert all((directory / name).read_bytes() == raw for name, raw in controls.items())


def test_public_wide_first_preserves_full_intake_and_selects_disjoint_pair(tmp_path, monkeypatch):
    import json
    pool, call = setup_parent(tmp_path, monkeypatch)
    nodes = [dict(id=k, title=k, weight=1) for k in ('WIDE', 'A', 'B')]
    assert not call('plan', plan=plan(nodes)).get('isError')
    result = call('dispatch', host=host_snapshot(), ownership={'A': ['src/a.py'], 'B': ['src/b.py'], 'WIDE': ['src']})
    assert not result.get('isError'), result
    body = result['structuredContent']
    assert {x['slug'].split('-')[-1] for x in body['handoffs']} == {'A', 'B'}
    assert set(body['execution_admission']['selected']) == {x['slug'] for x in body['handoffs']}
    state = json.loads((pool.repo/'doc/harness/tasks/TASK__managed/WBS.json').read_bytes())
    assert len(state['batch']['requests']) == 3


def test_standalone_bound_and_resumed_lead_keeps_source_assignment(tmp_path, monkeypatch):
    import json
    from test_batch_state import Pool, task, request
    from test_codex_worktree_prewrite import check
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request('a', 'a.py')])
    assert 'owner_task_id' not in json.loads(pool.state.read_bytes())
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    from test_harness_mcp_server import _write_marker_fixture
    _write_marker_fixture(str(worktree), str(directory), session_id='codex-prewrite-fixture')
    assert check(pool.repo, worktree / 'a.py') == {}
    assert check(pool.repo, worktree / 'outside.py').get('permissionDecision') == 'deny'
    (worktree / 'a.py').write_text('preserved interrupted source\n')
    proof = (directory / 'TASK.json').read_bytes()
    pool.cli('resume', '--slug', 'a', '--worker-stopped')
    assert check(pool.repo, worktree / 'a.py').get('permissionDecision') == 'deny'
    pool.cli('bind', '--slug', 'a', '--worker-id', 'replacement',
             '--worktree', worktree, '--branch', branch)
    assert check(pool.repo, worktree / 'a.py') == {}
    assert check(pool.repo, worktree / 'outside.py').get('permissionDecision') == 'deny'
    assert (directory / 'TASK.json').read_bytes() == proof
    assert (worktree / 'a.py').read_text() == 'preserved interrupted source\n'


def _ignore_original_source(pool):
    ignore = pool.repo / '.gitignore'
    ignore.write_text(ignore.read_text() + 'src/local.py\n')
    _git('add', '.gitignore', cwd=pool.repo)
    _git('commit', '-qm', 'ignore user-local source', cwd=pool.repo)
    shared = pool.repo / 'shared.txt'
    shared.write_text('staged user bytes\n')
    _git('add', 'shared.txt', cwd=pool.repo)
    shared.write_text('unstaged user bytes\n')
    return shared


def test_public_prepare_preserves_owned_ignored_original(tmp_path, monkeypatch):
    pool, call = setup_parent(tmp_path, monkeypatch)
    shared = _ignore_original_source(pool)
    local = pool.repo / 'src/local.py'
    local.parent.mkdir()
    local.write_bytes(b'precious original\x00\xff')
    original_index = _git('ls-files', '--stage', '-z', cwd=pool.repo)
    original_head = _head(pool.repo)
    refused = call('dispatch', ownership={'A': ['src'], 'B': ['b.py']}, host=host_snapshot())
    assert refused.get('isError') and 'ignored destination content' in str(refused)
    assert local.read_bytes() == b'precious original\x00\xff'
    assert shared.read_text() == 'unstaged user bytes\n'
    assert _git('ls-files', '--stage', '-z', cwd=pool.repo) == original_index
    assert _head(pool.repo) == original_head
    assert not (pool.repo / 'doc/harness/runtime/batches').exists()


def test_public_native_ff_retains_late_ignored_original_collision(tmp_path, monkeypatch):
    import batch_finish
    pool, call = setup_parent(tmp_path, monkeypatch)
    shared = _ignore_original_source(pool)
    original_index = _git('ls-files', '--stage', '-z', cwd=pool.repo)
    original_head = _head(pool.repo)
    prepared = call('dispatch', ownership={'A': ['src'], 'B': ['b.py']}, host=host_snapshot())
    assert not prepared.get('isError'), prepared
    lead = prepared['structuredContent']['handoffs'][0]
    tree = Path(lead['worktree'])
    assert not call('dispatch', operation='bind', slug=lead['slug'], worker_id='actual-a').get('isError')
    source = tree / 'src/local.py'
    source.parent.mkdir()
    source.write_text('lead replacement\n')
    _git('add', '-f', 'src/local.py', cwd=tree)
    _git('commit', '-qm', 'owned source', '--trailer', 'Harness-Task: '+lead['task_id'], cwd=tree)
    task(tree, lead['slug'], closed=True)
    result = dict(task_id=lead['task_id'], worktree=str(tree), branch=lead['branch'],
                  commit=_head(tree), verdict='closed', run_id=RUN)
    local = pool.repo / 'src/local.py'
    original_git = batch_finish._git
    def collision_at_merge(checkout, *args, **kwargs):
        if str(checkout) == str(pool.repo) and 'merge' in args:
            local.parent.mkdir(exist_ok=True)
            local.write_bytes(b'late precious original\x00\xff')
        return original_git(checkout, *args, **kwargs)
    monkeypatch.setattr(batch_finish, '_git', collision_at_merge)
    refused = call('dispatch', operation='complete', slug=lead['slug'], worker_id='actual-a',
                   result=result, worker_stopped=True)
    assert 'local.py' in str(refused) and 'overwritten' in str(refused)
    assert refused.get('isError') or refused['structuredContent']['status'] in {'kept', 'recovery-required'}
    assert local.read_bytes() == b'late precious original\x00\xff'
    assert shared.read_text() == 'unstaged user bytes\n'
    assert _git('ls-files', '--stage', '-z', cwd=pool.repo) == original_index
    assert _head(pool.repo) == original_head
    assert tree.is_dir() and _git('branch', '--list', lead['branch'], cwd=pool.repo)
