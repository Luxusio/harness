"""Host success must identify the actual prepared Python cache bytes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('wiki_installer_selection', Path(__file__).resolve().parents[1] / 'install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = tmp_path / 'versions/0.2.0+h0123456789abcdef'
    module.ensure_private_directory(payload / 'plugin')
    module.atomic_write(payload / 'plugin/.mcp.json', b'{"command":"python"}')
    home = tmp_path / 'host'
    module.ensure_private_directory(home)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(home))
    return module, payload, home


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
def test_cli_success_cannot_accept_old_native_cache_bytes(installer, monkeypatch, runtime):
    module, payload, home = installer
    cache = home / 'plugins/cache/knowledge-documents/wiki-atlassian' / payload.name
    module.ensure_private_directory(cache)
    module.atomic_write(cache / '.mcp.json', b'{"command":"java"}')
    row = dict(pluginId='wiki-atlassian@knowledge-documents', id='wiki-atlassian@knowledge-documents',
               installed=True, enabled=True, version=payload.name, scope='user', installPath=str(cache))
    monkeypatch.setattr(module, 'run_bounded', lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps([row]).encode()))
    with pytest.raises(ValueError, match='cached Python payload'):
        module._verify_selected(runtime, payload, [runtime])
    assert module.safe_read(cache / '.mcp.json', 100) == b'{"command":"java"}'


def test_claude_normalized_version_directory_is_accepted_by_registry_identity(installer, monkeypatch):
    module, payload, home = installer
    cache = home / 'plugins/cache/knowledge-documents/wiki-atlassian' / payload.name.replace('+', '-')
    module.ensure_private_directory(cache.parent)
    shutil.copytree(payload / 'plugin', cache)
    row = dict(id='wiki-atlassian@knowledge-documents', enabled=True, version=payload.name, scope='user', installPath=str(cache))
    monkeypatch.setattr(module, 'run_bounded', lambda *a, **k: subprocess.CompletedProcess([], 0, json.dumps([row]).encode()))
    module._verify_selected('claude', payload, ['claude'])
    row['version'] = '0.1.0+hold'
    with pytest.raises(ValueError):
        module._verify_selected('claude', payload, ['claude'])


def _legacy(installer):
    module, payload, _ = installer
    root = payload.parent.parent
    version = '0.1.0+hfedcba9876543210'
    package = root / 'packages' / version
    plugin = package / 'plugin'
    for name in ('skills', 'agents'):
        module.ensure_private_directory(plugin / name)
    contents = {
        '.claude-plugin/marketplace.json': module.marketplace_bytes('./plugin'),
        'plugin/scripts/launch.py': b'# retained old launcher\n',
        'plugin/lib/wiki-atlassian.jar': b'retained synthetic jar',
        'plugin/README.md': b'legacy README',
        'plugin/PROVENANCE.md': b'legacy provenance',
    }
    for directory in ('.claude-plugin', '.codex-plugin'):
        manifest = {'name': 'wiki-atlassian', 'version': version,
            'description': 'Shared Atlassian wiki knowledge and native Confluence document operations, independent of project workflows.'}
        if directory == '.codex-plugin':
            manifest['skills'] = './skills/'
        manifest['mcpServers'] = './.mcp.json'
        # Historical text writes use CRLF on native Windows.
        contents['plugin/' + directory + '/plugin.json'] = (json.dumps(manifest, indent=2) + '\n').replace('\n', '\r\n').encode()
    transport = {'mcpServers': {'wiki_atlassian': {'command': r'C:\Program Files\Java\bin\java.exe',
        'args': ['-jar', str(plugin / 'lib/wiki-atlassian.jar')],
        'env_vars': ['HOME', 'JAVA_HOME', 'WIKI_ATLASSIAN_JAVA', 'ATLASSIAN_EMAIL',
            'ATLASSIAN_API_TOKEN', 'ATLASSIAN_BASE_URL', 'SystemRoot']}}}
    contents['plugin/.mcp.json'] = (json.dumps(transport, indent=2) + '\n').replace('\n', '\r\n').encode()
    for name, content in contents.items():
        path = package / name
        module.ensure_private_directory(path.parent)
        module.atomic_write(path, content)
    metadata = root / '.claude-plugin/marketplace.json'
    module.ensure_private_directory(metadata.parent)
    old = module.marketplace_bytes('./packages/' + version + '/plugin')
    module.atomic_write(metadata, old)
    return package, metadata, old


def test_recognized_legacy_selection_migrates_once_and_preserves_old_bytes(installer):
    module, payload, home = installer
    package, metadata, old = _legacy(installer)
    before = module._inventory(package, private=False)
    unrelated = home / 'unrelated-settings.json'
    module.atomic_write(unrelated, b'{"keep":true}')
    for _ in range(2):
        with module.marketplace(payload) as root:
            assert metadata.read_bytes() == module.marketplace_bytes('./versions/' + payload.name + '/plugin')
    assert module._inventory(package, private=False) == before
    assert unrelated.read_bytes() == b'{"keep":true}'


def test_failed_legacy_registration_restores_exact_selection_and_both_payloads(installer):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    before = module._inventory(package, private=False)
    with pytest.raises(RuntimeError, match='registration failed'):
        with module.marketplace(payload):
            with pytest.raises(BlockingIOError):
                with module.marketplace(payload):
                    pytest.fail('Concurrent selection update succeeded')
            raise RuntimeError('registration failed')
    assert metadata.read_bytes() == old
    assert module._inventory(package, private=False) == before
    assert (payload / 'plugin/.mcp.json').read_bytes() == b'{"command":"python"}'


@pytest.mark.parametrize('mutation', ['market-owner', 'extra-plugin', 'extra-field', 'version',
    'codex-skills', 'transport', 'transport-command', 'package-market', 'missing-jar', 'linked-file', 'linked-directory',
    'hardlink', 'writable-file', 'writable-directory'])
def test_legacy_migration_refuses_modified_unknown_or_unsafe_payloads(installer, mutation):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    if mutation in ('market-owner', 'extra-plugin', 'extra-field'):
        value = json.loads(old)
        if mutation == 'market-owner':
            value['owner']['name'] = 'someone-else'
        elif mutation == 'extra-plugin':
            value['plugins'].append({'name': 'unrelated', 'source': './elsewhere'})
        else:
            value['extra'] = 'unowned setting'
        module.atomic_write(metadata, (json.dumps(value, indent=2) + '\n').encode())
    elif mutation in ('version', 'codex-skills'):
        path = package / ('plugin/.claude-plugin/plugin.json' if mutation == 'version' else 'plugin/.codex-plugin/plugin.json')
        value = json.loads(path.read_bytes())
        value['version' if mutation == 'version' else 'skills'] = 'changed'
        module.atomic_write(path, (json.dumps(value, indent=2) + '\n').encode())
    elif mutation == 'transport':
        module.atomic_write(package / 'plugin/.mcp.json', b'{"mcpServers":[]}')
    elif mutation == 'transport-command':
        path = package / 'plugin/.mcp.json'
        value = json.loads(path.read_bytes())
        value['mcpServers']['wiki_atlassian']['command'] = r'C:\modified\not-java.exe'
        module.atomic_write(path, (json.dumps(value, indent=2) + '\n').encode())
    elif mutation == 'package-market':
        module.atomic_write(package / '.claude-plugin/marketplace.json', module.marketplace_bytes('./other'))
    elif mutation == 'missing-jar':
        (package / 'plugin/lib/wiki-atlassian.jar').unlink()
    elif mutation in ('linked-file', 'linked-directory'):
        if os.name == 'nt':
            pytest.skip('symlink privilege fixture; native junction coverage exercises reparse refusal')
        path = package / ('plugin/README.md' if mutation == 'linked-file' else 'plugin/lib')
        moved = package / 'retained-original'
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=mutation == 'linked-directory')
    elif mutation == 'hardlink':
        os.link(package / 'plugin/README.md', package / 'plugin/alias')
    else:
        if os.name == 'nt':
            pytest.skip('POSIX mode fixture; native unsafe DACL coverage is separate')
        (package / ('plugin/README.md' if mutation == 'writable-file' else 'plugin/lib')).chmod(0o777)
    previous = metadata.read_bytes()
    with pytest.raises((ValueError, OSError)):
        with module.marketplace(payload):
            pytest.fail('An unsafe legacy selection was overwritten')
    assert metadata.read_bytes() == previous


@pytest.mark.parametrize('source', ['./packages/0.1.0+hfedcba9876543210/../plugin',
    './packages/0.1.0+unknown/plugin', '../packages/0.1.0+hfedcba9876543210/plugin',
    './packages/0.1.0+hfedcba9876543210/plugin/extra'])
def test_legacy_unknown_and_traversal_selections_are_preserved(installer, source):
    module, payload, _ = installer
    _, metadata, _ = _legacy(installer)
    old = module.marketplace_bytes(source)
    module.atomic_write(metadata, old)
    with pytest.raises(ValueError):
        with module.marketplace(payload):
            pytest.fail('Unknown legacy source was accepted')
    assert metadata.read_bytes() == old


def test_legacy_prepare_only_needs_no_host_cli(installer, monkeypatch, capsys):
    module, payload, home = installer
    package, metadata, _ = _legacy(installer)
    monkeypatch.setattr(module, 'prepare', lambda data: payload)
    monkeypatch.setattr(module.sys, 'argv', ['install.py', '--prepare-only'])
    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    monkeypatch.setattr(module, '_host_command', lambda runtime: pytest.fail('prepare-only resolved a host command'))
    assert module.main() == 0
    assert capsys.readouterr().out.strip() == str(payload.parent.parent)
    assert package.is_dir()


@pytest.mark.parametrize('arguments', [[], ['--runtime', 'codex']])
def test_missing_host_refuses_before_preparing_or_resolving_python(installer, monkeypatch, capsys, arguments):
    module, _, _ = installer
    monkeypatch.setattr(module.sys, 'argv', ['install.py', *arguments])
    monkeypatch.setattr(module.shutil, 'which', lambda *a, **k: None)
    monkeypatch.setattr(module, 'prepare', lambda *a: pytest.fail('missing host prepared a payload'))
    monkeypatch.setattr(module, 'select_python', lambda **k: pytest.fail('missing host required mise'))
    assert module.main() == 1
    assert 'CLI' in capsys.readouterr().err


def test_host_cli_failure_restores_legacy_selection(installer, monkeypatch, capsys):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    before = module._inventory(package, private=False)
    monkeypatch.setattr(module, 'prepare', lambda data: payload)
    monkeypatch.setattr(module.sys, 'argv', ['install.py', '--runtime', 'codex'])
    monkeypatch.setattr(module, '_host_command', lambda runtime: ['synthetic-codex'])
    monkeypatch.setattr(module, 'run_bounded', lambda *a, **k: subprocess.CompletedProcess([], 1, b''))
    assert module.main() == 1
    assert 'Codex: FAILED' in capsys.readouterr().out
    assert metadata.read_bytes() == old
    assert module._inventory(package, private=False) == before


@pytest.mark.parametrize('handler', ['prepare', 'registration'])
@pytest.mark.parametrize('operation,code,visible', [
    ('FileRenameInfoEx', 1, True), ('FileRenameInfoEx', 50, True),
    ('FileRenameInfoEx', 87, True), ('FileRenameInfoEx', 5, True),
    ('FileRenameInfoEx', 32, True), ('FileRenameInfoEx', 0xFFFFFFFF, True),
    (None, None, False), ('private-token-sentinel', 87, False),
    (['FileRenameInfoEx'], 87, False), ('FileRenameInfoEx', True, False),
    ('FileRenameInfoEx', None, False), ('FileRenameInfoEx', 87.0, False),
    ('FileRenameInfoEx', 'private-token-sentinel', False),
    ('FileRenameInfoEx', -1, False), ('FileRenameInfoEx', 0x100000000, False),
])
def test_modeled_native_error_diagnostics_are_bounded_and_preserve_selection(
        installer, monkeypatch, capsys, handler, operation, code, visible):
    """Modeled OSError metadata tests redaction, not native Windows execution."""
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    before = module._inventory(package, private=False)
    private_path = r'C:\private-path-sentinel\private-token-sentinel'
    failure = OSError(87, 'private-token-sentinel', private_path)
    if operation is not None:
        failure._wiki_operation = operation
        failure._wiki_native_code = code

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(module, 'prepare', fail if handler == 'prepare' else lambda data: payload)
    monkeypatch.setattr(module.sys, 'argv', ['install.py', '--runtime', 'codex'])
    monkeypatch.setattr(module, '_host_command', lambda runtime: ['synthetic-codex'])
    monkeypatch.setattr(module, 'run_bounded', fail)
    assert module.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    if visible:
        assert f'FileRenameInfoEx publication failed; native Windows code {code}' in output
    else:
        generic = ('Private payload operation failed; check file permissions and the selected interpreter'
                   if handler == 'prepare' else 'Host CLI could not complete registration')
        assert generic in output
        assert 'FileRenameInfoEx' not in output
    assert 'private-path-sentinel' not in output and 'private-token-sentinel' not in output
    assert metadata.read_bytes() == old
    assert module._inventory(package, private=False) == before
    assert (payload / 'plugin/.mcp.json').read_bytes() == b'{"command":"python"}'


@pytest.mark.parametrize('budget', ['entries', 'bytes'])
def test_legacy_inventory_limits_refuse_before_selection_change(installer, monkeypatch, budget):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    if budget == 'entries':
        monkeypatch.setattr(module, 'LEGACY_MAX_ENTRIES', 2)
    else:
        monkeypatch.setattr(module, 'LEGACY_MAX_BYTES', 32)
    with pytest.raises(ValueError):
        with module.marketplace(payload):
            pytest.fail('Oversize legacy tree was selected')
    assert metadata.read_bytes() == old


def test_legacy_directory_enumeration_error_preserves_selection(installer, monkeypatch):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    nested = package / 'plugin/skills/unreadable'
    module.ensure_private_directory(nested)
    module.atomic_write(nested / 'uninspected-file', b'must inspect this file')
    real_scandir = os.scandir

    def fail_nested(path):
        if Path(path) == nested:
            raise OSError(5, 'Synthetic directory enumeration failure')
        return real_scandir(path)

    monkeypatch.setattr(module.os, 'scandir', fail_nested)
    with pytest.raises(OSError, match='enumeration failure'):
        with module.marketplace(payload):
            pytest.fail('Partial legacy inventory was selected')
    assert metadata.read_bytes() == old


def test_only_legacy_jar_can_exceed_python_payload_file_limit(installer):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    large = b'x' * (16 * 1024 * 1024 + 1)
    module.atomic_write(package / 'plugin/lib/wiki-atlassian.jar', large)
    with module.marketplace(payload):
        pass
    assert (package / 'plugin/lib/wiki-atlassian.jar').read_bytes() == large
    module.atomic_write(metadata, old)
    module.atomic_write(package / 'plugin/README.md', large)
    with pytest.raises(ValueError):
        with module.marketplace(payload):
            pytest.fail('Oversize non-JAR legacy file was selected')
    assert metadata.read_bytes() == old


@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink fixture; native reparse rejection is tested separately')
@pytest.mark.parametrize('part', ['package', 'packages', 'metadata', 'manifest'])
def test_legacy_linked_ancestors_and_metadata_are_never_selected(installer, part):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    path = {'package': package, 'packages': package.parent, 'metadata': metadata.parent,
            'manifest': metadata}[part]
    moved = path.with_name(path.name + '-original')
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=part != 'manifest')
    with pytest.raises((ValueError, OSError)):
        with module.marketplace(payload):
            pytest.fail('Linked legacy storage was selected')
    assert metadata.read_bytes() == old


@pytest.mark.skipif(os.name == 'nt' or not hasattr(os, 'geteuid') or os.geteuid() != 0,
                    reason='requires a disposable POSIX fixture with a foreign owner')
def test_legacy_foreign_owner_is_rejected_without_changing_selection(installer):
    module, payload, _ = installer
    package, metadata, old = _legacy(installer)
    path = package / 'plugin/README.md'
    os.chown(path, 65534, -1)
    try:
        with pytest.raises(ValueError, match='Unsafe metadata'):
            with module.marketplace(payload):
                pytest.fail('Foreign-owned legacy payload was selected')
        assert metadata.read_bytes() == old
    finally:
        os.chown(path, 0, -1)
