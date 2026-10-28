"""Direct linked admission uses the real public STDIO envelope and write gate."""
import json
import os
import subprocess
import sys

import pytest

from test_codex_worktree_prewrite import check, setup
from test_managed_wbs import host_snapshot
from test_worktree_workspace import SERVER_PATH, _worktree


SID = 'codex-prewrite-fixture'


def public(cwd, tree, action, **extra):
    args = dict(action=action, wbs_id='ordinary', **extra)
    if cwd != tree:
        args['workspace'] = str(tree)
    requests = [
        dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
            protocolVersion='2025-06-18', capabilities={}, clientInfo=dict(name='codex', version='1'))),
        dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='wbs', arguments=args)),
    ]
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(HARNESS_RUNTIME='claude', HARNESS_SESSION_ID=SID, CLAUDE_SESSION_ID='',
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', str(SERVER_PATH)], cwd=cwd, env=env,
                            input=''.join(json.dumps(row) + '\n' for row in requests),
                            capture_output=True, text=True, timeout=30, check=True)
    replies = [json.loads(row) for row in result.stdout.splitlines() if row.startswith('{')]
    return next(row['result'] for row in replies if row.get('id') == 2)


def planned(tmp_path, linked_cwd, nodes=('A',)):
    main, tree = setup(tmp_path)
    cwd = tree if linked_cwd else main
    assert not public(cwd, tree, 'open').get('isError')
    plan = '# Plan\n```wbs\n' + json.dumps([
        dict(id=key, title='Assigned source ' + key, weight=1) for key in nodes]) + '\n```\n'
    assert not public(cwd, tree, 'plan', plan=plan).get('isError')
    return main, tree, cwd, tree / 'doc/harness/tasks/TASK__ordinary'


def controls(directory):
    return {name: (directory / name).read_bytes() for name in ('TASK.json', 'WBS.json', 'PLAN.md')}


@pytest.mark.parametrize('linked_cwd', [False, True])
def test_stdio_linked_direct_prepare_and_real_gates(tmp_path, linked_cwd):
    main, tree, cwd, directory = planned(tmp_path, linked_cwd)
    (tree / 'user.py').write_text('original user change\n')
    assert check(main, tree / 'a.py').get('permissionDecision') == 'deny'
    result = public(cwd, tree, 'dispatch', ownership={'A': ['a.py']}, host=host_snapshot())
    assert not result.get('isError'), result
    assert result['structuredContent']['execution_admission']['mode'] == 'direct'
    assert check(main, tree / 'a.py') == {}
    assert check(main, tree / 'outside.py').get('permissionDecision') == 'deny'
    # Ordinary Write and Codex apply_patch consume the same exact focus and assignment.
    from test_prewrite_gate_dormant import _invoke_payload
    env = dict(os.environ)
    try:
        os.environ['HARNESS_RUNTIME'] = 'codex'
        os.environ.pop('HARNESS_DISABLE_SCOPE_LOCK', None)
        write = _invoke_payload(str(tree), dict(cwd=str(tree), session_id=SID,
                                tool_name='Write', tool_input=dict(file_path=str(tree / 'a.py'), content='x=1\n')))
    finally:
        os.environ.clear()
        os.environ.update(env)
    assert write.returncode == 0 and not write.stderr and not write.stdout.strip(), write
    for name in ('TASK.json', 'PLAN.md', 'WBS.json'):
        assert check(main, directory / name).get('permissionDecision') == 'deny'
    sibling = _worktree(main, 'sibling')
    assert check(main, sibling / 'a.py').get('permissionDecision') == 'deny'
    assert check(main, main / 'a.py').get('permissionDecision') == 'deny'
    original = (directory / 'WBS.json').read_bytes()
    for mutation in ('run', 'definition'):
        state = json.loads(original)
        if mutation == 'run':
            state['execution_admission']['run_id'] = '00000000-0000-7000-8000-000000000000'
        else:
            state['nodes'][0]['title'] = 'Changed accepted intent'
        (directory / 'WBS.json').write_text(json.dumps(state))
        assert check(main, tree / 'a.py').get('permissionDecision') == 'deny'
    (directory / 'WBS.json').write_bytes(original)
    marker = tree / 'doc/harness/tasks/.active_sessions' / (SID + '.json')
    marker_bytes = marker.read_bytes()
    marker.unlink()
    assert check(main, tree / 'a.py').get('permissionDecision') == 'deny'
    marker.write_bytes(marker_bytes)
    assert check(main, tree / 'a.py') == {}
    assert (tree / 'user.py').read_text() == 'original user change\n'
    assert not (directory / 'BLOCKED.md').exists()
    assert not (main / 'doc/harness/runtime/batches').exists()
    assert not (tree / 'doc/harness/runtime/batches').exists()


@pytest.mark.parametrize('linked_cwd', [False, True])
def test_stdio_linked_managed_prepare_refuses_before_publication(tmp_path, linked_cwd):
    main, tree, cwd, directory = planned(tmp_path, linked_cwd, ('A', 'B'))
    before = controls(directory)
    result = public(cwd, tree, 'dispatch', ownership={'A': ['a.py'], 'B': ['b.py']}, host=host_snapshot())
    assert result.get('isError'), result
    assert 'surviving control checkout' in result['structuredContent']['error']
    assert controls(directory) == before
    assert not (directory / 'BLOCKED.md').exists()
    assert not (main / 'doc/harness/runtime/batches').exists()
    assert not (tree / 'doc/harness/runtime/batches').exists()


@pytest.mark.parametrize('linked_cwd', [False, True])
def test_stdio_linked_pool_operations_refuse_without_mutation(tmp_path, linked_cwd):
    main, tree, cwd, directory = planned(tmp_path, linked_cwd)
    assert not public(cwd, tree, 'dispatch', ownership={'A': ['a.py']}, host=host_snapshot()).get('isError')
    before = controls(directory)
    for operation in ('status', 'bind', 'complete', 'resume', 'recover', 'extend'):
        result = public(cwd, tree, 'dispatch', operation=operation)
        assert result.get('isError'), result
        assert 'surviving control checkout' in result['structuredContent']['error']
        assert controls(directory) == before
    assert not (main / 'doc/harness/runtime/batches').exists()


@pytest.mark.parametrize('extra', [
    dict(ownership={'A': ['a.py']}),
    dict(ownership={'A': ['a.py']}, host={}),
    dict(ownership={'A': ['../escape']}, host=host_snapshot()),
    dict(ownership={'B': ['b.py']}, host=host_snapshot()),
])
def test_stdio_linked_invalid_prepare_preserves_controls(tmp_path, extra):
    _, tree, cwd, directory = planned(tmp_path, False)
    before = controls(directory)
    assert public(cwd, tree, 'dispatch', **extra).get('isError')
    assert controls(directory) == before
