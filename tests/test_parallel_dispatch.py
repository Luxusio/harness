"""Admission tests use fresh host snapshots; the dispatcher owns no durable state."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys



SCRIPT = Path(__file__).resolve().parents[1] / 'plugin/scripts/parallel_dispatch.py'
spec = importlib.util.spec_from_file_location('parallel_dispatch', SCRIPT)
dispatcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatcher)


def ac(aid, deps=()):
    return dict(id=aid, files=[f'src/{aid}.py'], tests=[f'tests/test_{aid}.py'], depends_on=list(deps))


def snapshot(capacity=5, count=3):
    return dict(acs=[ac(f'AC-{n}') for n in range(count)], host_capacity=capacity,
                live_agents=[dict(id='root', role='coordinator')])


def ids(result):
    return [item['ac_id'] for item in result['dispatch']]


def test_pair_is_atomic_and_uses_actual_slots():
    data = snapshot(capacity=4)
    result = dispatcher.schedule(data)
    assert ids(result) == ['AC-0']
    assert result['dispatch'][0]['slots'] == 2
    assert [worker['role'] for worker in result['dispatch'][0]['workers']] == ['implementation', 'test-author']
    assert result['remaining_slots'] == 1
    assert 'need 2' in result['deferred'][0]['reason']


def test_review_and_reserved_slots_count_against_host():
    data = snapshot(capacity=5)
    data['live_agents'] += [dict(id='review', role='review-code'),
                            dict(id='reserved', role='implementation', ac_id='AC-0')]
    data['progress'] = {'AC-0': {'status': 'running'}}
    assert ids(dispatcher.schedule(data)) == ['AC-1']


def test_unpaired_only_when_pair_cannot_ever_fit_host():
    result = dispatcher.schedule(snapshot(capacity=2))
    assert ids(result) == ['AC-0']
    assert result['dispatch'][0]['mode'] == 'unpaired'
    assert 'host cannot fit a pair' in result['dispatch'][0]['reason']
    assert len(result['dispatch'][0]['workers'][0]['paths']) == 2
    data = snapshot(capacity=3)
    data['live_agents'].append(dict(id='busy', role='review-code'))
    assert dispatcher.schedule(data)['dispatch'] == []
    assert dispatcher.schedule(snapshot(capacity=1))['dispatch'] == []


def test_refill_when_one_lane_finishes_while_sibling_is_busy():
    data = snapshot(capacity=5, count=4)
    assert ids(dispatcher.schedule(data)) == ['AC-0', 'AC-1']
    data['progress'] = {'AC-0': dict(status='completed', verified=True, workers_released=True),
                        'AC-1': dict(status='running')}
    data['live_agents'] += [dict(id='b-impl', role='implementation', ac_id='AC-1'),
                            dict(id='b-test', role='test-author', ac_id='AC-1')]
    result = dispatcher.schedule(data)
    assert ids(result) == ['AC-2']
    assert result['remaining_slots'] == 0


def test_partial_writer_exit_frees_only_real_slot():
    data = snapshot(capacity=4)
    data['progress'] = {'AC-0': dict(status='running')}
    data['live_agents'] += [dict(id='impl', role='implementation', ac_id='AC-0'),
                            dict(id='test', role='test-author', ac_id='AC-0')]
    assert dispatcher.schedule(data)['dispatch'] == []
    data['live_agents'].pop()
    assert ids(dispatcher.schedule(data)) == ['AC-1']
    # AC-0 still owns tests even after its test writer exited.
    data['acs'][1]['tests'] = data['acs'][0]['tests']
    assert ids(dispatcher.schedule(data)) == ['AC-2']


def test_dependencies_wait_for_own_verification_and_all_writers(verified, released, live):
    data = snapshot(capacity=6)
    data['acs'][1]['depends_on'] = ['AC-0']
    data['progress'] = {'AC-0': dict(status='completed', verified=verified, workers_released=released)}
    if live:
        data['live_agents'].append(dict(id='lingering', role='test-author', ac_id='AC-0'))
    assert ids(dispatcher.schedule(data)) == ['AC-2']


def test_verifying_lane_retains_paths_while_disjoint_work_refills():
    data = snapshot(capacity=5)
    data['acs'][1]['files'] = data['acs'][0]['files']
    data['progress'] = {'AC-0': dict(status='verifying', workers_released=True)}
    result = dispatcher.schedule(data)
    assert ids(result) == ['AC-2']
    assert result['available_slots'] == 4
    assert 'scope overlaps' in next(item['reason'] for item in result['deferred'] if item['ac_id'] == 'AC-1')
    data['progress']['AC-0'] = dict(status='completed', workers_released=True, verified=True)
    assert ids(dispatcher.schedule(data)) == ['AC-1', 'AC-2']


def test_completed_dependency_unlocks_without_sibling_barrier():
    data = snapshot(capacity=5)
    data['acs'][2]['depends_on'] = ['AC-0']
    data['progress'] = {'AC-0': dict(status='completed', verified=True, workers_released=True),
                        'AC-1': dict(status='verifying', workers_released=True)}
    assert ids(dispatcher.schedule(data)) == ['AC-2']


def test_failure_never_redispatches_or_releases_claims_implicitly(status):
    data = snapshot(capacity=7, count=4)
    data['acs'][1]['depends_on'] = ['AC-0']
    data['acs'][2]['files'] = data['acs'][0]['files']
    data['progress'] = {'AC-0': dict(status=status)}
    assert ids(dispatcher.schedule(data)) == ['AC-3']
    data['progress']['AC-0']['workers_released'] = True
    assert ids(dispatcher.schedule(data)) == ['AC-2', 'AC-3']


def test_ac_cap_applies_even_when_host_has_more_capacity():
    data = snapshot(capacity=30, count=10)
    assert len(ids(dispatcher.schedule(data))) == 4
    data['ac_cap'] = 8
    assert len(ids(dispatcher.schedule(data))) == 8
    data['ac_cap'] = 1
    data['progress'] = {'AC-0': dict(status='verifying', workers_released=True)}
    assert not dispatcher.schedule(data)['dispatch']


def test_aliases_and_ancestor_paths_are_exclusive_but_prefix_siblings_are_not():
    data = snapshot(capacity=9, count=4)
    data['acs'][0]['files'] = ['./src//shared']
    data['acs'][1]['files'] = ['src/shared/a.py']
    data['acs'][2]['files'] = ['src/shared']
    data['acs'][3]['files'] = ['src/shared_other/a.py']
    assert ids(dispatcher.schedule(data)) == ['AC-0', 'AC-3']


def test_live_external_claims_are_respected():
    data = snapshot(capacity=5)
    data['live_agents'].append(dict(id='external', role='other', paths=['src/AC-0.py']))
    assert ids(dispatcher.schedule(data)) == ['AC-1']


def test_symlink_alias_collisions_and_escape(tmp_path):
    import pytest

    (tmp_path / 'src').mkdir()
    (tmp_path / 'alias').symlink_to(tmp_path / 'src', target_is_directory=True)
    data = snapshot()
    data['repo'] = str(tmp_path)
    data['acs'][1]['files'] = ['alias/AC-0.py']
    assert ids(dispatcher.schedule(data)) == ['AC-0', 'AC-2']
    (tmp_path / 'outside').symlink_to(tmp_path.parent, target_is_directory=True)
    data['acs'][1]['files'] = ['outside/escaped.py']
    with pytest.raises(dispatcher.DispatchError, match='escapes repository'):
        dispatcher.schedule(data)


def test_invalid_paths_rejected(path):
    import pytest

    data = snapshot()
    data['acs'][0]['files'] = [path]
    with pytest.raises(dispatcher.DispatchError):
        dispatcher.schedule(data)


def test_malformed_snapshot_rejected(mutate, match):
    import pytest

    data = snapshot()
    mutate(data)
    with pytest.raises(dispatcher.DispatchError, match=match):
        dispatcher.schedule(data)


def test_multinode_cycle_and_overcapacity():
    import pytest

    data = snapshot()
    data['acs'][0]['depends_on'] = ['AC-1']
    data['acs'][1]['depends_on'] = ['AC-0']
    with pytest.raises(dispatcher.DispatchError, match='cycle'):
        dispatcher.schedule(data)
    data = snapshot(capacity=1)
    data['live_agents'].append(dict(id='extra', role='review-code'))
    with pytest.raises(dispatcher.DispatchError, match='exceeds'):
        dispatcher.schedule(data)


def test_snapshot_is_immutable_and_repeated_calls_are_deterministic():
    data = snapshot()
    before = copy.deepcopy(data)
    assert dispatcher.schedule(data) == dispatcher.schedule(data)
    assert data == before


def test_cli_json_success_and_actionable_errors(tmp_path):
    data = snapshot()
    result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(data), text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == dispatcher.schedule(data)
    path = tmp_path / 'snapshot.json'
    path.write_text(json.dumps(data))
    result = subprocess.run([sys.executable, str(SCRIPT), '--input', str(path)], text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == dispatcher.schedule(data)
    for invalid in ['{', '{}', '[]']:
        result = subprocess.run([sys.executable, str(SCRIPT)], input=invalid, text=True, capture_output=True)
        assert result.returncode == 2
        assert json.loads(result.stderr)['error']
        assert not result.stdout


def pytest_generate_tests(metafunc):
    cases = {
        'verified,released,live': [(False, True, False), (True, False, False), (True, True, True)],
        'status': ['failed', 'blocked', 'reservation_failed'],
        'path': ['../x', 'src/../x', '/tmp/x', '.', './', 'C:/x', r'src\x', 'a\x00b', '*.py'],
        'mutate,match': [
            (lambda d: d['acs'].append(ac('AC-0')), 'duplicate AC'),
            (lambda d: d['acs'][0].update(depends_on=['missing']), 'unknown dependency'),
            (lambda d: d['acs'][0].update(depends_on=['AC-1', 'AC-1']), 'unique AC'),
            (lambda d: d['acs'][0].update(depends_on=['AC-0']), 'cycle'),
            (lambda d: d.update(host_capacity=None), 'host_capacity'),
            (lambda d: d.update(host_capacity=True), 'host_capacity'),
            (lambda d: d.update(ac_cap=9), 'ac_cap'),
            (lambda d: d.update(live_agents=[]), 'coordinator'),
            (lambda d: d['live_agents'].append(dict(id='root', role='other')), 'unique'),
            (lambda d: d['live_agents'].append(dict(id='other', role='other', ac_id='unknown')), 'declared AC'),
            (lambda d: d.update(progress={'missing': {}}), 'declared AC'),
            (lambda d: d.update(progress={'AC-0': dict(status='completed', verified='yes')}), 'booleans'),
            (lambda d: d.update(progress={'AC-0': dict(status='pending', workers_released=False)}), 'pending'),
            (lambda d: d['acs'][0].update(tests=['src']), 'scopes overlap'),
        ],
    }
    for names, values in cases.items():
        if all(name in metafunc.fixturenames for name in names.split(',')):
            metafunc.parametrize(names, values)


def test_observed_host_capacity_reserves_real_verification_without_double_count():
    import pytest
    host = dict(capacity=5, live_agents=[dict(id='root', role='coordinator')],
                reserved_slots=[], verification_reserve=1,
                spawn_available=True, continuation_available=True)
    assert dispatcher.host_slots(host) == 3
    host['live_agents'].append(dict(id='qa', role='qa-cli'))
    assert dispatcher.host_slots(host) == 3
    host['reserved_slots'] = [dict(id='held', role='implementation')]
    assert dispatcher.host_slots(host) == 2
    host['live_agents'][-1]['role'] = 'qa-placeholder'
    assert dispatcher.host_slots(host) == 1
    for change in (dict(verification_reserve=0), dict(capacity=True),
                   dict(reserved_slots=[dict(id='root', role='implementation')]),
                   dict(live_agents=[]), dict(spawn_available=1)):
        invalid = {**host, **change}
        with pytest.raises(dispatcher.DispatchError):
            dispatcher.host_slots(invalid)


def test_wide_first_does_not_prevent_a_ready_disjoint_pair():
    data = [dict(id='wide', paths=['src']), dict(id='a', paths=['src/a']),
            dict(id='b', paths=['src/b'])]
    original = copy.deepcopy(data)
    ordered = dispatcher.independent_order(data, lambda x: x['paths'])
    assert [x['id'] for x in ordered] == ['a', 'b', 'wide']
    assert data == original
