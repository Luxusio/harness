"""Read-only restart discovery never substitutes for batch completion proof."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugin/scripts'
sys.path.insert(0, str(SCRIPTS))
import batch_pending as pending


def record(repo, *, name='pool', status='blocked'):
    item = dict(slug='leaf', request='SECRET REQUEST MUST NOT BE INJECTED',
                scopes=['src'], resolved_scopes=[str(repo / 'src')],
                depends_on=[], status=status, task_id='TASK__' + ('leaf' if name == 'pool' else name))
    item['slug'] = item['task_id'][6:]
    data = dict(schema_version=1, repo=str(repo), batch_id=name,
                owner_task_id='TASK__parent', max_leads=2,
                destination_ref='refs/heads/master', status='open', halted=False,
                initial_head='a' * 40, created_at='now', updated_at='now',
                cap_source='explicit', requests=[item])
    path = repo / 'doc/harness/runtime/batches' / (name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path, data


def test_pending_is_readonly_no_git_and_no_request_injection(tmp_path, monkeypatch):
    path, _ = record(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no subprocess permitted'))
    result = pending.pending(tmp_path)
    assert result['diagnostics'] == []
    assert result['pools'][0]['requests'][0]['next_operation'] == 'resume'
    assert 'SECRET' not in json.dumps(result)
    assert path.read_bytes() == before
    assert 'TASK__parent' in pending.render_prompt_block(tmp_path)


@pytest.mark.parametrize('corrupt', ['json', 'schema', 'symlink', 'hardlink', 'writable', 'parent'])
def test_corrupt_or_unsafe_metadata_remains_visible(tmp_path, corrupt):
    path, data = record(tmp_path)
    if corrupt == 'json':
        path.write_text('{')
    elif corrupt == 'schema':
        data['schema_version'] = 99
        path.write_text(json.dumps(data))
    elif corrupt in {'symlink', 'hardlink'}:
        other = tmp_path / 'other'
        path.rename(other)
        (path.symlink_to(other) if corrupt == 'symlink' else os.link(other, path))
    elif corrupt == 'writable':
        path.chmod(0o666)
    else:
        path.parent.chmod(0o777)
    result = pending.pending(tmp_path)
    assert result['diagnostics'] == ['pending-worktree-discovery-unavailable']
    assert 'do not infer no pending work' in pending.render_prompt_block(tmp_path)


def test_directory_budget_and_projection_budget(tmp_path):
    record(tmp_path)
    directory = tmp_path / 'doc/harness/runtime/batches'
    for index in range(pending.MAX_DIRECTORY_ENTRIES):
        (directory / f'junk-{index}').touch()
    assert pending.pending(tmp_path)['diagnostics']
    for path in directory.glob('junk-*'):
        path.unlink()
    for index in range(pending.MAX_POOLS + 1):
        record(tmp_path, name=f'pool-{index}')
    result = pending.pending(tmp_path)
    assert result['truncated']
    assert len(result['pools']) == pending.MAX_POOLS
    assert len(pending.render_prompt_block(tmp_path)) <= pending.MAX_PROMPT_CHARS


def test_linked_worktree_metadata_discovers_main_state_without_git(tmp_path, monkeypatch):
    main, work = tmp_path / 'main', tmp_path / 'work'
    main.mkdir()
    work.mkdir()
    metadata = main / '.git/worktrees/worker'
    metadata.mkdir(parents=True)
    (work / '.git').write_text('gitdir: ' + str(metadata))
    (metadata / 'commondir').write_text('../..')
    (metadata / 'gitdir').write_text(str(work / '.git'))
    record(main)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no Git'))
    assert pending.pending(work)['pools'][0]['batch_id'] == 'pool'
    (metadata / 'gitdir').write_text(str(tmp_path / 'foreign/.git'))
    assert pending.pending(work)['diagnostics']


@pytest.mark.parametrize('active', [False, True])
def test_prompt_recovery_survives_context_cap_and_no_active_task(tmp_path, active):
    from test_prompt_memory import _build_scratch_repo, _invoke
    _build_scratch_repo(tmp_path, active_task_id='TASK__ordinary' if active else None)
    record(tmp_path)
    response = _invoke(str(tmp_path))
    assert response.returncode == 0
    assert '[harness-worktree-recovery]' in response.stdout
    assert 'batch=pool owner=TASK__parent' in response.stdout
    assert len(response.stdout) <= 401 + pending.MAX_PROMPT_CHARS
    assert 'SECRET' not in response.stdout


def test_session_recovery_without_active_task(tmp_path):
    from test_prompt_memory import _build_scratch_repo
    _build_scratch_repo(tmp_path)
    record(tmp_path)
    response = subprocess.run([sys.executable, str(SCRIPTS / 'verification_gap_check.py')],
                              cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert response.returncode == 0
    assert 'batch=pool' in response.stdout


def test_missing_safe_state_is_silent_but_unsafe_missing_leaf_is_visible(tmp_path):
    assert pending.pending(tmp_path) == dict(pools=[], diagnostics=[], truncated=False)
    directory = tmp_path / 'doc/harness/runtime'
    directory.mkdir(parents=True)
    directory.chmod(0o777)
    assert pending.pending(tmp_path)['diagnostics']


def test_unbound_creation_intent_remains_discoverable_and_reserves_names(tmp_path):
    path, data = record(tmp_path, status='reserved')
    item = data['requests'][0]
    item['spawn_head'] = 'a' * 40
    item['creation_intent'] = dict(token='f' * 64, spawn_head=item['spawn_head'],
        worktree=str(tmp_path / '.claude/worktrees' / ('harness-' + 'f' * 24)),
        branch='harness/' + 'f' * 64)
    path.write_text(json.dumps(data))
    projected = pending.pending(tmp_path)['pools'][0]['requests'][0]
    assert projected['worktree'] == item['creation_intent']['worktree']
    assert projected['branch'] == item['creation_intent']['branch']
    second_path, second = record(tmp_path, name='second', status='reserved')
    second['requests'][0].update(spawn_head=item['spawn_head'], creation_intent=item['creation_intent'])
    second_path.write_text(json.dumps(second))
    assert pending.pending(tmp_path)['diagnostics']


def test_projection_budget_cannot_silently_hide_pending_work(tmp_path, monkeypatch):
    record(tmp_path)
    monkeypatch.setattr(pending, 'MAX_PROJECTION_BYTES', 10)
    state = pending.pending(tmp_path)
    assert state['pools'] == [] and state['truncated']
    assert 'authoritative MCP inspection required' in pending.render_prompt_block(tmp_path)
