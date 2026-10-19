"""Exercise Goal-to-pool entry points and runtime payloads, not forged receipts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys



REPO = Path(__file__).resolve().parents[1]
CHILD = 'TASK__integration'
GOAL = 'GOAL__automatic-pack'


def make_context(tmp_path, monkeypatch):
    from test_batch_state import Pool, request
    from test_harness_mcp_server import HarnessMcpServerTests
    pool = Pool(tmp_path, monkeypatch)
    server = HarnessMcpServerTests()
    def call(name, args):
        return server._call_in_repo(str(pool.repo), name, args)
    return pool, call, [request('a'), request('b')]


def declare(context):
    import work_pack
    pool, call, requests = context
    spec = work_pack.make_spec(str(pool.repo), CHILD, requests)
    directory = pool.repo / 'doc/harness/tasks' / CHILD
    directory.mkdir(parents=True)
    state = dict(version=1, nodes=[], checks={}, run_id='', started_at=0,
                 last_progress_at=0, pack_only=True, batch=spec)
    (directory / 'WBS.json').write_text(json.dumps(state))
    pool.batch = spec['batch_id']
    return dict(task_id=CHILD, batch=spec)


def current_bytes(pool):
    return (pool.repo / 'doc/harness/tasks' / CHILD / 'WBS.json').read_bytes()


def test_unbaselined_pack_refuses_open_and_remains_unknown(tmp_path, monkeypatch):
    import wbs
    context = make_context(tmp_path, monkeypatch)
    pool, call, _ = context
    declare(context)
    result = call('wbs', dict(action='open', wbs_id=CHILD))
    assert result.get('isError') and 'unfinished' in result['structuredContent']['error']
    td = pool.repo / 'doc/harness/tasks' / CHILD
    assert not (td / 'TASK.json').exists()
    assert wbs.task_progress(str(td), str(pool.repo), '')['percentage'] is None
    before = current_bytes(pool)
    import pytest
    with pytest.raises(ValueError, match='finish the active work pack'):
        wbs.prepare(str(td), '# Plan\n```wbs\n[{"id":"A","title":"Result","weight":1}]\n```\n', 'new')
    assert current_bytes(pool) == before


def test_batch_claim_respects_actual_available_slots_and_rejects_negative(tmp_path, monkeypatch):
    context = make_context(tmp_path, monkeypatch)
    pool, _, requests = context
    pool.init(requests, cap=8)
    assert pool.cli('claim', '--available-slots', '0')['claims'] == []
    before = pool.state.read_bytes()
    result = pool.cli('claim', '--available-slots', '-1', ok=False)
    assert result['status'] != 'ok'
    assert pool.state.read_bytes() == before
    claimed = pool.cli('claim', '--available-slots', '1')['claims']
    assert [item['slug'] for item in claimed] == ['a']
    assert pool.cli('claim', '--available-slots', '0')['claims'] == []
    state = json.loads(pool.state.read_text())
    assert [item['status'] for item in state['requests']] == ['reserved', 'queued']


def test_both_runtime_payloads_ship_executable_dispatch_and_ready_lane_guide(tmp_path):
    from test_install_writable_payload import _load_install_module

    install = _load_install_module()
    claude = tmp_path / 'claude'
    codex = tmp_path / 'codex'
    install._build_claude_payload(claude)
    install._build_codex_payload(codex, codex)
    snapshot = dict(host_capacity=3, acs=[dict(id='AC-1', files=['src/a.py'], tests=['tests/a.py'])],
                    live_agents=[dict(id='root', role='coordinator')])
    results = []
    for root, skills in [(claude / 'plugin', 'skills'), (codex, 'internal-skills')]:
        assert (root / 'scripts/work_pack.py').is_file()
        assert (root / skills / 'develop/ready-lanes.md').is_file()
        completed = subprocess.run([sys.executable, str(root / 'scripts/parallel_dispatch.py')],
                                   input=json.dumps(snapshot), capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result['dispatch'][0]['mode'] == 'paired'
        results.append(result)
    assert results[0] == results[1]
    guide = (codex / 'internal-skills/develop/ready-lanes.md').read_text()
    assert '${CLAUDE_PLUGIN_ROOT}' not in guide
    assert '${HARNESS_PLUGIN_ROOT}' in guide
    assert (codex / 'internal-skills/batch/SKILL.md').is_file()


def pytest_generate_tests(metafunc):
    cases = {
        'malformed': [None, {}, 'batch', [], [None], [dict(slug='only', request='one', scopes=['a.txt'])]],
        'pool_state': ['missing', 'queued', 'abandoned'],
    }
    for names, values in cases.items():
        if all(name in metafunc.fixturenames for name in names.split(',')):
            metafunc.parametrize(names, values)
