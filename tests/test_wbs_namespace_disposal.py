"""Real stopped-lead migration must preserve history and finish disposal."""

from test_symlink_fixture import create_symlink
import json
import os

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required to collect this module') from error

from test_batch_state import Pool, request, task, _git, RUN
from conftest import SCRIPTS_DIR


@pytest.fixture(autouse=True)
def scripts_path(monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS_DIR)


def retire_pool_identity(value):
    if isinstance(value, dict):
        keys = {'wbs_id': 'task_id', 'wbs_dir': 'task_dir', 'owner_wbs_id': 'owner_task_id'}
        return {keys.get(key, key): retire_pool_identity(item) for key, item in value.items()}
    if isinstance(value, list):
        return [retire_pool_identity(item) for item in value]
    return 'TASK__' + value[5:] if isinstance(value, str) and value.startswith('WBS__') else value


def seed_legacy_outcome(repo, name):
    from _lib import new_uuid7
    old = repo / 'doc/harness/tasks' / ('TASK__' + name)
    old.mkdir(parents=True)
    control = dict(run_id=new_uuid7(), execution_mode='standard', required_lenses=['review-code', 'qa-cli'], closed=False)
    (old / 'TASK.json').write_text(json.dumps(control))
    (old / 'PLAN.md').write_text('# Historical outcome\n')


def close_imported_fixture(repo, name):
    path = repo / 'doc/harness/wbs' / ('WBS__' + name) / 'WBS.control.json'
    control = json.loads(path.read_text())
    control['closed'] = True
    path.write_text(json.dumps(control))


def _harvest_for_proof(pool, worktree, monkeypatch):
    """Reach the Native archive through its actual owning batch transaction."""
    import batch_harvest
    if os.name != 'nt':
        return batch_harvest.harvest(str(pool.repo), str(worktree), 'WBS__a')
    import batch_finish
    import batch_state
    reached = []
    def retain_for_inventory(*args, **kwargs):
        reached.append(True)
        raise batch_finish.Outcome('kept', 'fixture pauses after durable harvest for removal inventory')
    with monkeypatch.context() as pause:
        pause.setattr(batch_finish, '_cleanup', retain_for_inventory)
        assert batch_state.main(pool.argv('finish', '--slug', 'a')[2:]) != 0
    assert reached
    item = json.loads(pool.state.read_text())['requests'][0]
    assert item['checkpoint']['stage'] == 'harvested'
    return item['checkpoint']['harvest']


def stopped_legacy_lead(tmp_path, monkeypatch, *, frozen=False, checkpoint=False, large=False, historical_link=None,
                        preexisting_epoch=False):
    import wbs_namespace_migration as migration

    pool = Pool(tmp_path, monkeypatch)
    ignore = pool.repo / '.gitignore'
    ignore.write_text(ignore.read_text() + 'doc/harness/tasks/\ndoc/harness/checkpoints/\n')
    _git('add', '.gitignore', cwd=pool.repo)
    _git('commit', '-qm', 'retain historical operational ignores', cwd=pool.repo)
    if preexisting_epoch:
        seed_legacy_outcome(pool.repo, 'earlier')
        assert migration.apply.__wrapped__(pool.repo)['status'] == 'complete'
        close_imported_fixture(pool.repo, 'earlier')
    pool.init([request('a')])
    pool.claim()
    worktree, branch = pool.bind('a', 'actual-worker')
    (worktree / 'a.txt').write_text('implemented\n')
    _git('add', 'a.txt', cwd=worktree)
    _git('commit', '-qm', 'implementation', '--trailer', 'Harness-Task: TASK__a', cwd=worktree)
    current = task(worktree, 'a', closed=True)
    (current / 'historical-review.bin').write_bytes(b'Original TASK__a review\x00\xff' + (b'x' * (128 * 1024) if large else b''))
    if historical_link:
        external = tmp_path / 'outside-sentinel'
        if historical_link == 'directory':
            external.mkdir()
            (external / 'precious.bin').write_bytes(b'outside bytes must survive')
        else:
            external.write_bytes(b'outside bytes must survive')
        link = current / 'work/link-to-external'
        link.parent.mkdir()
        create_symlink(link, external, target_is_directory=historical_link == 'directory')
    if frozen:
        snapshot = current / 'work/verification-targets/0123456789abcdef/source'
        snapshot.mkdir(parents=True)
        (snapshot.parent / 'target.json').write_text(json.dumps(dict(token='0123456789abcdef', run_id=RUN,
            manifest=[], spec=dict(inputs={'source': ['a.txt']}, lenses=['qa-cli']))))
        (snapshot / 'source.rs').write_text('reviewed source\n')
        (snapshot / 'source.rs').chmod(0o400)
        snapshot.chmod(0o500)
    pool.cli(*pool.result('a', worktree, branch, 'closed', 'actual-worker', run_id=RUN))
    old = worktree / 'doc/harness/tasks/TASK__a'
    old.parent.mkdir(parents=True, exist_ok=True)
    current.rename(old)
    (old / 'WBS.control.json').rename(old / 'TASK.json')
    if checkpoint:
        path = worktree / 'doc/harness/checkpoints/TASK__a.md'
        path.parent.mkdir(parents=True)
        path.write_text('# Checkpoint — TASK__a\n\n- written: earlier\n\n## Task state\nclosed\n\n## Progress\nreviewed\n\n## Next action\nfinish\n')

    pool.state.write_text(json.dumps(retire_pool_identity(json.loads(pool.state.read_text()))))
    # The fixture injects original on-disk data, then uses the real importer
    # body. Public migration writer authentication is tested separately.
    assert migration.apply.__wrapped__(pool.repo)['status'] == 'complete'
    return pool, worktree, old


@pytest.mark.parametrize('frozen', [False, True])
def test_migrated_stopped_lead_finishes_without_losing_legacy_bytes(tmp_path, monkeypatch, frozen):
    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch, frozen=frozen, checkpoint=True)
    before = (old / 'historical-review.bin').read_bytes()
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated' and result['cleanup']['removed'] and result['cleanup']['branch_deleted']
    assert not worktree.exists()
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    assert (recovery / 'doc/harness/tasks/TASK__a/historical-review.bin').read_bytes() == before
    assert (recovery / 'doc/harness/tasks/TASK__a/PLAN.md').read_text() == '# Fixture task\n'
    original_control = recovery / 'doc/harness/archive/wbs-namespace-migration/original/doc/harness/tasks/TASK__a/TASK.json'
    if os.name == 'nt':
        from wiki_platform import safe_metadata_snapshot
        # The logical historical name can exceed MAX_PATH; read its genuine
        # retained bytes through the same native-safe metadata boundary.
        raw, _ = safe_metadata_snapshot(original_control, 16384)
        expected = dict(run_id=RUN, execution_mode='standard',
                        required_lenses=['review-code', 'qa-cli'], closed=True)
        assert json.loads(raw) == expected
        assert raw == json.dumps(expected).encode('utf-8')
    else:
        assert original_control.is_file()
    assert (recovery / 'doc/harness/checkpoints/TASK__a.md').read_text().startswith('# Checkpoint — TASK__a\n')
    if frozen:
        source = recovery / 'doc/harness/tasks/TASK__a/work/verification-targets/0123456789abcdef/source/source.rs'
        if os.name == 'nt':
            from wiki_platform import safe_metadata_snapshot
            raw, stamp = safe_metadata_snapshot(source, 16384)
            assert raw.replace(b'\r\n', b'\n') == b'reviewed source\n'
            assert stamp[4] & 1
        else:
            assert source.read_text() == 'reviewed source\n' and source.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize('mutation', ['unknown', 'changed', 'symlink'])
def test_migration_history_disposal_refuses_unknown_or_changed_bytes(tmp_path, monkeypatch, mutation):
    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch)
    if mutation == 'unknown':
        target = old / 'unrecorded.bin'
        target.write_bytes(b'preserve this user file')
    else:
        target = old / 'historical-review.bin'
        if mutation == 'changed':
            target.write_bytes(b'changed after migration')
        else:
            target.unlink()
            create_symlink(target, worktree / 'a.txt')
    result = pool.cli('finish', '--slug', 'a', ok=False)
    assert result['status'] == 'kept' and worktree.exists()
    assert not result['cleanup']['removed'] and not result['cleanup']['branch_deleted']
    assert os.path.lexists(target)


def test_durable_history_copy_cannot_be_overwritten_on_retry(tmp_path, monkeypatch):
    import batch_harvest

    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch)
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    _harvest_for_proof(pool, worktree, monkeypatch)
    batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    copy = recovery / 'doc/harness/tasks/TASK__a/historical-review.bin'
    copy.write_bytes(b'existing different historical bytes')
    with pytest.raises(batch_harvest.HarvestError, match='copy changed'):
        batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    assert copy.read_bytes() == b'existing different historical bytes'
    assert (old / 'historical-review.bin').read_bytes() == b'Original TASK__a review\x00\xff'


def test_partial_native_removal_reuses_preserved_streamed_history(tmp_path, monkeypatch):
    import batch_harvest
    import batch_state

    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch, large=True,
                                           historical_link=None if os.name == 'nt' else 'directory')
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    tip = _git('rev-parse', 'HEAD', cwd=pool.repo)
    harvested = _harvest_for_proof(pool, worktree, monkeypatch)
    batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    relative = 'doc/harness/tasks/TASK__a/historical-review.bin'
    expected = (worktree / relative).read_bytes()
    proof = dict(stage='harvested', integrated_tip=tip, harvest=harvested,
                 archive_fingerprint=batch_state.fingerprint(harvested['archived']))
    _git('worktree', 'unlock', str(worktree), cwd=pool.repo, check=False)
    _git('worktree', 'remove', str(worktree), cwd=pool.repo)
    # Model native interruption: only a subset of old history survived after
    # Git unregistered the worktree. Custody must use the existing main copy.
    residue = worktree / relative
    residue.parent.mkdir(parents=True)
    residue.write_bytes(expected)
    if os.name != 'nt':
        link = residue.parent / 'work/link-to-external'
        link.parent.mkdir()
        create_symlink(link, tmp_path / 'outside-sentinel', target_is_directory=True)
    precious = residue.parent / 'unrecorded.bin'
    precious.write_bytes(b'keep unknown residue')
    with pytest.raises(batch_harvest.HarvestError, match='unpreserved'):
        batch_harvest.remove_ordinary_residue(str(pool.repo), str(worktree), 'WBS__a', tip, proof)
    assert precious.read_bytes() == b'keep unknown residue'
    precious.unlink()
    batch_harvest.remove_ordinary_residue(str(pool.repo), str(worktree), 'WBS__a', tip, proof)
    assert not worktree.exists()
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    assert (recovery / relative).read_bytes() == expected
    if os.name != 'nt':
        assert (tmp_path / 'outside-sentinel/precious.bin').read_bytes() == b'outside bytes must survive'


@pytest.mark.parametrize('historical_link', [False, True])
def test_migrated_selected_module_lead_preserves_history_before_disposal(tmp_path, monkeypatch, historical_link):
    if os.name == 'nt' and historical_link:
        pytest.skip('Native migration preserves and refuses historical reparse payloads; regular history remains covered')
    from test_batch_state import _s1_pool, _s1_request, _s1_complete
    import wbs_namespace_migration as migration

    pool = _s1_pool(tmp_path, monkeypatch)
    ignore = pool.repo / '.gitignore'
    ignore.write_text(ignore.read_text() + 'doc/harness/tasks/\n')
    _git('add', '.gitignore', cwd=pool.repo)
    _git('commit', '-qm', 'historical operational ignores', cwd=pool.repo)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch, current, module_tip = _s1_complete(pool)
    (current / 'review.bin').write_bytes(b'Original module review\x00\xff')
    outside = tmp_path / 'outside-module-sentinel'
    outside.write_bytes(b'preserve external module sentinel')
    if historical_link:
        link = current / 'work/module-link'
        link.parent.mkdir()
        create_symlink(link, outside)
    old = worktree / 'doc/harness/tasks/TASK__a'
    old.parent.mkdir(parents=True)
    current.rename(old)
    (old / 'WBS.control.json').rename(old / 'TASK.json')
    pool.state.write_text(json.dumps(retire_pool_identity(json.loads(pool.state.read_text()))))
    assert migration.apply.__wrapped__(pool.repo)['status'] == 'complete'
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated' and not worktree.exists()
    assert not _git('branch', '--list', branch, cwd=pool.repo)
    assert _git('rev-parse', 'HEAD', cwd=pool.repo / 'libs/module0') == module_tip
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    assert (recovery / 'doc/harness/tasks/TASK__a/review.bin').read_bytes() == b'Original module review\x00\xff'
    if historical_link:
        assert (recovery / 'doc/harness/tasks/TASK__a/work/module-link').read_bytes() == os.fsencode(outside)
    assert outside.read_bytes() == b'preserve external module sentinel'


@pytest.mark.parametrize('kind', ['file', 'directory'])
@pytest.mark.skipif(os.name == 'nt', reason='POSIX literal-link migration; Native reparse payloads are explicitly refused and preserved')
def test_declared_historical_links_preserve_lexical_descriptors_without_touching_targets(tmp_path, monkeypatch, kind):
    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch, historical_link=kind)
    target = tmp_path / 'outside-sentinel'
    sentinel = target / 'precious.bin' if kind == 'directory' else target
    raw_target = os.readlink(os.fsencode(old / 'work/link-to-external'))
    assert not os.path.lexists(worktree / 'doc/harness/wbs/WBS__a/work/link-to-external')
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated' and not worktree.exists()
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    saved = recovery / 'doc/harness/tasks/TASK__a/work/link-to-external'
    if os.name == 'nt':
        from wiki_platform import safe_metadata_snapshot
        raw, stamp = safe_metadata_snapshot(saved, 16384)
        assert raw == raw_target and not stamp[4] & 1
    else:
        assert saved.is_file() and not saved.is_symlink() and saved.read_bytes() == raw_target
        assert saved.stat().st_mode & 0o777 == 0o600
    assert sentinel.read_bytes() == b'outside bytes must survive'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX imported link descriptor; Native migration refuses reparse payload before import')
def test_changed_historical_link_keeps_worktree_and_external_targets(tmp_path, monkeypatch):
    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch, historical_link='file')
    other = tmp_path / 'other-sentinel'
    other.write_bytes(b'other outside bytes')
    link = old / 'work/link-to-external'
    link.unlink()
    create_symlink(link, other)
    result = pool.cli('finish', '--slug', 'a', ok=False)
    assert result['status'] == 'kept' and worktree.exists() and link.is_symlink()
    assert other.read_bytes() == b'other outside bytes'
    assert (tmp_path / 'outside-sentinel').read_bytes() == b'outside bytes must survive'


def test_link_in_current_wbs_is_not_a_historical_disposal_exception(tmp_path, monkeypatch):
    pool, worktree, _ = stopped_legacy_lead(tmp_path, monkeypatch,
                                         historical_link=None if os.name == 'nt' else 'file')
    current = worktree / 'doc/harness/wbs/WBS__a/work/current-link'
    create_symlink(current, tmp_path / 'outside-sentinel')
    result = pool.cli('finish', '--slug', 'a', ok=False)
    assert result['status'] == 'kept' and current.is_symlink() and worktree.exists()
    assert (tmp_path / 'outside-sentinel').read_bytes() == b'outside bytes must survive'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX imported link proof; actual Native no-follow migration refusal is tested below')
def test_historical_link_proof_never_opens_its_external_target(tmp_path, monkeypatch):
    import builtins
    import batch_harvest

    pool, worktree, _ = stopped_legacy_lead(tmp_path, monkeypatch, historical_link='directory')
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    _harvest_for_proof(pool, worktree, monkeypatch)
    target = str(tmp_path / 'outside-sentinel')
    actual_open, actual_os_open, actual_scandir = builtins.open, os.open, os.scandir

    def forbid_target(path, dir_fd=None):
        if isinstance(path, int):
            return
        selected = os.fsdecode(os.fspath(path))
        if dir_fd is not None and not os.path.isabs(selected):
            selected = '/proc/self/fd/' + str(dir_fd) + '/' + selected
        resolved = os.path.realpath(selected)
        assert resolved != target and not resolved.startswith(target + os.sep), 'external target was opened'

    def opening(path, *args, **kwargs):
        forbid_target(path)
        return actual_open(path, *args, **kwargs)

    def opening_fd(path, *args, **kwargs):
        forbid_target(path, kwargs.get('dir_fd'))
        return actual_os_open(path, *args, **kwargs)

    def scanning(path):
        forbid_target(path)
        return actual_scandir(path)

    with monkeypatch.context() as guard:
        guard.setattr(builtins, 'open', opening)
        guard.setattr(os, 'open', opening_fd)
        guard.setattr(os, 'scandir', scanning)
        batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    assert (tmp_path / 'outside-sentinel/precious.bin').read_bytes() == b'outside bytes must survive'


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Native reparse refusal and public migration entrypoint')
@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_native_public_migration_refuses_reparse_history_without_following_or_losing_bytes(tmp_path, monkeypatch, kind):
    from test_harness_mcp_server import HarnessMcpServerTests
    import _wiki_windows as win
    pool = Pool(tmp_path, monkeypatch)
    seed_legacy_outcome(pool.repo, 'reparse')
    old = pool.repo / 'doc/harness/tasks/TASK__reparse'
    target = tmp_path / 'outside-target'
    if kind == 'directory':
        from test_windows_core_runtime import _junction
        target.mkdir()
        sentinel = target / 'precious.bin'
        sentinel.write_bytes(b'keep external bytes\x00\xff')
        _junction(old / 'external-link', target)
    else:
        sentinel = target
        sentinel.write_bytes(b'keep external bytes\x00\xff')
        create_symlink(old / 'external-link', target)
    link = old / 'external-link'
    original = {name: (old / name).read_bytes() for name in ('TASK.json', 'PLAN.md')}
    literal = os.readlink(link)
    actual_open = win._CreateFile
    attempted = []
    outside = os.path.normcase(str(target))
    def no_follow(path, *args):
        selected = os.path.normcase(str(path)).removeprefix('\\\\?\\')
        assert selected != outside and not selected.startswith(outside + os.sep), 'external target was opened'
        attempted.append(selected)
        return actual_open(path, *args)
    with monkeypatch.context() as boundary:
        boundary.setattr(win, '_CreateFile', no_follow)
        result = HarnessMcpServerTests()._call_in_repo(str(pool.repo), 'wbs', dict(action='migrate', apply=True))
    assert result.get('isError'), result
    assert attempted and os.readlink(link) == literal
    assert all((old / name).read_bytes() == raw for name, raw in original.items())
    assert sentinel.read_bytes() == b'keep external bytes\x00\xff'
    assert not (pool.repo / 'doc/harness/wbs/WBS__reparse/WBS.control.json').exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX linkat fallback crash window; Native write-through recovery publication has dedicated coverage')
def test_process_loss_after_fallback_recovery_publication_resumes_exact_pair(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    import sys
    import batch_harvest

    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch)
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    _harvest_for_proof(pool, worktree, monkeypatch)
    destination = ('doc/harness/archive/worktree-recovery/WBS__a/' + RUN + '/namespace-history/'
                   'doc/harness/archive/wbs-namespace-migration/original/doc/harness/tasks/TASK__a/TASK.json')
    reserved = '.namespace-' + hashlib.sha256(destination.encode()).hexdigest()[:32]
    code = '''import hashlib,os,sys
sys.path.insert(0,sys.argv[1])
import batch_harvest,wbs_namespace_migration
actual=wbs_namespace_migration.storage._rename_new
stage='.namespace-'+hashlib.sha256(sys.argv[4].encode()).hexdigest()[:32]
def publish_and_lose_process(parent,temporary,destination):
    if temporary==stage:
        os.link(temporary,destination,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
        os._exit(97)
    return actual(parent,temporary,destination)
wbs_namespace_migration.storage._rename_new=publish_and_lose_process
batch_harvest.ordinary_removal_proof(sys.argv[2],sys.argv[3],'WBS__a')
'''
    crashed = subprocess.run([sys.executable, '-B', '-c', code, str(SCRIPTS_DIR), str(pool.repo),
                              str(worktree), destination], capture_output=True, text=True, timeout=60)
    assert crashed.returncode == 97, crashed.stderr
    published = pool.repo / destination
    stage = published.with_name(reserved)
    before = published.read_bytes()
    assert published.stat().st_ino == stage.stat().st_ino and published.stat().st_nlink == 2
    assert (old / 'historical-review.bin').read_bytes() == b'Original TASK__a review\x00\xff'
    batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    assert published.read_bytes() == before and published.stat().st_nlink == 1 and not stage.exists()


@pytest.mark.parametrize('impostor_stage', [False, True])
def test_unrelated_recovery_hardlinks_are_not_publication_aliases(tmp_path, monkeypatch, impostor_stage):
    import hashlib
    import batch_harvest

    pool, worktree, old = stopped_legacy_lead(tmp_path, monkeypatch)
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    _harvest_for_proof(pool, worktree, monkeypatch)
    batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    destination = ('doc/harness/archive/worktree-recovery/WBS__a/' + RUN +
                   '/namespace-history/doc/harness/tasks/TASK__a/historical-review.bin')
    copy = pool.repo / destination
    unrelated = copy.with_name('unrelated-history-link')
    os.link(copy, unrelated)
    stage = copy.with_name('.namespace-' + hashlib.sha256(destination.encode()).hexdigest()[:32])
    if impostor_stage:
        stage.write_bytes(b'not the reserved file inode')
    with pytest.raises((batch_harvest.HarvestError, ValueError), match=(
            'nonlinked disk object' if os.name == 'nt' else 'unaccounted namespace recovery publication links')):
        batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    assert copy.exists() and unrelated.exists() and copy.stat().st_nlink == 2
    assert (old / 'historical-review.bin').read_bytes() == copy.read_bytes() == b'Original TASK__a review\x00\xff'
    if impostor_stage:
        assert stage.read_bytes() == b'not the reserved file inode'


def test_disposal_uses_completed_ownership_journal_after_later_import(tmp_path, monkeypatch):
    import wbs_namespace_migration as migration

    pool, worktree, _ = stopped_legacy_lead(tmp_path, monkeypatch)
    original_manifest = (pool.repo / migration.MANIFEST).read_bytes()
    seed_legacy_outcome(pool.repo, 'later')
    assert migration.apply.__wrapped__(pool.repo)['status'] == 'complete'
    close_imported_fixture(pool.repo, 'later')
    records = list(migration.journals(pool.repo))
    assert len(records) == 2
    assert any(relative != migration.MANIFEST and (pool.repo / relative).read_bytes() == original_manifest
               for relative, _ in records)
    assert not any(tree['root'] == str(worktree) for tree in records[0][1]['trees'])
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated' and not worktree.exists()
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    assert (recovery / 'doc/harness/tasks/TASK__a/historical-review.bin').read_bytes() == b'Original TASK__a review\x00\xff'


def test_worker_imported_in_later_epoch_uses_its_exact_control_prefix(tmp_path, monkeypatch):
    import wbs_namespace_migration as migration

    pool, worktree, _ = stopped_legacy_lead(tmp_path, monkeypatch, preexisting_epoch=True)
    current = next(iter(migration.journals(pool.repo)))[1]
    prefix = current['archive_prefix']
    assert prefix.startswith(migration.ARCHIVE + '/epochs/')
    original_control = prefix + '/doc/harness/tasks/TASK__a/TASK.json'
    before = (worktree / original_control).read_bytes()
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated' and not worktree.exists()
    recovery = pool.repo / 'doc/harness/archive/worktree-recovery/WBS__a' / RUN / 'namespace-history'
    if os.name == 'nt':
        from wiki_platform import safe_metadata_snapshot
        assert safe_metadata_snapshot(recovery / original_control, 16384)[0] == before
    else:
        assert (recovery / original_control).read_bytes() == before


@pytest.mark.skipif(os.name == 'nt', reason='POSIX file-fsync/linkat publication; Native publisher uses retained writer and NTFS write-through rename')
def test_complete_orphan_recovery_stage_syncs_held_file_before_publication(tmp_path, monkeypatch):
    import hashlib
    import stat
    import batch_harvest
    import wbs_namespace_migration as namespace

    pool, worktree, _ = stopped_legacy_lead(tmp_path, monkeypatch)
    _git('merge', '--ff-only', 'worktree-a', cwd=pool.repo)
    _harvest_for_proof(pool, worktree, monkeypatch)
    relative = (namespace.ARCHIVE + '/original/doc/harness/tasks/TASK__a/TASK.json')
    destination = ('doc/harness/archive/worktree-recovery/WBS__a/' + RUN + '/namespace-history/' + relative)
    published = pool.repo / destination
    published.parent.mkdir(parents=True)
    temporary = '.namespace-' + hashlib.sha256(destination.encode()).hexdigest()[:32]
    staged = published.with_name(temporary)
    raw = (worktree / relative).read_bytes()
    # Reproduce the complete orphan state reached after writing but before
    # the producer's file fsync. Close deliberately does not fsync this file.
    producer = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        assert os.write(producer, raw) == len(raw)
    finally:
        os.close(producer)
    identity = (staged.stat().st_dev, staged.stat().st_ino)
    events, synced = [], []
    actual_sync, actual_publish = os.fsync, namespace.storage._rename_new

    def sync(fd):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) == identity:
            assert stat.S_ISREG(info.st_mode)
            synced.append(fd)
            events.append('file-sync')
        return actual_sync(fd)

    def publish(parent, stage, name):
        if stage == temporary:
            assert synced, 'orphan stage published without file fsync'
            info = os.fstat(synced[-1])  # Must still hold this exact file FD.
            assert (info.st_dev, info.st_ino) == identity and info.st_nlink == 1
            events.append('publish')
        return actual_publish(parent, stage, name)

    with monkeypatch.context() as calls:
        calls.setattr(os, 'fsync', sync)
        calls.setattr(namespace.storage, '_rename_new', publish)
        batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), 'WBS__a')
    assert events[:2] == ['file-sync', 'publish']
    assert published.read_bytes() == raw and published.stat().st_nlink == 1 and not staged.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory-fsync durability; Native exact-content publisher reuse is verified separately')
def test_visible_recovery_destination_syncs_held_file_and_parent_before_reuse(tmp_path, monkeypatch):
    import hashlib
    import batch_harvest

    repo, worktree = tmp_path / 'repo', tmp_path / 'worker'
    repo.mkdir()
    worktree.mkdir()
    raw = b'authentic historical bytes'
    source = worktree / 'source.bin'
    source.write_bytes(raw)
    source.chmod(0o600)
    relative = 'doc/harness/archive/worktree-recovery/WBS__fixture/run/namespace-history/source.bin'
    published = repo / relative
    published.parent.mkdir(parents=True)
    temporary = published.with_name('native-stage')
    producer = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        assert os.write(producer, raw) == len(raw)
        os.fsync(producer)
    finally:
        os.close(producer)
    os.rename(temporary, published)  # Crash window: parent has not been fsynced.
    file_identity = (published.stat().st_dev, published.stat().st_ino)
    parent_identity = (published.parent.stat().st_dev, published.parent.stat().st_ino)
    actual_sync = os.fsync
    events, held = [], []

    def sync(fd):
        info = os.fstat(fd)
        current = (info.st_dev, info.st_ino)
        if current == file_identity:
            held.append(fd)
            events.append('file-sync')
        elif current == parent_identity:
            assert held, 'visible recovery entry reused without file fsync'
            original = os.fstat(held[-1])
            assert (original.st_dev, original.st_ino) == file_identity
            events.append('parent-sync')
        return actual_sync(fd)

    with monkeypatch.context() as calls:
        calls.setattr(os, 'fsync', sync)
        batch_harvest._preserve_namespace_file(str(repo), str(worktree), 'source.bin', relative,
            dict(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), mode=0o600))
    assert events == ['file-sync', 'parent-sync']
    assert published.read_bytes() == source.read_bytes() == raw and published.stat().st_nlink == 1


@pytest.mark.skipif(os.name != 'nt', reason='requires real NTFS write-through publisher and retained Native metadata handles')
def test_native_history_publication_reuses_exact_content_and_refuses_changed_copy(tmp_path, monkeypatch):
    import hashlib
    import batch_harvest
    import wiki_platform
    repo, worktree = tmp_path / 'repo', tmp_path / 'worker'
    repo.mkdir()
    worktree.mkdir()
    raw = b'authentic Native historical bytes\x00\xff'
    source = worktree / 'source.bin'
    source.write_bytes(raw)
    relative = 'doc/harness/archive/worktree-recovery/WBS__fixture/run/namespace-history/source.bin'
    expected = dict(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), mode=0o600)
    actual = wiki_platform.metadata_copy
    results = []
    def publication(*args, **kwargs):
        result = actual(*args, **kwargs)
        results.append(result)
        return result
    monkeypatch.setattr(wiki_platform, 'metadata_copy', publication)
    for _ in range(2):
        batch_harvest._preserve_namespace_file(str(repo), str(worktree), 'source.bin', relative, expected)
    assert results == ['published', 'reused']
    copy = repo / relative
    assert copy.read_bytes() == source.read_bytes() == raw
    copy.write_bytes(b'unrelated changed historical bytes')
    with pytest.raises(ValueError):
        batch_harvest._preserve_namespace_file(str(repo), str(worktree), 'source.bin', relative, expected)
    assert copy.read_bytes() == b'unrelated changed historical bytes'
    assert source.read_bytes() == raw
