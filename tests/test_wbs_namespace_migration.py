"""Explicit namespace conversion preserves runs and real evidence bytes."""

from test_symlink_fixture import create_symlink
import hashlib
import json
import os
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required to collect this module') from error

from conftest import SCRIPTS_DIR


def fixtures(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import _lib
    import wbs_namespace_migration as migration
    root = tmp_path / 'repo'
    root.mkdir(mode=0o700)
    old = root / 'doc/harness/tasks/TASK__example'
    old.mkdir(parents=True, mode=0o700)
    control = dict(run_id=_lib.new_uuid7(), execution_mode='standard', required_lenses=['review-code', 'qa-cli'], closed=False)
    (old / 'TASK.json').write_text(json.dumps(control))
    state = dict(version=1, run_id=control['run_id'], nodes=[dict(id='A', title='Observable outcome', weight=1)], checks={'kept': {'passed': False, 'result': {'returncode': 1, 'error': None, 'output': 'not a new PASS'}}}, started_at=1, last_progress_at=1)
    (old / 'WBS.json').write_text(json.dumps(state))
    # White-box filesystem scenarios exercise the pinned migration body; the
    # public owner's refusal is covered separately, without claiming role PASS.
    apply = lambda: migration.apply.__wrapped__(root)
    return root, old, control, state, migration, apply


def test_direct_import_preserves_run_checks_opaque_bytes_and_exact_binding(tmp_path, monkeypatch):
    root, old, control, state, migration, apply = fixtures(tmp_path, monkeypatch)
    raw = b'old TASK__example arbitrary evidence\x00\xff'
    (old / 'evidence.bin').write_bytes(raw)
    marker = root / 'doc/harness/tasks/.active_sessions/native-session.json'
    marker.parent.mkdir()
    binding = dict(session_id='native-session', task_id=old.name, task_dir=str(old), run_id=control['run_id'], updated='2026-10-30T00:00:00Z')
    marker.write_text(json.dumps(binding))
    before = (old / 'WBS.json').read_bytes()
    preview = migration.preview(root)
    assert preview['status'] == 'prepared' and (old / 'TASK.json').exists()
    result = apply()
    new = root / 'doc/harness/wbs/WBS__example'
    assert result['status'] == 'complete'
    assert json.loads((new / 'WBS.control.json').read_text()) == control
    assert (new / 'WBS.json').read_bytes() == before
    assert (new / 'evidence.bin').read_bytes() == raw == (old / 'evidence.bin').read_bytes()
    assert not (old / 'TASK.json').exists() and not marker.exists()
    actual = json.loads((root / 'doc/harness/wbs/.active_sessions/native-session.json').read_text())
    assert actual == dict(session_id='native-session', wbs_id=new.name, wbs_dir=str(new), run_id=control['run_id'], updated=binding['updated'])
    migration.require_complete(root)
    assert apply() == result


def test_namespace_reads_never_migrate(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='preview/apply'):
        migration.require_complete(root)
    assert (old / 'TASK.json').exists() and not (root / migration.ARCHIVE).exists()


def test_public_apply_requires_canonical_mcp_owner(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='MCP owner'):
        migration.apply(root)
    assert (old / 'TASK.json').exists()


@pytest.mark.parametrize('kind', ['symlink', 'collision', 'invalid-control'])
def test_unsafe_input_refuses_before_publication(tmp_path, monkeypatch, kind):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    if kind == 'symlink':
        create_symlink(old / 'trap', '/etc/passwd')
    elif kind == 'collision':
        new = root / 'doc/harness/wbs/WBS__example'
        new.mkdir(parents=True)
        (new / 'user.txt').write_text('retain')
    else:
        (old / 'TASK.json').write_text('{"run_id":"invalid"}')
    with pytest.raises((ValueError, OSError)):
        apply()
    assert (old / 'TASK.json').exists()
    assert not (root / migration.MANIFEST).exists()


def test_snapshot_modes_and_large_payload_are_preserved(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    view = old / 'work/source'
    view.mkdir(parents=True)
    payload = view / 'artifact'
    # Larger than the migration helper's ordinary bounded JSON reads.
    data = b'x' * (17 * 1024 * 1024)
    payload.write_bytes(data)
    payload.chmod(0o444)
    view.chmod(0o555)
    apply()
    target = root / 'doc/harness/wbs/WBS__example/work/source/artifact'
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(data).digest()
    assert target.stat().st_mode & 0o777 == 0o444
    assert target.parent.stat().st_mode & 0o777 == 0o555


def test_interrupted_copy_resumes_same_run_without_overwriting_drift(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    original = migration._copy
    count = 0
    def interrupt(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('injected interruption')
        return original(*args, **kwargs)
    monkeypatch.setattr(migration, '_copy', interrupt)
    with pytest.raises((ValueError, OSError), match='unsafe storage path|injected interruption'):
        apply()
    assert count == 2, 'the copy interruption must reach the selected backend'
    assert (old / 'TASK.json').exists()
    with pytest.raises(ValueError, match='interrupted migration'):
        migration.require_complete(root)
    monkeypatch.setattr(migration, '_copy', original)
    assert apply()['status'] == 'complete'
    new = root / 'doc/harness/wbs/WBS__example'
    assert json.loads((new / 'WBS.control.json').read_text())['run_id'] == control['run_id']


def test_manifest_budget_refuses_before_copy(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(migration, 'MAX_MANIFEST', 100)
    with pytest.raises(ValueError, match='manifest budget'):
        apply()
    assert (old / 'TASK.json').exists()
    assert not (root / 'doc/harness/wbs/WBS__example').exists()


def test_retired_focus_is_archived_without_becoming_session_authority(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    focus = root / 'doc/harness/tasks/.active'
    focus.write_bytes(b'opaque TASK__example\x00\xff')
    default = root / 'doc/harness/tasks/.active_sessions/default.json'
    default.parent.mkdir()
    default.write_text(json.dumps(dict(session_id='default', task_id=old.name)))
    expected = default.read_bytes()
    apply()
    assert not focus.exists() and not default.exists()
    assert not (root / 'doc/harness/wbs/.active_sessions/default.json').exists()
    assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/.active').read_bytes() == b'opaque TASK__example\x00\xff'
    assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/.active_sessions/default.json').read_bytes() == expected


def test_detached_closed_history_is_not_copied_or_reactivated(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    control['closed'] = True
    (old / 'TASK.json').write_text(json.dumps(control))
    before = (old / 'TASK.json').read_bytes()
    assert apply()['retained_historical_roots'] == [str(old)]
    assert not (old / 'TASK.json').exists()
    assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/TASK__example/TASK.json').read_bytes() == before
    assert not (root / 'doc/harness/wbs/WBS__example').exists()
    migration.require_complete(root)


def test_old_receipt_control_converts_without_new_run(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    legacy = dict(control)
    legacy.pop('closed')
    legacy['close_receipt_fingerprint'] = None
    (old / 'TASK.json').write_text(json.dumps(legacy))
    apply()
    actual = json.loads((root / 'doc/harness/wbs/WBS__example/WBS.control.json').read_text())
    assert actual == control


def test_stopped_pool_preserves_batch_identity_and_verified_archive_custody(tmp_path, monkeypatch):
    root, old, control, state, migration, apply = fixtures(tmp_path, monkeypatch)
    import batch_state
    archive = root / 'doc/harness/archive/batch/TASK__lead'
    archive.mkdir(parents=True)
    closed = dict(control, closed=True)
    (archive / 'TASK.json').write_text(json.dumps(closed))
    (archive / 'WBS.json').write_text(json.dumps(state))
    (archive / 'review.bin').write_bytes(b'actual historical TASK__lead\x00\xff')
    request = dict(slug='lead', request='Implement observable result', scopes=['src'], depends_on=[],
                   resolved_scopes=[str(root / 'src')], task_id='TASK__lead', status='integrated',
                   worker_id='actual-worker', worktree=str(root / 'removed-worker'), branch='lead',
                   spawn_head='a' * 40, run_id=control['run_id'], closed=True)
    request['result'] = dict(verdict='closed', task_id='TASK__lead', worktree=request['worktree'], branch='lead', commit='a' * 40)
    request['checkpoint'] = dict(stage='harvested', destination_ref='refs/heads/main', run_id=control['run_id'],
                                 closed=True, branch_tip='a' * 40, integrated_tip='a' * 40,
                                 harvest=dict(archived=str(archive)), archive_fingerprint=batch_state.fingerprint(str(archive)))
    pool = dict(schema_version=1, repo=str(root), batch_id='original-pool', owner_task_id=old.name,
                max_leads=2, destination_ref='refs/heads/main', status='closed', halted=False,
                initial_head='a' * 40, created_at='then', updated_at='then', cap_source='user', requests=[request])
    pool_path = root / 'doc/harness/runtime/batches/original-pool.json'
    pool_path.parent.mkdir(parents=True)
    pool_path.write_text(json.dumps(pool))
    apply()
    actual = json.loads(pool_path.read_text())
    assert actual['batch_id'] == 'original-pool' and actual['owner_wbs_id'] == 'WBS__example'
    assert actual['requests'][0]['wbs_id'] == 'WBS__lead'
    new_archive = root / 'doc/harness/archive/batch/WBS__lead'
    assert actual['requests'][0]['checkpoint']['archive_fingerprint'] == batch_state.fingerprint(str(new_archive))
    assert actual['requests'][0]['checkpoint']['harvest']['archived'] == str(new_archive)
    assert (new_archive / 'review.bin').read_bytes() == (archive / 'review.bin').read_bytes()
    assert json.loads((new_archive / 'WBS.control.json').read_text()) == closed


def test_running_pool_refuses_before_touching_control(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    path = root / 'doc/harness/runtime/batches/active.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(owner_task_id=old.name, requests=[dict(task_id='TASK__lead', status='running')])))
    with pytest.raises(ValueError, match='stop and return'):
        apply()
    assert (old / 'TASK.json').exists() and not (root / migration.MANIFEST).exists()


def test_conflict_fence_and_run_identity_survive_namespace_change(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    other = old.with_name('TASK__other')
    other.mkdir()
    (other / 'TASK.json').write_text(json.dumps(control))
    marker = root / 'doc/harness/tasks/.active_sessions/exact-session.json'
    marker.parent.mkdir()
    conflicts = [dict(task_id=path.name, task_dir=str(path), run_id=control['run_id']) for path in (old, other)]
    marker.write_text(json.dumps(dict(session_id='exact-session', conflicts=conflicts, binding_conflict=True)))
    apply()
    actual = json.loads((root / 'doc/harness/wbs/.active_sessions/exact-session.json').read_text())
    assert actual['binding_conflict'] is True
    assert actual['conflicts'] == [dict(wbs_id='WBS__' + path.name[6:], wbs_dir=str(root / 'doc/harness/wbs' / ('WBS__' + path.name[6:])), run_id=control['run_id']) for path in (old, other)]


def test_prepared_manifest_cannot_invent_different_control_or_pass(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    original = migration._copy
    monkeypatch.setattr(migration, '_copy', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError, match='interrupted'):
        apply()
    path = root / migration.MANIFEST
    journal = json.loads(path.read_text())
    import _lib
    journal['trees'][0]['replacement']['WBS.control.json'] = migration._encoded(dict(control, run_id=_lib.new_uuid7(), closed=True))
    path.write_text(json.dumps(journal))
    monkeypatch.setattr(migration, '_copy', original)
    with pytest.raises(ValueError, match='unrelated authority'):
        apply()
    assert json.loads((old / 'TASK.json').read_text()) == control


def test_only_root_wbs_control_is_transformed_not_nested_evidence(tmp_path, monkeypatch):
    root, old, _, state, migration, apply = fixtures(tmp_path, monkeypatch)
    state['batch'] = dict(task_id='TASK__example', batch_id='stable-pool', requests=[])
    (old / 'WBS.json').write_text(json.dumps(state))
    nested = old / 'work/original-evidence/WBS.json'
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b'arbitrary historical TASK__example\x00\xff')
    apply()
    new = root / 'doc/harness/wbs/WBS__example'
    actual = json.loads((new / 'WBS.json').read_text())
    assert actual['batch'] == dict(wbs_id='WBS__example', batch_id='stable-pool', requests=[])
    assert actual['checks'] == state['checks']
    assert (new / 'work/original-evidence/WBS.json').read_bytes() == nested.read_bytes()


def test_stopped_returned_lead_migrates_registered_worktree_and_can_resume_pool(tmp_path, monkeypatch):
    root, old, control, state, migration, apply = fixtures(tmp_path, monkeypatch)
    import subprocess
    import _lib
    import batch_state
    def git(repo, *args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git(root, 'init', '-q')
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    (root / '.gitignore').write_text('doc/harness/tasks/\ndoc/harness/archive/\ndoc/harness/runtime/\n')
    (root / 'src.txt').write_text('product')
    git(root, 'add', 'src.txt', 'doc/harness/manifest.yaml', '.gitignore')
    git(root, '-c', 'user.name=Migration test', '-c', 'user.email=migration@example.invalid', 'commit', '-qm', 'baseline')
    worker = tmp_path / 'worker'
    git(root, 'worktree', 'add', '-qb', 'lead', str(worker))
    child = worker / 'doc/harness/tasks/TASK__lead'
    child.mkdir(parents=True)
    child_control = dict(control, run_id=_lib.new_uuid7(), closed=True)
    (child / 'TASK.json').write_text(json.dumps(child_control))
    (child / 'WBS.json').write_text(json.dumps(dict(state, run_id=child_control['run_id'])))
    (child / 'review.bin').write_bytes(b'actual independent historical response\x00\xff')
    head = git(root, 'rev-parse', 'HEAD')
    request = dict(slug='lead', request='Implement observable result', scopes=['src.txt'], depends_on=[],
                   resolved_scopes=[str(root / 'src.txt')], task_id='TASK__lead', status='returned',
                   worker_id='native-worker', worktree=str(worker), branch='lead', spawn_head=head,
                   run_id=child_control['run_id'], closed=True)
    request['result'] = dict(verdict='closed', task_id='TASK__lead', worktree=str(worker), branch='lead', commit=head)
    pool = dict(schema_version=1, repo=str(root), batch_id='stopped-pool', owner_task_id=old.name,
                max_leads=2, destination_ref='refs/heads/master', status='open', halted=False,
                initial_head=head, created_at='then', updated_at='then', cap_source='user', requests=[request])
    path = root / 'doc/harness/runtime/batches/stopped-pool.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pool))
    tracked_ignore = (root / '.gitignore').read_bytes()
    apply()
    assert (root / '.gitignore').read_bytes() == tracked_ignore == (worker / '.gitignore').read_bytes()
    assert git(root, 'status', '--porcelain') == ''
    assert git(worker, 'status', '--porcelain') == ''
    assert git(worker, 'check-ignore', 'doc/harness/wbs/WBS__lead/WBS.control.json') == 'doc/harness/wbs/WBS__lead/WBS.control.json'
    actual = batch_state.read_json(str(path))
    batch_state.validate(actual, str(root), 'stopped-pool')
    item = actual['requests'][0]
    assert item['status'] == 'returned' and item['worker_id'] == 'native-worker'
    assert item['worktree'] == str(worker) and item['run_id'] == child_control['run_id']
    assert batch_state.control(item, closed=True) == child_control
    assert (worker / 'doc/harness/wbs/WBS__lead/review.bin').read_bytes() == b'actual independent historical response\x00\xff'
    migration.require_complete(root)
    migration.require_complete(worker)
    assert (root / 'src.txt').read_text() == 'product' and git(root, 'rev-parse', 'HEAD') == head


def test_already_new_namespace_is_noop_for_downstream_schema_importer(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import wbs_namespace_migration as migration
    root = tmp_path / 'repo'
    new = root / 'doc/harness/wbs/WBS__new'
    new.mkdir(parents=True)
    # Namespace import owns no interpretation or repair of new-namespace data.
    raw = b'{"close_receipt_fingerprint":null,"invalid":"handled downstream"}'
    (new / 'WBS.control.json').write_bytes(raw)
    assert migration.preview(root)['status'] == 'complete'
    assert migration.apply.__wrapped__(root)['status'] == 'complete'
    assert (new / 'WBS.control.json').read_bytes() == raw
    assert not (root / migration.ARCHIVE).exists()


def test_public_migrate_uses_real_owner_and_leaves_same_root_resumable(tmp_path, monkeypatch):
    root, old, control, _, migration, _ = fixtures(tmp_path, monkeypatch)
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    result = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert not result.get('isError'), result
    new = root / 'doc/harness/wbs/WBS__example'
    assert json.loads((new / 'WBS.control.json').read_text())['run_id'] == control['run_id']
    result = server._call_in_repo(str(root), 'wbs', dict(action='open', wbs_id='WBS__example'))
    assert not result.get('isError'), result
    assert result['structuredContent']['run_id'] == control['run_id']


def test_registered_direct_worktree_without_pool_is_included(tmp_path, monkeypatch):
    root, old, control, state, migration, apply = fixtures(tmp_path, monkeypatch)
    import subprocess
    import _lib
    def git(repo, *args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git(root, 'init', '-q')
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    (root / '.gitignore').write_text('doc/harness/tasks/\n')
    git(root, 'add', '.gitignore', 'doc/harness/manifest.yaml')
    git(root, '-c', 'user.name=Migration test', '-c', 'user.email=migration@example.invalid', 'commit', '-qm', 'baseline')
    worker = tmp_path / 'manual'
    git(root, 'worktree', 'add', '-qb', 'manual', str(worker))
    child = worker / 'doc/harness/tasks/TASK__direct'
    child.mkdir(parents=True)
    own_control = dict(control, run_id=_lib.new_uuid7())
    (child / 'TASK.json').write_text(json.dumps(own_control))
    (child / 'WBS.json').write_text(json.dumps(dict(state, run_id=own_control['run_id'])))
    before = git(root, 'rev-parse', 'HEAD')
    apply()
    actual = json.loads((worker / 'doc/harness/wbs/WBS__direct/WBS.control.json').read_text())
    assert actual == own_control and git(worker, 'rev-parse', 'HEAD') == before
    assert git(worker, 'status', '--porcelain') == ''
    assert not (child / 'TASK.json').exists()
    migration.require_complete(worker)


def test_unavailable_worktree_discovery_never_counts_as_empty(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    import codex_hook_registration
    monkeypatch.setattr(codex_hook_registration, 'workspace_roots', lambda _: [])
    with pytest.raises(ValueError, match='discovery unavailable'):
        apply()
    assert (old / 'TASK.json').exists() and not (root / migration.MANIFEST).exists()


def test_absent_registry_and_effective_ignores_do_not_touch_unused_unsafe_git_metadata(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    import subprocess
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    (root / '.gitignore').write_text('/doc/harness/tasks/\n/doc/harness/wbs/\n/doc/harness/archive/\n')
    (root / '.git').chmod(0o777)
    assert not (root / '.git/worktrees').exists()
    actual_read = migration.storage._read
    def no_git_storage(repo, relative, *args, **kwargs):
        assert not relative.startswith('.git/'), relative
        return actual_read(repo, relative, *args, **kwargs)
    monkeypatch.setattr(migration.storage, '_read', no_git_storage)
    assert migration.preview(root)['status'] == 'prepared'
    assert apply()['status'] == 'complete'
    assert (root / '.git').stat().st_mode & 0o777 == 0o777


def test_required_unsafe_local_ignore_reports_exact_path_and_preserves_control(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    import subprocess
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    directory = root / '.git'
    if os.name == 'nt':
        from test_wiki_windows_native import _acl
        _acl(directory, '(A;;GA;;;WD)')
    else:
        directory.chmod(0o777)
    before = (old / 'TASK.json').read_bytes()
    try:
        with pytest.raises(ValueError, match='unsafe Git-local ignore path') as error:
            apply()
        assert str(root / '.git/info/exclude') in str(error.value)
        assert (old / 'TASK.json').read_bytes() == before and not (root / migration.MANIFEST).exists()
    finally:
        if os.name == 'nt':
            _acl(directory, '')
        else:
            directory.chmod(0o700)


def test_hint_temp_and_stale_closed_markers_are_history_not_live_references(tmp_path, monkeypatch):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    control['closed'] = True
    (old / 'TASK.json').write_text(json.dumps(control))
    directory = root / 'doc/harness/tasks/.active_sessions'
    directory.mkdir()
    payloads = {'.session-hint': b'old-session\n', '.text.abc.tmp': b'partial json {', '.b1_any.tmp': b'\x00\xff'}
    payloads['old-session.json'] = json.dumps(dict(session_id='old-session', task_id=old.name, task_dir=str(old), run_id=control['run_id'])).encode()
    for name, raw in payloads.items():
        (directory / name).write_bytes(raw)
    assert migration.preview(root)['retained_historical_roots'] == [str(old)]
    apply()
    assert not (old / 'TASK.json').exists() and not (root / 'doc/harness/wbs/WBS__example').exists()
    assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/TASK__example/TASK.json').exists()
    for name, raw in payloads.items():
        assert not (directory / name).exists()
        assert not (root / 'doc/harness/wbs/.active_sessions' / name).exists()
        assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/.active_sessions' / name).read_bytes() == raw


def test_unresolved_overflow_fence_remains_a_fence(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    directory = root / 'doc/harness/tasks/.active_sessions'
    directory.mkdir()
    fence = dict(session_id='exact', conflicts=[], conflicts_overflow=True)
    (directory / 'exact.json').write_text(json.dumps(fence))
    apply()
    actual = json.loads((root / 'doc/harness/wbs/.active_sessions/exact.json').read_text())
    assert actual == fence


def test_public_fresh_open_repairs_old_ignore_without_legacy_controls_or_source_edit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import subprocess
    root = tmp_path / 'repo'
    (root / 'doc/harness').mkdir(parents=True)
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    (root / '.gitignore').write_text('/doc/harness/tasks/\n')
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('add', '.gitignore', 'doc/harness/manifest.yaml')
    git('-c', 'user.name=Scaffold test', '-c', 'user.email=scaffold@example.invalid', 'commit', '-qm', 'old setup')
    before = (root / '.gitignore').read_bytes()
    head = git('rev-parse', 'HEAD')
    from test_harness_mcp_server import HarnessMcpServerTests
    result = HarnessMcpServerTests()._call_in_repo(str(root), 'wbs', dict(action='open', wbs_id='WBS__new'))
    assert not result.get('isError'), result
    assert (root / 'doc/harness/wbs/WBS__new/WBS.control.json').exists()
    assert (root / '.gitignore').read_bytes() == before and git('rev-parse', 'HEAD') == head
    assert git('status', '--porcelain') == ''
    assert not (root / 'doc/harness/archive/wbs-namespace-migration').exists()


def test_payload_siblings_share_one_pinned_directory_chain(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    directory = old / 'work/deep/siblings'
    directory.mkdir(parents=True)
    for i in range(80):
        (directory / f'{i:03}.txt').write_text(str(i))
    if os.name == 'nt':
        import _wiki_windows as native
        actual = native._open
        opened = []
        def recording(path, access, **kwargs):
            if kwargs.get('directory'):
                opened.append(Path(path))
            return actual(path, access, **kwargs)
        monkeypatch.setattr(native, '_open', recording)
        result = migration._tree(root, 'doc/harness/tasks/TASK__example')
        assert len(result) == 85
        assert opened.count(directory) == 1
        assert len(opened) == len(set(opened)), 'each native directory is retained once'
        assert result['work/deep/siblings/079.txt']['sha256'] == hashlib.sha256(b'79').hexdigest()
        return
    actual = migration.os.open
    opened = []
    def recording(path, flags, *args, **kwargs):
        if flags & migration.os.O_DIRECTORY:
            opened.append(path)
        return actual(path, flags, *args, **kwargs)
    monkeypatch.setattr(migration.os, 'open', recording)
    result = migration._tree(root, 'doc/harness/tasks/TASK__example')
    assert len(result) == 85  # 80 siblings + three dirs + two control/state files.
    assert len(opened) == 8  # repo, doc, harness, tasks, execution, work, deep, siblings.


def test_pinned_payload_directory_rebinding_is_rejected(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    directory = old / 'work/deep'
    directory.mkdir(parents=True)
    (directory / 'a.txt').write_text('original')
    if os.name == 'nt':
        import _wiki_windows as native
        actual = native._ReadFile
        target = native.metadata_probe(directory / 'a.txt')['identity']
        attempts = []
        def replacing(*args):
            result = actual(*args)
            if result and native._identity(args[0]) == target and not attempts:
                with pytest.raises(PermissionError) as refused:
                    directory.rename(old / 'work/retained-original')
                assert refused.value.winerror == 32
                attempts.append('blocked')
            return result
        monkeypatch.setattr(native, '_ReadFile', replacing)
        result = migration._tree(root, 'doc/harness/tasks/TASK__example')
        assert attempts == ['blocked']
        assert result['work/deep/a.txt']['sha256'] == hashlib.sha256(b'original').hexdigest()
        assert (directory / 'a.txt').read_bytes() == b'original'
        assert not (old / 'work/retained-original').exists()
        return
    actual = migration._hash_at
    changed = False
    def replacing(parent, name, relative, **kwargs):
        nonlocal changed
        result = actual(parent, name, relative, **kwargs)
        if relative.endswith('/a.txt') and not changed:
            changed = True
            directory.rename(old / 'work/retained-original')
            directory.mkdir()
            (directory / 'a.txt').write_text('replacement')
        return result
    monkeypatch.setattr(migration, '_hash_at', replacing)
    with pytest.raises(ValueError, match='rebound|directory changed'):
        migration._tree(root, 'doc/harness/tasks/TASK__example')
    assert changed


def test_referenced_pack_only_root_migrates_intent_without_manufacturing_run(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    (old / 'TASK.json').unlink()
    requests = [dict(slug=name, request='Deliver ' + name, scopes=[name], depends_on=[]) for name in ('one', 'two')]
    state = dict(version=1, run_id='', nodes=[], checks={}, started_at=0, last_progress_at=0, pack_only=True,
                 batch=dict(task_id=old.name, batch_id='kept-pool', requests=requests))
    (old / 'WBS.json').write_text(json.dumps(state))
    pool_requests = [dict(item, task_id='TASK__' + item['slug'], status='queued', resolved_scopes=[str(root / item['slug'])]) for item in requests]
    pool = dict(schema_version=1, repo=str(root), batch_id='kept-pool', owner_task_id=old.name,
                max_leads=2, destination_ref='refs/heads/main', status='open', halted=False,
                initial_head='a' * 40, created_at='then', updated_at='then', cap_source='user', requests=pool_requests)
    path = root / 'doc/harness/runtime/batches/kept-pool.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pool))
    apply()
    new = root / 'doc/harness/wbs/WBS__example'
    assert not (new / 'WBS.control.json').exists()
    actual = json.loads((new / 'WBS.json').read_text())
    assert actual['run_id'] == '' and actual['checks'] == {} and actual['pack_only'] is True
    assert actual['batch']['wbs_id'] == 'WBS__example' and actual['batch']['batch_id'] == 'kept-pool'
    assert json.loads(path.read_text())['owner_wbs_id'] == 'WBS__example'
    assert json.loads((old / 'WBS.json').read_text()) == state


def new_only_linked_workspace(root, tmp_path):
    import subprocess
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    git('add', 'doc/harness/manifest.yaml')
    git('-c', 'user.name=Barrier test', '-c', 'user.email=barrier@example.invalid', 'commit', '-qm', 'baseline')
    worker = tmp_path / 'new-only-worker'
    git('worktree', 'add', '-qb', 'new-only', str(worker))
    (worker / 'doc/harness/wbs/WBS__new').mkdir(parents=True)
    assert not (worker / 'doc/harness/tasks').exists()
    return worker


def test_new_only_linked_workspace_cannot_bypass_common_prepared_barrier(tmp_path, monkeypatch):
    root, _, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    worker = new_only_linked_workspace(root, tmp_path)
    manifest = root / migration.MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(dict(version=1, status='prepared', trees=[], changes=[], retained=[])))
    with pytest.raises(ValueError, match='interrupted migration'):
        migration.require_complete(worker)


def test_new_only_linked_workspace_does_not_validate_unused_unsafe_common_root(tmp_path, monkeypatch):
    root, _, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    worker = new_only_linked_workspace(root, tmp_path)
    root.chmod(0o777)
    (root / '.git').chmod(0o777)
    actual = migration.storage._root_fd
    def do_not_open_common(repo):
        assert str(repo) != str(root), 'unused common metadata was inspected'
        return actual(repo)
    monkeypatch.setattr(migration.storage, '_root_fd', do_not_open_common)
    migration.require_complete(worker)


def test_work_fixture_links_are_literal_history_never_opened_or_published(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    outside = tmp_path / 'never-open-target'
    outside.write_bytes(b'private unrelated fixture')
    work = old / 'work'
    work.mkdir()
    link = work / 'harness-destcurrent'
    create_symlink(link, outside)
    actual_open = migration.os.open
    def no_target(path, *args, **kwargs):
        assert path not in (str(outside), outside.name, bytes(outside)), 'historical target opened'
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(migration.os, 'open', no_target)
    result = apply()
    assert result['retained_historical_links'] == [str(link)]
    assert link.is_symlink() and not migration.os.path.lexists(root / 'doc/harness/wbs/WBS__example/work/harness-destcurrent')
    journal = json.loads((root / migration.MANIFEST).read_text())
    row = journal['trees'][0]['entries']['work/harness-destcurrent']
    import base64
    target = base64.b64decode(row['link'], validate=True)
    assert target == bytes(outside) and row['sha256'] == hashlib.sha256(target).hexdigest()
    assert migration._link_file(root, 'doc/harness/tasks/TASK__example/work/harness-destcurrent') == row


@pytest.mark.parametrize('name', ['TASK.json', 'WBS.json', 'PLAN.md'])
def test_authority_symlinks_still_refuse_even_when_target_is_plain_file(tmp_path, monkeypatch, name):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    target = tmp_path / 'plain-target'
    target.write_text('unrelated')
    path = old / name
    if path.exists():
        path.unlink()
    create_symlink(path, target)
    with pytest.raises((ValueError, OSError)):
        apply()
    assert path.is_symlink() and not (root / migration.MANIFEST).exists()


@pytest.mark.parametrize('kind', ['copy', 'replacement', 'original-archive'])
@pytest.mark.skipif(os.name == 'nt', reason='POSIX v9fs linkat recovery; native retained-handle publication is tested separately')
def test_interrupted_v9fs_hardlink_publication_normalizes_only_exact_stage_pair(tmp_path, monkeypatch, kind):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    if kind == 'replacement':
        legacy = {key: value for key, value in control.items() if key != 'closed'}
        legacy['close_receipt_fingerprint'] = None
        (old / 'TASK.json').write_text(json.dumps(legacy))
    destination_name = 'TASK.json' if kind == 'original-archive' else 'WBS.control.json'
    actual = migration.storage._rename_new
    crashed = False
    def crash_after_link(parent, temporary, destination):
        nonlocal crashed
        if destination == destination_name and not crashed:
            crashed = True
            migration.os.link(temporary, destination, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            raise RuntimeError('crash after hardlink publication')
        return actual(parent, temporary, destination)
    monkeypatch.setattr(migration.storage, '_rename_new', crash_after_link)
    with pytest.raises(RuntimeError, match='hardlink publication'):
        apply()
    new = root / 'doc/harness/wbs/WBS__example/WBS.control.json'
    paired = root / migration.ARCHIVE / 'original/doc/harness/tasks/TASK__example/TASK.json' if kind == 'original-archive' else new
    assert paired.stat().st_nlink == 2 and (old / 'TASK.json').exists()
    with pytest.raises(ValueError, match='unsafe payload'):
        migration._hash_file(root, paired.relative_to(root).as_posix())
    monkeypatch.setattr(migration.storage, '_rename_new', actual)
    assert apply()['status'] == 'complete'
    assert new.stat().st_nlink == paired.stat().st_nlink == 1 and json.loads(new.read_text()) == control
    assert not list(new.parent.glob('.migration-*'))


@pytest.mark.parametrize('referenced', [False, True])
def test_disposed_closed_pool_does_not_hash_detached_closed_owner_work(tmp_path, monkeypatch, referenced):
    root, old, control, state, migration, apply = fixtures(tmp_path, monkeypatch)
    import subprocess
    import batch_state
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    (root / '.gitignore').write_text('/doc/harness/tasks/\n/doc/harness/runtime/\n/doc/harness/archive/\n')
    control['closed'] = not referenced
    (old / 'TASK.json').write_text(json.dumps(control))
    if referenced:
        state['batch_history'] = [dict(batch=dict(task_id=old.name, batch_id='disposed-pool', requests=[dict(slug='lead', request='Delivered result', scopes=['src'], depends_on=[])]))]
        (old / 'WBS.json').write_text(json.dumps(state))
    work = old / 'work/large-history'
    work.mkdir(parents=True)
    (work / 'unused-payload.bin').write_bytes(b'x' * (2 * 1024 * 1024))
    archive = root / 'doc/harness/archive/batch/TASK__lead'
    archive.mkdir(parents=True)
    (archive / 'TASK.json').write_text(json.dumps(control))
    (archive / 'WBS.json').write_text(json.dumps(state))
    request = dict(slug='lead', request='Delivered result', scopes=['src'], depends_on=[],
                   resolved_scopes=[str(root / 'src')], task_id='TASK__lead', status='integrated',
                   worker_id='stopped-worker', worktree=str(root / 'removed-worker'), branch='removed-branch',
                   spawn_head='a' * 40, run_id=control['run_id'], closed=True)
    request['result'] = dict(verdict='closed', task_id='TASK__lead', worktree=request['worktree'], branch=request['branch'], commit='a' * 40)
    request['checkpoint'] = dict(stage='harvested', destination_ref='refs/heads/main', run_id=control['run_id'],
                                 closed=True, branch_tip='a' * 40, integrated_tip='a' * 40,
                                 harvest=dict(archived=str(archive)), archive_fingerprint=batch_state.fingerprint(str(archive)))
    pool = dict(schema_version=1, repo=str(root), batch_id='disposed-pool', owner_task_id=old.name,
                max_leads=2, destination_ref='refs/heads/main', status='closed', halted=False,
                initial_head='a' * 40, created_at='then', updated_at='then', cap_source='user', requests=[request])
    path = root / 'doc/harness/runtime/batches/disposed-pool.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pool))
    original_pool = path.read_bytes()
    real_tree, real_fingerprint = migration._tree, batch_state.fingerprint
    def no_owner_payload(repo, relative):
        if not referenced:
            assert relative not in ('doc/harness/tasks/TASK__example', 'doc/harness/archive/batch/TASK__lead'), 'historical payload was needlessly measured'
        return real_tree(repo, relative)
    def no_history_fingerprint(candidate):
        if not referenced:
            assert candidate != str(archive), 'unreferenced historical archive was needlessly fingerprinted'
        return real_fingerprint(candidate)
    monkeypatch.setattr(migration, '_tree', no_owner_payload)
    monkeypatch.setattr(batch_state, 'fingerprint', no_history_fingerprint)
    result = apply()
    if referenced:
        assert (root / 'doc/harness/wbs/WBS__example').exists()
        assert json.loads(path.read_text())['owner_wbs_id'] == 'WBS__example'
        assert (root / 'doc/harness/archive/batch/WBS__lead/WBS.control.json').exists()
    else:
        assert str(old) in result['retained_historical_roots']
        assert not (root / 'doc/harness/wbs/WBS__example').exists() and not path.exists()
        assert (root / migration.ARCHIVE / 'original/doc/harness/runtime/batches/disposed-pool.json').read_bytes() == original_pool
        assert not (root / 'doc/harness/archive/batch/WBS__lead').exists()
    assert not (old / 'TASK.json').exists() and (work / 'unused-payload.bin').stat().st_size == 2 * 1024 * 1024


def test_normal_guard_does_not_decode_reappeared_legacy_control(tmp_path, monkeypatch):
    root, old, _, _, migration, apply = fixtures(tmp_path, monkeypatch)
    apply()
    (old / 'TASK.json').write_bytes(b'{broken old schema')
    actual = migration.storage._read
    def no_old_reader(repo, relative, *args, **kwargs):
        assert not relative.endswith('/TASK.json'), 'normal path parsed old control'
        return actual(repo, relative, *args, **kwargs)
    monkeypatch.setattr(migration.storage, '_read', no_old_reader)
    with pytest.raises(ValueError, match='retired execution controls exist'):
        migration.require_complete(root)


@pytest.mark.parametrize('action', ['open', 'migrate'])
@pytest.mark.parametrize('selective', [False, True])
def test_public_ignore_override_never_returns_ready_or_retires_original_control(tmp_path, monkeypatch, action, selective):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    import subprocess
    if action == 'open':
        (old / 'TASK.json').unlink()
        (old / 'WBS.json').unlink()
        old.rmdir()
    original = (old / 'TASK.json').read_bytes() if action == 'migrate' else None
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    policy = '/doc/harness/tasks/\n/doc/harness/archive/\n!/doc/harness/wbs/\n'
    policy += '/doc/harness/wbs/*\n!/doc/harness/wbs/WBS__*/\n!/doc/harness/wbs/WBS__*/WBS.control.json\n' if selective else '!/doc/harness/wbs/**\n'
    (root / '.gitignore').write_text(policy)
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('add', '.gitignore', 'doc/harness/manifest.yaml')
    git('-c', 'user.name=Ignore test', '-c', 'user.email=ignore@example.invalid', 'commit', '-qm', 'overriding policy')
    before, head = (root / '.gitignore').read_bytes(), git('rev-parse', 'HEAD')
    from test_harness_mcp_server import HarnessMcpServerTests
    args = dict(action=action, apply=True) if action == 'migrate' else dict(action='open', wbs_id='WBS__fresh')
    result = HarnessMcpServerTests()._call_in_repo(str(root), 'wbs', args)
    assert result.get('isError') and 'runtime metadata remains visible to Git' in json.dumps(result), result
    assert (root / '.gitignore').read_bytes() == before and git('rev-parse', 'HEAD') == head
    assert not (root / 'doc/harness/wbs/WBS__fresh/WBS.control.json').exists()
    if action == 'migrate':
        assert (old / 'TASK.json').read_bytes() == original
        assert not (root / 'doc/harness/wbs/WBS__example/WBS.control.json').exists()


def test_public_migrate_local_initialized_worktree_does_not_enable_common_project(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)
    import _lib
    import subprocess
    main = tmp_path / 'uninitialized-main'
    main.mkdir()
    def git(repo, *args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git(main, 'init', '-q')
    (main / '.gitignore').write_text('/doc/harness/tasks/\n')
    (main / 'source.txt').write_text('product')
    git(main, 'add', '.gitignore', 'source.txt')
    git(main, '-c', 'user.name=Local test', '-c', 'user.email=local@example.invalid', 'commit', '-qm', 'baseline')
    local = tmp_path / 'only-enabled-local'
    git(main, 'worktree', 'add', '-qb', 'local', str(local))
    (local / 'doc/harness').mkdir(parents=True)
    (local / 'doc/harness/manifest.yaml').write_text('type: library\n')
    old = local / 'doc/harness/tasks/TASK__local'
    old.mkdir(parents=True)
    control = dict(run_id=_lib.new_uuid7(), execution_mode='standard', required_lenses=['review-code', 'qa-cli'], closed=False)
    (old / 'TASK.json').write_text(json.dumps(control))
    (old / 'evidence.bin').write_bytes(b'real retained fixture\x00\xff')
    before, head = (main / '.gitignore').read_bytes(), git(main, 'rev-parse', 'HEAD')
    from test_harness_mcp_server import HarnessMcpServerTests
    result = HarnessMcpServerTests()._call_in_repo(str(local), 'wbs', dict(action='migrate', apply=True))
    assert not result.get('isError'), result
    actual = json.loads((local / 'doc/harness/wbs/WBS__local/WBS.control.json').read_text())
    assert actual == control
    assert not (main / 'doc/harness').exists()
    assert (main / '.gitignore').read_bytes() == before and (local / '.gitignore').read_bytes() == before
    assert git(main, 'rev-parse', 'HEAD') == head == git(local, 'rev-parse', 'HEAD')
    assert (local / 'doc/harness/wbs/WBS__local/evidence.bin').read_bytes() == b'real retained fixture\x00\xff'


def test_public_migrate_rolls_completed_journal_for_late_independent_publisher(tmp_path, monkeypatch):
    root, old, control, state, migration, _ = fixtures(tmp_path, monkeypatch)
    import _lib
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    hint = root / 'doc/harness/tasks/.active_sessions/.session-hint'
    hint.parent.mkdir()
    hint.write_bytes(b'first old hint')
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    first = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert not first.get('isError'), first
    original_journal = (root / migration.MANIFEST).read_bytes()
    original_digest = hashlib.sha256(original_journal).hexdigest()
    late = old.with_name('TASK__late-independent')
    late.mkdir()
    late_control = dict(control, run_id=_lib.new_uuid7())
    (late / 'TASK.json').write_text(json.dumps(late_control))
    (late / 'WBS.json').write_text(json.dumps(dict(state, run_id=late_control['run_id'])))
    hint.write_bytes(b'new old writer hint')
    with pytest.raises(ValueError, match='retired execution controls'):
        migration.require_complete(root)
    preview = server._call_in_repo(str(root), 'wbs', dict(action='migrate'))
    assert not preview.get('isError') and preview['structuredContent']['namespace_migration']['status'] == 'prepared', preview
    result = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert not result.get('isError'), result
    new = root / 'doc/harness/wbs/WBS__late-independent'
    assert json.loads((new / 'WBS.control.json').read_text()) == late_control
    assert json.loads((root / 'doc/harness/wbs/WBS__example/WBS.control.json').read_text()) == control
    records = list(migration.journals(root))
    assert records[0][0] == migration.MANIFEST and len(records) == 2
    historical = migration.HISTORY + '/' + original_digest + '.json'
    assert records[1][0] == historical and (root / historical).read_bytes() == original_journal
    prefix = migration.ARCHIVE + '/epochs/' + original_digest + '/original'
    assert records[0][1]['archive_prefix'] == prefix
    assert (root / prefix / 'doc/harness/tasks/.active_sessions/.session-hint').read_bytes() == b'new old writer hint'
    assert (root / migration.ARCHIVE / 'original/doc/harness/tasks/.active_sessions/.session-hint').read_bytes() == b'first old hint'
    migration.require_complete(root)
    # Historical corruption blocks history consumers, never becomes a normal
    # current-WBS reader or a reason to reinterpret old lifecycle evidence.
    (root / historical).write_bytes(original_journal + b'\n')
    with pytest.raises(ValueError, match='historical journal digest mismatch'):
        list(migration.journals(root))
    migration.require_complete(root)


def test_public_late_publisher_cannot_overwrite_existing_wbs_generation(tmp_path, monkeypatch):
    root, old, control, _, migration, _ = fixtures(tmp_path, monkeypatch)
    import _lib
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    assert not server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True)).get('isError')
    current = root / 'doc/harness/wbs/WBS__example/WBS.control.json'
    before, journal = current.read_bytes(), (root / migration.MANIFEST).read_bytes()
    late = dict(control, run_id=_lib.new_uuid7())
    (old / 'TASK.json').write_text(json.dumps(late))
    result = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert result.get('isError') and 'destination already exists' in json.dumps(result), result
    assert current.read_bytes() == before and (root / migration.MANIFEST).read_bytes() == journal
    assert json.loads((old / 'TASK.json').read_text()) == late


def test_public_retry_after_goal_conflict_uses_exact_completed_namespace_custody(tmp_path, monkeypatch):
    root, old, control, _, migration, _ = fixtures(tmp_path, monkeypatch)
    (root / 'doc/harness/manifest.yaml').write_text('type: library\n')
    goals = root / 'doc/harness/goals'
    goals.mkdir()
    row = dict(goal_id='GOAL__resume', objective='Resume requested work', tasks=[dict(task_id=old.name)], status='active')
    named = goals / 'GOAL__resume.json'
    named.write_text(json.dumps(row))
    current = goals / 'current.json'
    current.write_text(json.dumps(dict(row, objective='Wrong current objective')))
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    first = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert first.get('isError') and 'current/named Goal conflict' in json.dumps(first), first
    new = root / 'doc/harness/wbs/WBS__example/WBS.control.json'
    before, journal = new.read_bytes(), (root / migration.MANIFEST).read_bytes()
    assert json.loads(before) == control and not (old / 'TASK.json').exists()
    current.write_bytes(named.read_bytes())
    second = server._call_in_repo(str(root), 'wbs', dict(action='migrate', apply=True))
    assert not second.get('isError'), second
    assert new.read_bytes() == before and (root / migration.MANIFEST).read_bytes() == journal
    assert not current.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX orphan-stage fd recovery; native authenticated publication is tested separately')
def test_complete_orphan_stage_is_file_synced_before_publication(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    import stat
    source = old / 'work/source.bin'
    source.parent.mkdir()
    source.write_bytes(b'complete bytes without a prior explicit file sync')
    source.chmod(0o444)
    expected = migration._hash_file(root, source.relative_to(root).as_posix())
    destination = root / 'doc/harness/wbs/WBS__example/work/payload.bin'
    destination.parent.mkdir(parents=True)
    stage = destination.parent / migration.storage._publication_stage(destination.name)
    stage.write_bytes(source.read_bytes())
    stage.chmod(0o444)
    stage_inode = stage.stat().st_ino
    actual_sync, actual_publish, actual_open = migration.os.fsync, migration.storage._rename_new, migration.os.open
    events, stage_opens = [], []
    def opening(path, *args, **kwargs):
        if path == stage.name:
            stage_opens.append(path)
        return actual_open(path, *args, **kwargs)
    def syncing(fd):
        info = migration.os.fstat(fd)
        events.append(('file' if stat.S_ISREG(info.st_mode) else 'directory', info.st_ino))
        return actual_sync(fd)
    def publishing(parent, temporary, name):
        assert ('file', stage_inode) in events, 'orphan inode was published before file fsync'
        events.append(('publish', stage_inode))
        return actual_publish(parent, temporary, name)
    monkeypatch.setattr(migration.os, 'fsync', syncing)
    monkeypatch.setattr(migration.os, 'open', opening)
    monkeypatch.setattr(migration.storage, '_rename_new', publishing)
    migration._copy(root, source.relative_to(root).as_posix(), destination.relative_to(root).as_posix(), expected)
    assert [kind for kind, _ in events] == ['file', 'publish', 'directory']
    assert stage_opens == [stage.name], 'recovery must hash the held descriptor, not reopen its name'
    assert destination.read_bytes() == source.read_bytes() and not stage.exists()


@pytest.mark.parametrize('paired', [False, True])
@pytest.mark.skipif(os.name == 'nt', reason='POSIX file/directory fsync recovery; native authenticated publication is tested separately')
def test_visible_destination_retry_syncs_held_file_and_parent_before_custody(tmp_path, monkeypatch, paired):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    import stat
    source = old / 'work/source.bin'
    source.parent.mkdir()
    source.write_bytes(b'already visible does not establish directory durability')
    source.chmod(0o444)
    expected = migration._hash_file(root, source.relative_to(root).as_posix())
    destination = root / 'doc/harness/wbs/WBS__example/work/payload.bin'
    destination.parent.mkdir(parents=True)
    stage = destination.parent / migration.storage._publication_stage(destination.name)
    stage.write_bytes(source.read_bytes())
    stage.chmod(0o444)
    fd = migration.os.open(stage, migration.os.O_RDONLY)
    try:
        migration.os.fsync(fd)
    finally:
        migration.os.close(fd)
    if paired:
        migration.os.link(stage, destination)
    else:
        migration.os.rename(stage, destination)
    # The previous attempt stopped after publication, without syncing parent.
    inode, parent_inode = destination.stat().st_ino, destination.parent.stat().st_ino
    actual_sync, actual_open = migration.os.fsync, migration.os.open
    events, visible_opens = [], []
    def syncing(fd):
        info = migration.os.fstat(fd)
        events.append(('file' if stat.S_ISREG(info.st_mode) else 'directory', info.st_ino))
        return actual_sync(fd)
    def opening(path, *args, **kwargs):
        if path == destination.name:
            visible_opens.append(path)
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(migration.os, 'fsync', syncing)
    monkeypatch.setattr(migration.os, 'open', opening)
    migration._copy(root, source.relative_to(root).as_posix(), destination.relative_to(root).as_posix(), expected)
    assert events == [('file', inode), ('directory', parent_inode)]
    assert visible_opens == [destination.name], 'retry must hash the held destination descriptor'
    assert destination.stat().st_nlink == 1 and not stage.exists()
    assert destination.read_bytes() == source.read_bytes()


@pytest.mark.skipif(os.name != 'nt', reason='requires actual native retained-handle publication')
@pytest.mark.parametrize('stage', ['flush', 'rename', 'published'])
def test_native_interrupted_publication_preserves_history_and_resumes(tmp_path, monkeypatch, stage):
    root, old, control, _, migration, apply = fixtures(tmp_path, monkeypatch)
    import _wiki_windows as native
    history = old / 'evidence.bin'
    history.write_bytes(b'opaque preserved history\x00\xff')
    before = (old / 'TASK.json').read_bytes()
    actual_publish = native._publish_metadata
    fired = []
    def publishing(path, *args, **kwargs):
        if Path(path).name != 'WBS.control.json' or fired:
            return actual_publish(path, *args, **kwargs)
        if stage == 'published':
            result = actual_publish(path, *args, **kwargs)
            fired.append(stage)
            raise OSError('interrupted native publication')
        actual = native._FlushFileBuffers if stage == 'flush' else native._SetFileInformation
        def interrupt(*call_args):
            if stage == 'flush' or call_args[1] == native.FileRenameInfoEx:
                fired.append(stage)
                raise OSError('interrupted native publication')
            return actual(*call_args)
        with monkeypatch.context() as patch:
            patch.setattr(native, '_FlushFileBuffers' if stage == 'flush' else '_SetFileInformation', interrupt)
            return actual_publish(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(native, '_publish_metadata', publishing)
        with pytest.raises(OSError, match='interrupted native publication'):
            apply()
    assert fired == [stage]
    assert (old / 'TASK.json').read_bytes() == before
    assert history.read_bytes() == b'opaque preserved history\x00\xff'
    assert json.loads((root / migration.MANIFEST).read_text())['status'] == 'prepared'
    assert apply()['status'] == 'complete'
    destination = root / 'doc/harness/wbs/WBS__example'
    assert json.loads((destination / 'WBS.control.json').read_text()) == control
    assert (destination / 'evidence.bin').read_bytes() == history.read_bytes()


@pytest.mark.skipif(os.name != 'nt', reason='requires actual native authenticated copy and write-through rename')
def test_native_copy_flushes_before_publication_and_reuses_only_authenticated_journal(tmp_path, monkeypatch):
    root, old, _, _, migration, _ = fixtures(tmp_path, monkeypatch)
    import _wiki_windows as native
    source = old / 'work/source.bin'
    source.parent.mkdir()
    raw = b'private native publication\x00\xff'
    source.write_bytes(raw)
    source.chmod(0o444)
    relative = source.relative_to(root).as_posix()
    expected = migration._hash_file(root, relative)
    destination = 'doc/harness/wbs/WBS__example/work/source.bin'
    journal = migration._plan(root)
    actual_flush, actual_set, actual_open = native._FlushFileBuffers, native._SetFileInformation, native._open
    events, write_through = [], []
    def flushing(handle):
        events.append('flush')
        return actual_flush(handle)
    def setting(handle, kind, *args):
        if kind == native.FileRenameInfoEx:
            assert events and events[-1] == 'flush'
            events.append('rename')
        return actual_set(handle, kind, *args)
    def opening(path, access, **kwargs):
        if kwargs.get('create') == native.CREATE_NEW:
            write_through.append(kwargs.get('write_through'))
        return actual_open(path, access, **kwargs)
    monkeypatch.setattr(native, '_FlushFileBuffers', flushing)
    monkeypatch.setattr(native, '_SetFileInformation', setting)
    monkeypatch.setattr(native, '_open', opening)
    with pytest.raises(ValueError, match='authenticated prepared journal'):
        migration._copy(root, relative, destination, expected)
    assert not events and not (root / destination).exists()
    migration._copy(root, relative, destination, expected, journal=journal)
    assert events == ['flush', 'flush', 'rename'] and write_through == [True]
    assert (root / destination).read_bytes() == raw == source.read_bytes()
    events.clear()
    migration._copy(root, relative, destination, expected, journal=journal)
    assert not events, 'a journal-bound matching Native retry verifies bytes without mutating a readonly file'
    with pytest.raises(ValueError, match='authenticated prepared journal'):
        migration._copy(root, relative, destination, expected)
    assert (root / destination).read_bytes() == raw
