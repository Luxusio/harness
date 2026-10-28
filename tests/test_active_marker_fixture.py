"""Exact scratch focus preserves unrelated live sessions and fails closed."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required to collect this module')

from conftest import scratch_task_in_real_repo

SPEC = importlib.util.spec_from_file_location(
    'active_marker_lib', Path(__file__).resolve().parents[1] / 'plugin/scripts/_lib.py')
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lib)


def marker(repo):
    return repo / 'doc/harness/tasks/.active_sessions/pytest-hook.json'


@pytest.mark.parametrize('raises', [False, True])
def test_fixture_restores_exact_marker_on_success_and_exception(tmp_path, raises):
    path = marker(tmp_path)
    path.parent.mkdir(parents=True)
    prior = b'{"unrelated":"preserved exactly"}\n'
    path.write_bytes(prior)
    foreign = path.with_name('live-native-session.json')
    foreign.write_bytes(b'live session bytes\x00\xff')
    legacy = path.parent.parent / '.active'
    legacy.write_bytes(b'unknown legacy input\n')
    try:
        with scratch_task_in_real_repo('fixture', repo_root=str(tmp_path)) as td:
            binding = lib.resolve_session_task_binding(str(tmp_path), 'pytest-hook')
            assert binding['task_dir'] == td
            assert lib.read_task_control(td)['run_id'] == binding['run_id']
            assert legacy.read_bytes() == b'unknown legacy input\n'
            if raises:
                raise RuntimeError('body failed')
    except RuntimeError:
        assert raises
    assert path.read_bytes() == prior
    assert foreign.read_bytes() == b'live session bytes\x00\xff'
    assert legacy.read_bytes() == b'unknown legacy input\n'
    assert not (path.parent.parent / 'TASK__fixture').exists()
    assert not list(path.parent.glob('*.fixture-backup.*'))


def test_fixture_without_marker_leaves_no_binding(tmp_path):
    with scratch_task_in_real_repo('fixture', repo_root=str(tmp_path)) as td:
        assert lib.resolve_active_task_dir(str(tmp_path), 'pytest-hook') == td
    assert not marker(tmp_path).exists()
    assert not (marker(tmp_path).parent.parent / '.active').exists()


@pytest.mark.parametrize('mutation', ['stale', 'fenced', 'wrong-id', 'closed', 'parked'])
def test_exact_resolution_never_falls_back_to_legacy(tmp_path, mutation):
    with scratch_task_in_real_repo('fixture', repo_root=str(tmp_path)) as td:
        task = Path(td)
        (task.parent / '.active').write_text(td)
        path = marker(tmp_path)
        value = json.loads(path.read_text())
        if mutation == 'stale':
            value['run_id'] = '0198c349-5800-7000-8000-000000000001'
        elif mutation == 'fenced':
            value['conflicts'] = []
        elif mutation == 'wrong-id':
            value['task_id'] = 'TASK__other'
        elif mutation == 'closed':
            control = json.loads((task / 'TASK.json').read_text())
            control['closed'] = True
            (task / 'TASK.json').write_text(json.dumps(control))
        elif mutation == 'parked':
            (task / 'BLOCKED.md').write_text('blocked')
        path.write_text(json.dumps(value))
        assert lib.resolve_active_task_dir(str(tmp_path), 'pytest-hook') == ''
        assert list(lib.iter_active_task_dirs(str(tmp_path))) == []
        assert lib.resolve_active_task_dir(str(tmp_path), 'other-session') == ''
        assert lib.resolve_active_task_dir(str(tmp_path), 'default') == ''
