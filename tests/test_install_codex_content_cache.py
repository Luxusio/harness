"""Content-version publication preserves caches already loaded by Codex."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required to collect this module') from error


@pytest.fixture
def project(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('install_content_cache', Path(__file__).resolve().parents[1] / 'install.py')
    install = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = install
    spec.loader.exec_module(install)
    source = tmp_path / 'source'
    install._copytree_clean(install.PLUGIN_CODEX_ROOT, source / 'plugin-codex')
    install._copytree_clean(install.PLUGIN_ROOT, source / 'plugin')
    monkeypatch.setattr(install, 'PLUGIN_CODEX_ROOT', source / 'plugin-codex')
    monkeypatch.setattr(install, 'PLUGIN_ROOT', source / 'plugin')
    return install, source


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_identity_is_root_independent_repeatable_and_source_preserving(project, tmp_path):
    install, source = project
    manifest = source / 'plugin-codex/.codex-plugin/plugin.json'
    original, mode = manifest.read_bytes(), manifest.stat().st_mode
    first = install.sync_codex_payload(tmp_path / 'one')
    second = install.sync_codex_payload(tmp_path / 'other root with \' and " quotes')
    version = install._codex_plugin_version(first)
    assert version == install._codex_plugin_version(second)
    assert install._stamp_codex_plugin_version(first) == version
    assert install._codex_plugin_version(install.sync_codex_payload(tmp_path / 'one')) == version
    assert manifest.read_bytes() == original and manifest.stat().st_mode == mode
    marketplace = json.loads((tmp_path / 'one/.agents/plugins/marketplace.json').read_text())
    assert marketplace['plugins'][0]['version'] == version


@pytest.mark.parametrize('relative', ['plugin/scripts/_lib.py', 'plugin-codex/skills/run/SKILL.md', 'plugin/skills/plan/intake.md'])
def test_identity_covers_copied_and_projected_content(project, tmp_path, relative):
    install, source = project
    root = tmp_path / 'mirror'
    old = install._codex_plugin_version(install.sync_codex_payload(root))
    with (source / relative).open('a') as stream:
        stream.write('\n# changed content\n')
    assert install._codex_plugin_version(install.sync_codex_payload(root)) != old


@pytest.mark.parametrize('renderer', ['_codex_hooks_config', '_codex_mcp_config'])
def test_identity_covers_generated_configuration(project, tmp_path, monkeypatch, renderer):
    install, _ = project
    root = tmp_path / 'mirror'
    old = install._codex_plugin_version(install.sync_codex_payload(root))
    render = getattr(install, renderer)
    def changed(root):
        result = render(root)
        if renderer == '_codex_hooks_config':
            result['hooks']['SessionStart'][0]['hooks'][0]['timeout'] = 9
        else:
            result['mcpServers']['harness']['env']['PYTHONUTF8'] = '1'
        return result
    monkeypatch.setattr(install, renderer, changed)
    assert install._codex_plugin_version(install.sync_codex_payload(root)) != old


def test_identity_excludes_runtime_bytecode(project, tmp_path):
    install, source = project
    root = tmp_path / 'mirror'
    old = install._codex_plugin_version(install.sync_codex_payload(root))
    cache = source / 'plugin/scripts/__pycache__'
    cache.mkdir(exist_ok=True)
    (cache / 'volatile.pyc').write_bytes(b'not source')
    assert install._codex_plugin_version(install.sync_codex_payload(root)) == old


def test_changed_payload_and_equal_reinstall_never_replace_loaded_cache(project, tmp_path, monkeypatch):
    install, source = project
    mirror = install.sync_codex_payload(tmp_path / 'mirror')
    home = tmp_path / 'runtime'
    old = install.install_codex_plugin_cache(mirror, home)
    original = inventory(old)
    rename, replace = os.rename, os.replace
    def guarded(operation):
        def call(src, dst, *args, **kwargs):
            if Path(src) == old or Path(dst) == old:
                raise PermissionError('loaded cache is locked')
            return operation(src, dst, *args, **kwargs)
        return call
    monkeypatch.setattr(install.os, 'rename', guarded(rename))
    monkeypatch.setattr(install.os, 'replace', guarded(replace))
    assert install.install_codex_plugin_cache(mirror, home) == old
    with (source / 'plugin/scripts/_lib.py').open('a') as stream:
        stream.write('\n# new payload\n')
    mirror = install.sync_codex_payload(tmp_path / 'mirror')
    new = install.install_codex_plugin_cache(mirror, home)
    assert new != old and inventory(old) == original
    assert new.name == install._codex_plugin_version(mirror)
    assert str(new) in (new / 'hooks.json').read_text()
    assert str(new) in (new / '.mcp.json').read_text()
    assert json.loads((new / '.codex-plugin/plugin.json').read_text())['version'] == new.name


@pytest.mark.parametrize('kind', ['corrupt', 'symlink', 'file', 'empty'])
def test_existing_identity_mismatch_refuses_without_overwrite(project, tmp_path, kind):
    install, _ = project
    mirror = install.sync_codex_payload(tmp_path / 'mirror')
    home = tmp_path / 'runtime'
    target = install.install_codex_plugin_cache(mirror, home)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'untouched').write_text('user bytes')
    if kind == 'corrupt':
        (target / 'scripts/_lib.py').write_text('corrupt bytes')
    else:
        shutil.rmtree(target)
        if kind == 'symlink':
            target.symlink_to(outside, target_is_directory=True)
        elif kind == 'file':
            target.write_text('existing file')
        else:
            target.mkdir()
    before = inventory(target) if target.is_dir() else target.read_bytes()
    with pytest.raises(RuntimeError, match='cache identity mismatch'):
        install.install_codex_plugin_cache(mirror, home)
    assert (inventory(target) if target.is_dir() else target.read_bytes()) == before
    assert (outside / 'untouched').read_text() == 'user bytes'
    assert not list(target.parent.glob('.cache-staging-*'))


def test_failed_publication_preserves_prior_cache_and_cleans_stage(project, tmp_path, monkeypatch):
    install, source = project
    root, home = tmp_path / 'mirror', tmp_path / 'runtime'
    mirror = install.sync_codex_payload(root)
    old = install.install_codex_plugin_cache(mirror, home)
    original = inventory(old)
    with (source / 'plugin/scripts/_lib.py').open('a') as stream:
        stream.write('\n# changed\n')
    mirror = install.sync_codex_payload(root)
    def denied(*args, **kwargs):
        raise PermissionError('new cache publication denied')
    monkeypatch.setattr(install.os, 'rename', denied)
    with pytest.raises(PermissionError, match='publication denied'):
        install.install_codex_plugin_cache(mirror, home)
    assert inventory(old) == original
    assert not list(old.parent.glob('.cache-staging-*'))


def test_concurrent_identical_publications_share_one_cache(project, tmp_path, monkeypatch):
    install, _ = project
    mirror = install.sync_codex_payload(tmp_path / 'mirror')
    home = tmp_path / 'runtime'
    rename = os.rename
    publications = []
    def publish(src, dst):
        assert not os.path.lexists(dst), 'must not replace another publication'
        publications.append(str(dst))
        return rename(src, dst)
    monkeypatch.setattr(install.os, 'rename', publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: install.install_codex_plugin_cache(mirror, home), range(2)))
    assert results[0] == results[1] and publications == [str(results[0])]
    assert not list(results[0].parent.glob('.cache-staging-*'))


@pytest.mark.parametrize('change', ['mode', 'empty-directory'])
def test_identity_matches_compared_topology_and_modes(project, tmp_path, change):
    install, source = project
    root, home = tmp_path / 'mirror', tmp_path / 'runtime'
    mirror = install.sync_codex_payload(root)
    old = install.install_codex_plugin_cache(mirror, home)
    old_mode = (old / 'scripts/_lib.py').stat().st_mode
    original = inventory(old)
    if change == 'mode':
        (source / 'plugin/scripts/_lib.py').chmod(0o600)
    else:
        (source / 'plugin/scripts/new-empty-dir').mkdir()
    mirror = install.sync_codex_payload(root)
    new = install.install_codex_plugin_cache(mirror, home)
    assert new != old and inventory(old) == original
    assert (old / 'scripts/_lib.py').stat().st_mode == old_mode
    if change == 'mode':
        assert (new / 'scripts/_lib.py').stat().st_mode & 0o777 == 0o600
    else:
        assert (new / 'scripts/new-empty-dir').is_dir()
        assert not (old / 'scripts/new-empty-dir').exists()
