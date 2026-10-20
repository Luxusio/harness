"""Real Git lifecycle checks: durable ownership, interrupted effects and disposal."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin/scripts'))
import batch_state as batch
import batch_worktrees as managed
import work_pack
from test_batch_state import Pool, request, task, _git, _write, _head, RUN


def setup(tmp_path, monkeypatch, requests=None):
    pool = Pool(tmp_path, monkeypatch)
    spec = work_pack._spec('TASK__owner', requests or [request('a'), request('b')], batch_id='pool')
    return pool, spec


def call(pool, spec, **kwargs):
    return managed.execute(str(pool.repo), spec, **kwargs)


def test_prepare_replay_and_exact_native_bind(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    first = call(pool, spec, available_slots=1)
    handoff, = first['handoffs']
    assert first['reasons'] == {'b': 'capacity'}
    assert Path(handoff['worktree']).is_dir()
    assert Path(handoff['worktree'], '.harness-batch-bootstrap').is_file()
    replay = call(pool, spec, available_slots=0)
    assert replay['handoffs'] == first['handoffs']
    bound = call(pool, spec, operation='bind', slug='a', worker_id='native-actual')
    item = bound['batch']['requests'][0]
    assert item['worktree'] == handoff['worktree'] and item['worker_id'] == 'native-actual'
    assert item['status'] == 'running'


def test_crash_after_git_effect_recovers_from_exact_lock_witness(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    original = managed._call
    def stop(repo, spec, directory, records, command, **kwargs):
        if command == 'bootstrap':
            raise RuntimeError('injected after Git creation before marker')
        return original(repo, spec, directory, records, command, **kwargs)
    monkeypatch.setattr(managed, '_call', stop)
    with pytest.raises(RuntimeError, match='injected'):
        call(pool, spec, available_slots=1)
    intent = json.loads(pool.state.read_text())['requests'][0]['creation_intent']
    assert Path(intent['worktree']).is_dir()
    monkeypatch.setattr(managed, '_call', original)
    handoff, = call(pool, spec, available_slots=0)['handoffs']
    assert handoff['worktree'] == intent['worktree']


@pytest.mark.parametrize('foreign', ['path', 'branch', 'matching-head-worktree'])
def test_intent_without_witness_never_adopts_foreign_resource(tmp_path, monkeypatch, foreign):
    pool, spec = setup(tmp_path, monkeypatch)
    original = managed._save
    def stop(directory, data):
        original(directory, data)
        if data['requests'][0].get('creation_intent'):
            raise RuntimeError('intent only')
    monkeypatch.setattr(managed, '_save', stop)
    with pytest.raises(RuntimeError, match='intent only'):
        call(pool, spec, available_slots=1)
    intent = json.loads(pool.state.read_text())['requests'][0]['creation_intent']
    Path(intent['worktree']).parent.mkdir(parents=True, exist_ok=True)
    if foreign == 'path':
        Path(intent['worktree']).mkdir()
        Path(intent['worktree'], 'precious').write_text('keep')
    elif foreign == 'branch':
        _git('branch', intent['branch'], cwd=pool.repo)
    else:
        _git('worktree', 'add', '-qb', intent['branch'], intent['worktree'], cwd=pool.repo)
    monkeypatch.setattr(managed, '_save', original)
    with pytest.raises((RuntimeError, OSError)):
        call(pool, spec, available_slots=0)
    if foreign == 'path':
        assert Path(intent['worktree'], 'precious').read_text() == 'keep'


def test_lock_busy_has_no_git_effect(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    call(pool, spec, available_slots=0)
    directory = pool.state.parent
    fd = batch.acquire_lock(str(directory))
    before = pool.state.read_bytes()
    try:
        with pytest.raises(batch.Busy):
            call(pool, spec)
    finally:
        os.close(fd)
    assert pool.state.read_bytes() == before
    assert not (pool.repo / '.claude/worktrees').exists()


def finish_result(pool, spec, slug='a'):
    response = call(pool, spec, available_slots=1)
    handoff = next(h for h in response['handoffs'] if h['slug'] == slug)
    call(pool, spec, operation='bind', slug=slug, worker_id='actual-' + slug)
    worktree = Path(handoff['worktree'])
    _write(worktree / (slug + '.txt'), slug + '\n')
    _git('add', slug + '.txt', cwd=worktree)
    _git('commit', '-qm', slug, '--trailer', 'Harness-Task: TASK__' + slug, cwd=worktree)
    task(worktree, slug, closed=True)
    return dict(verdict='closed', task_id='TASK__' + slug, worktree=str(worktree), branch=handoff['branch'],
                commit=_head(worktree), run_id=RUN)


def test_complete_requires_stopped_and_disposes_then_exact_replay(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch, [request('a'), request('b', dependencies=['a'])])
    result = finish_result(pool, spec)
    with pytest.raises(batch.Refusal, match='stopped'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['batch']['requests'][0]['status'] == 'integrated'
    assert not Path(result['worktree']).exists()
    assert not _git('branch', '--list', result['branch'], cwd=pool.repo)
    replay = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert replay['batch']['requests'][0]['status'] == 'integrated'
    with pytest.raises(batch.Refusal, match='replay differs'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result={**result, 'commit':'a'*40}, worker_stopped=True)
    second = finish_result(pool, spec, 'b')
    response = call(pool, spec, operation='complete', slug='b', worker_id='actual-b', result=second, worker_stopped=True)
    assert response['status'] == 'closed'
    assert call(pool, spec, operation='complete', slug='b', worker_id='actual-b', result=second, worker_stopped=True)['status'] == 'closed'


def test_managed_intent_cannot_be_released_or_bound_elsewhere(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    call(pool, spec, available_slots=1)
    denied = pool.cli('release', '--slug', 'a', '--worker-stopped', '--no-external-work', ok=False)
    assert 'unused' in denied['reason']
    denied = pool.cli('bind', '--slug', 'a', '--worker-id', 'w', '--worktree', pool.repo, '--branch', 'master', ok=False)
    assert 'binding changed' in denied['reason']


@pytest.mark.parametrize('stage', ['intent', 'git-effect'])
def test_process_death_retries_exact_creation(tmp_path, monkeypatch, stage):
    import subprocess
    pool, spec = setup(tmp_path, monkeypatch)
    script = '''
import json, os, sys
sys.path.insert(0, sys.argv[1])
import batch_worktrees as m
spec=json.loads(sys.argv[3])
original_save=m._save
original_call=m._call
def save(directory,data):
    original_save(directory,data)
    if sys.argv[4]=='intent' and data['requests'][0].get('creation_intent'):
        os._exit(81)
def call(repo,spec,directory,records,command,**kwargs):
    if sys.argv[4]=='git-effect' and command=='bootstrap':
        os._exit(82)
    return original_call(repo,spec,directory,records,command,**kwargs)
m._save=save
m._call=call
m.execute(sys.argv[2],spec,available_slots=1)
'''
    process = subprocess.run([sys.executable, '-c', script, str(Path(managed.__file__).parent),
                              str(pool.repo), json.dumps(spec), stage], capture_output=True, text=True)
    assert process.returncode == (81 if stage == 'intent' else 82), process.stderr
    data = json.loads(pool.state.read_text())
    intent = data['requests'][0]['creation_intent']
    assert Path(intent['worktree']).exists() == (stage == 'git-effect')
    handoff, = call(pool, spec, available_slots=0)['handoffs']
    assert handoff['worktree'] == intent['worktree']
    assert handoff['branch'] == intent['branch']


def test_dependency_waits_and_readonly_status_preserves_state(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch, [request('a'), request('b', dependencies=['a'])])
    response = call(pool, spec)
    assert response['reasons'] == {'b':'dependencies'}
    before = pool.state.read_bytes()
    response = call(pool, spec, operation='status')
    assert len(response['handoffs']) == 1
    assert pool.state.read_bytes() == before


def test_blocked_completion_exposes_retained_disposition(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    handoff, = call(pool, spec, available_slots=1)['handoffs']
    call(pool, spec, operation='bind', slug='a', worker_id='actual-a')
    result = dict(verdict='blocked', task_id='TASK__a', worktree=handoff['worktree'], branch=handoff['branch'], commit=None)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['status'] == 'blocked'
    assert Path(handoff['worktree']).exists()
    assert 'blocker' in response['next_action']
    assert call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)['status'] == 'blocked'


def test_binding_replay_preserves_actual_worker_identity(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    call(pool, spec, available_slots=1)
    first = call(pool, spec, operation='bind', slug='a', worker_id='actual-a')
    before = pool.state.read_bytes()
    second = call(pool, spec, operation='bind', slug='a', worker_id='actual-a')
    assert first['batch'] == second['batch']
    assert before == pool.state.read_bytes()
    with pytest.raises(batch.Refusal, match='replay worker differs'):
        call(pool, spec, operation='bind', slug='a', worker_id='other-worker')


def test_finish_interruption_after_removal_uses_existing_checkpoint_recovery(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    original = batch.finish_helper.finish
    def interrupt(*args, **kwargs):
        answer = original(*args, **kwargs)
        assert answer['status'] == 'integrated'
        raise RuntimeError('interrupted after disposal before state commit')
    monkeypatch.setattr(batch.finish_helper, 'finish', interrupt)
    with pytest.raises(RuntimeError, match='interrupted after disposal'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert not Path(result['worktree']).exists()
    persisted = json.loads(pool.state.read_text())['requests'][0]
    assert persisted['status'] == 'integrating' and persisted['checkpoint']['stage'] == 'harvested'
    monkeypatch.setattr(batch.finish_helper, 'finish', original)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['batch']['requests'][0]['status'] == 'integrated'


def test_completed_replay_refuses_recreated_owned_branch(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    _git('branch', result['branch'], cwd=pool.repo)
    with pytest.raises(batch.Refusal, match='branch still exists'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert _git('branch', '--list', result['branch'], cwd=pool.repo)


def test_symlink_worktree_ancestor_refused_before_external_creation(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    ignore = pool.repo / '.gitignore'
    ignore.write_text(ignore.read_text() + '.claude\n')
    _git('add', '.gitignore', cwd=pool.repo)
    _git('commit', '-qm', 'ignore managed parent for symlink guard fixture', cwd=pool.repo)
    external = tmp_path / 'outside'
    external.mkdir()
    (pool.repo / '.claude').symlink_to(external, target_is_directory=True)
    with pytest.raises(batch.Refusal, match='unsafe metadata (parent|path)'):
        call(pool, spec, available_slots=1)
    assert list(external.iterdir()) == []


def test_creation_intent_corruption_and_cross_pool_collision_fail_closed(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    call(pool, spec, available_slots=1)
    value = json.loads(pool.state.read_text())
    original = pool.state.read_bytes()
    value['requests'][0]['creation_intent']['branch'] = 'unowned'
    batch.write_json(str(pool.state), value)
    with pytest.raises(batch.Refusal, match='creation intent'):
        call(pool, spec, operation='status')
    pool.state.write_bytes(original)
    duplicate = json.loads(original)
    duplicate['batch_id'] = 'other'
    duplicate['requests'] = [duplicate['requests'][0]]
    duplicate['requests'][0]['slug'] = 'different'
    duplicate['requests'][0]['task_id'] = 'TASK__different'
    batch.write_json(str(pool.state.parent / 'other.json'), duplicate)
    with pytest.raises(batch.Refusal, match='duplicate worktree ownership'):
        call(pool, spec, operation='status')


def test_returned_retry_never_integrates_unreported_commit(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    original = managed._call
    def interrupt(repo, spec, directory, records, command, **kwargs):
        if command == 'finish':
            raise RuntimeError('interrupted after result')
        return original(repo, spec, directory, records, command, **kwargs)
    monkeypatch.setattr(managed, '_call', interrupt)
    with pytest.raises(RuntimeError, match='interrupted after result'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    monkeypatch.setattr(managed, '_call', original)
    worktree = Path(result['worktree'])
    _write(worktree / 'a.txt', 'unreported change\n')
    _git('add', 'a.txt', cwd=worktree)
    _git('commit', '-qm', 'unreported', '--trailer', 'Harness-Task: TASK__a', cwd=worktree)
    before = _head(pool.repo)
    for _ in range(2):
        with pytest.raises(batch.Refusal, match='reported result'):
            call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert _head(pool.repo) == before and worktree.exists()
    assert 'checkpoint' not in json.loads(pool.state.read_text())['requests'][0]


@pytest.mark.parametrize('interrupt_ack', [False, True])
def test_manually_resolved_rebase_requires_exact_explicit_recovery_commit(tmp_path, monkeypatch, interrupt_ack):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    _write(pool.repo / 'a.txt', 'destination conflicting edit\n')
    _git('add', 'a.txt', cwd=pool.repo)
    _git('commit', '-qm', 'destination moved', cwd=pool.repo)
    failed = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert failed['status'] == 'recovery-required'
    worktree = Path(result['worktree'])
    # Managed finish aborts its conflict; the operator explicitly retries and resolves.
    _git('rebase', 'main', cwd=worktree, check=False)
    _write(worktree / 'a.txt', 'reviewed resolved change\n')
    _git('add', 'a.txt', cwd=worktree)
    _git('rebase', '--continue', cwd=worktree, env={'GIT_EDITOR':'true'})
    tip = _head(worktree)
    assert tip != result['commit']
    with pytest.raises(batch.Refusal, match='branch tip changed'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    with pytest.raises(batch.Refusal, match='current branch tip'):
        call(pool, spec, operation='recover', slug='a', worker_stopped=True, recovery_commit=result['commit'])
    if interrupt_ack:
        original = batch.finish_helper.finish
        def interrupt(*args, **kwargs):
            raise RuntimeError('after acknowledged checkpoint before Git')
        monkeypatch.setattr(batch.finish_helper, 'finish', interrupt)
        with pytest.raises(RuntimeError, match='after acknowledged checkpoint'):
            call(pool, spec, operation='recover', slug='a', worker_stopped=True, recovery_commit=tip)
        persisted = json.loads(pool.state.read_text())['requests'][0]['checkpoint']
        assert persisted['recovery_commit'] == tip and persisted['stage'] == 'rebase'
        monkeypatch.setattr(batch.finish_helper, 'finish', original)
        response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    else:
        response = call(pool, spec, operation='recover', slug='a', worker_stopped=True, recovery_commit=tip)
    assert response['batch']['requests'][0]['status'] == 'integrated'
    assert not worktree.exists() and _head(pool.repo) == tip
    assert (pool.repo / 'a.txt').read_text() == 'reviewed resolved change\n'


def test_prepare_preserves_existing_explicit_pool_capacity(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    pool.init(spec['requests'], cap='2', owner=spec['task_id'])
    first = json.loads(pool.state.read_text())
    response = call(pool, spec, available_slots=0)
    assert response['batch']['max_leads'] == 2
    assert response['batch']['cap_source'] == 'explicit'
    assert response['batch']['initial_head'] == first['initial_head']


def test_status_exposes_actual_residual_resources_readonly(tmp_path, monkeypatch):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    second = finish_result(pool, spec, 'b')
    closed = call(pool, spec, operation='complete', slug='b', worker_id='actual-b', result=second, worker_stopped=True)
    assert closed['status'] == 'closed'
    _git('branch', result['branch'], cwd=pool.repo)
    before = pool.state.read_bytes()
    response = call(pool, spec, operation='status')
    assert response['batch']['status'] == 'closed' and response['status'] == 'recovery-required'
    assert 'retained integrated resources' in response['next_action']
    assert 'branch still exists' in response['observed']['a']['identity_error']
    assert 'recover' in response['observed']['a']['next_action']
    assert pool.state.read_bytes() == before


@pytest.mark.parametrize('component', ['.claude', '.claude/worktrees'])
def test_writable_creation_ancestor_refused_before_git_effect(tmp_path, monkeypatch, component):
    pool, spec = setup(tmp_path, monkeypatch)
    ancestor = pool.repo / component
    ancestor.mkdir(parents=True, exist_ok=True)
    ancestor.chmod(0o775)
    before = _git('worktree', 'list', '--porcelain', cwd=pool.repo)
    with pytest.raises(batch.Refusal, match='unsafe metadata path'):
        call(pool, spec, available_slots=1)
    assert _git('worktree', 'list', '--porcelain', cwd=pool.repo) == before
    assert not _git('branch', '--list', 'harness/*', cwd=pool.repo)


def test_branch_only_process_death_has_token_witness_for_safe_retry(tmp_path, monkeypatch):
    import subprocess
    pool, spec = setup(tmp_path, monkeypatch)
    script = '''
import json, os, sys
sys.path.insert(0,sys.argv[1])
import batch_worktrees as m
original=m._create_branch
def create(repo,intent):
    original(repo,intent)
    os._exit(83)
m._create_branch=create
m.execute(sys.argv[2],json.loads(sys.argv[3]),available_slots=1)
'''
    process = subprocess.run([sys.executable, '-c', script, str(Path(managed.__file__).parent),
                              str(pool.repo), json.dumps(spec)], capture_output=True, text=True)
    assert process.returncode == 83, process.stderr
    intent = json.loads(pool.state.read_text())['requests'][0]['creation_intent']
    assert not Path(intent['worktree']).exists()
    assert _git('rev-parse', intent['branch'], cwd=pool.repo) == intent['spawn_head']
    handoff, = call(pool, spec, available_slots=0)['handoffs']
    assert handoff['worktree'] == intent['worktree'] and Path(handoff['worktree']).exists()


@pytest.mark.parametrize('interrupt_started', [False, True])
def test_abbreviated_result_commit_integrates_and_replays_exact_original(tmp_path, monkeypatch, interrupt_started):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    full_commit = result['commit']
    result['commit'] = full_commit[:12]
    if interrupt_started:
        original = batch.finish_helper.finish
        def interrupt(*args, **kwargs):
            raise RuntimeError('after initial checkpoint before Git')
        monkeypatch.setattr(batch.finish_helper, 'finish', interrupt)
        with pytest.raises(RuntimeError, match='after initial checkpoint'):
            call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
        persisted = json.loads(pool.state.read_text())['requests'][0]
        assert persisted['result'] == result
        assert persisted['checkpoint']['stage'] == 'started'
        assert persisted['checkpoint']['branch_tip'] == full_commit
        monkeypatch.setattr(batch.finish_helper, 'finish', original)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['batch']['requests'][0]['status'] == 'integrated'
    assert response['batch']['requests'][0]['result'] == result
    assert not Path(result['worktree']).exists()
    replay = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert replay['batch']['requests'][0]['result'] == result
    assert replay['batch']['requests'][0]['status'] == 'integrated'
    with pytest.raises(batch.Refusal, match='replay differs'):
        call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result={**result, 'commit': full_commit}, worker_stopped=True)


def ignore_disposal_fixture(pool):
    ignore = pool.repo / '.gitignore'
    ignore.write_text(ignore.read_text() + '*.local\n.venv/\nnode_modules/\n__pycache__/\n.pytest_cache\n.mypy_cache/\n.ruff_cache/\n.cache/\n')
    _git('add', '.gitignore', cwd=pool.repo)
    _git('commit', '-qm', 'ignore disposal fixture data', cwd=pool.repo)


@pytest.mark.parametrize('kind', ['precious', 'other-task', 'bad-learning', 'invalid-marker', 'cache-file', 'unknown-cache', 'nested-cache-repo'])
def test_ordinary_complete_retains_unknown_ignored_bytes_and_original_lock(tmp_path, monkeypatch, kind):
    pool, spec = setup(tmp_path, monkeypatch)
    ignore_disposal_fixture(pool)
    result = finish_result(pool, spec)
    worktree = Path(result['worktree'])
    rel = {'precious':'precious.local', 'other-task':'doc/harness/tasks/TASK__other/notes.md',
           'bad-learning':'doc/harness/learnings.jsonl', 'invalid-marker':'doc/harness/tasks/.active_sessions/default.json',
           'cache-file':'.pytest_cache', 'unknown-cache':'.cache/precious.txt',
           'nested-cache-repo':'.venv/nested/.git/config'}[kind]
    precious = worktree / rel
    _write(precious, 'unique ignored evidence\n')
    marker = worktree / '.harness-batch-bootstrap'
    original_marker = marker.read_bytes()
    lock_path = Path(_git('rev-parse', '--absolute-git-dir', cwd=worktree)) / 'locked'
    original_lock = lock_path.read_bytes()
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['status'] == 'kept'
    assert precious.read_text() == 'unique ignored evidence\n'
    assert marker.read_bytes() == original_marker and lock_path.read_bytes() == original_lock
    assert _git('branch', '--list', result['branch'], cwd=pool.repo)
    assert response['operation_result']['cleanup']['unlocked'] is False


def test_post_merge_hook_ignored_bytes_are_inventoried_before_unlock(tmp_path, monkeypatch):
    import shlex
    pool, spec = setup(tmp_path, monkeypatch)
    ignore_disposal_fixture(pool)
    result = finish_result(pool, spec)
    late = Path(result['worktree']) / 'late.local'
    hook = pool.repo / '.git/hooks/post-merge'
    hook.write_text('#!/bin/sh\nprintf "late unique evidence\\n" > ' + shlex.quote(str(late)) + '\n')
    hook.chmod(0o755)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['status'] == 'kept'
    assert late.read_text() == 'late unique evidence\n'
    assert response['operation_result']['integrated_tip'] == _head(pool.repo)
    assert response['operation_result']['cleanup']['unlocked'] is False
    assert (late.parent / '.harness-batch-bootstrap').is_file()


@pytest.mark.parametrize('mutate', ['task', 'archive'])
def test_evidence_changed_after_harvest_prevents_ordinary_removal(tmp_path, monkeypatch, mutate):
    pool, spec = setup(tmp_path, monkeypatch)
    result = finish_result(pool, spec)
    original = batch.finish_helper._cleanup
    def late_change(repo, worktree, branch, outcome, *args, **kwargs):
        directory = (Path(worktree) / 'doc/harness/tasks/TASK__a' if mutate == 'task'
                     else Path(repo) / 'doc/harness/archive/batch/TASK__a')
        _write(directory / 'late-evidence.txt', 'late unique bytes\n')
        return original(repo, worktree, branch, outcome, *args, **kwargs)
    monkeypatch.setattr(batch.finish_helper, '_cleanup', late_change)
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['status'] == 'kept'
    assert Path(result['worktree']).exists()
    assert 'archive' in response['operation_result']['reason']
    assert response['operation_result']['cleanup']['unlocked'] is False


@pytest.mark.parametrize('markers', ['empty-directory', 'exact-marker'])
def test_reproducible_cache_directories_and_exact_closed_metadata_allow_disposal(tmp_path, monkeypatch, markers):
    pool, spec = setup(tmp_path, monkeypatch)
    ignore_disposal_fixture(pool)
    result = finish_result(pool, spec)
    worktree = Path(result['worktree'])
    for name in ('.venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'):
        _write(worktree / name / 'generated-cache.bin', 'reproducible content\n')
    sessions = worktree / 'doc/harness/tasks/.active_sessions'
    sessions.mkdir()
    _write(worktree / 'doc/harness/tasks/.active.fixture.lock', '')
    if markers == 'exact-marker':
        _write(sessions / 'default.json', json.dumps(dict(session_id='default', task_dir=str(worktree / 'doc/harness/tasks/TASK__a'),
                    task_id='TASK__a', run_id=RUN, updated='2026-10-20T00:00:00+00:00')))
        _write(sessions / '.session-hint', 'default\n')
    response = call(pool, spec, operation='complete', slug='a', worker_id='actual-a', result=result, worker_stopped=True)
    assert response['batch']['requests'][0]['status'] == 'integrated', response
    assert not worktree.exists()
