"""Host success must identify the actual prepared Python cache bytes."""
import importlib.util
import json
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
