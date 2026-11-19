"""Project authentication at the same startup boundary used by Desktop."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from wiki_atlassian.config import AUTH_FIELDS, Project
import wiki_platform as platform


CREDS = dict(zip(AUTH_FIELDS, ('https://wiki.example.invalid', 'project@example.invalid', 'synthetic-only')))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    (root / '.git').mkdir()
    manifest = root / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'wiki': {
        'enabled': True, 'backend': 'atlassian', 'site': CREDS[AUTH_FIELDS[0]],
        'spaces': [{'key': 'TEAM'}],
    }}), encoding='utf8')
    return root


def auth_file(root):
    path = root / 'mise.local.toml'
    platform.atomic_write(path, b'[env]\n# Synthetic provider input\n')
    return path


def provider(monkeypatch, path, values=None, result=None):
    calls = []
    monkeypatch.setattr(platform, 'native_executable', lambda *a, **k: sys.executable)
    monkeypatch.setattr('wiki_atlassian.config.shutil.which', lambda *a, **k: sys.executable)
    entries = {key: {'source': str(path), 'value': value} for key, value in (values or CREDS).items()}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return result or subprocess.CompletedProcess(argv, 0, json.dumps(entries).encode(), b'')

    monkeypatch.setattr(platform, 'run_bounded', run)
    return calls, entries


def test_desktop_without_inherited_auth_loads_exact_project_once(project, monkeypatch):
    path = auth_file(project)
    calls, _ = provider(monkeypatch, path)
    original = {'PATH': os.environ['PATH'], 'MISE_ENV': 'other-project',
                'MISE_CONFIG_FILE': '/another/mise.toml', '__MISE_ORIG_PATH': '/another/bin'}
    before = original.copy()
    selected = Project.from_project(project, original)
    assert selected.credentials.email == CREDS['ATLASSIAN_EMAIL']
    assert selected.credentials.api_token == CREDS['ATLASSIAN_API_TOKEN']
    assert original == before
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[1:] == ['env', '--json-extended']
    assert kwargs['cwd'] == project
    assert kwargs['timeout'] == 10 and kwargs['capture_stderr'] == (os.name == 'nt')
    assert kwargs['env']['MISE_YES'] == '0'
    assert kwargs['env']['MISE_AUTO_INSTALL'] == '0'
    assert not any(key.startswith(('ATLASSIAN_', '__MISE_')) for key in kwargs['env'])
    assert not {'MISE_ENV', 'MISE_CONFIG_FILE'} & kwargs['env'].keys()
    assert 'synthetic-only' not in str(argv)


def test_local_tuple_wins_as_one_unit_and_nested_repositories_remain_inert(project, monkeypatch):
    path = auth_file(project)
    calls, _ = provider(monkeypatch, path)
    inherited = dict(CREDS, ATLASSIAN_API_TOKEN='old-token', ATLASSIAN_EMAIL='old@example.invalid')
    nested = project / 'ordinary/subdir'
    nested.mkdir(parents=True)
    assert Project.from_project(nested, inherited).credentials.email == CREDS['ATLASSIAN_EMAIL']
    (nested / '.git').mkdir()
    assert Project.from_project(nested, inherited) is None
    assert len(calls) == 1


def test_injected_only_callers_remain_supported_and_do_not_start_mise(project, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('An injected-only caller must not evaluate mise')
    monkeypatch.setattr(platform, 'run_bounded', forbidden)
    assert Project.from_project(project, CREDS).credentials.api_token == 'synthetic-only'
    with pytest.raises(ValueError):
        Project.from_project(project, {'ATLASSIAN_API_TOKEN': 'partial-only'})


@pytest.mark.parametrize('fault', ['missing', 'foreign-source', 'control', 'wrong-origin'])
def test_invalid_local_auth_never_borrows_inherited_or_private_fields(project, monkeypatch, fault):
    path = auth_file(project)
    _, values = provider(monkeypatch, path)
    if fault == 'missing':
        del values['ATLASSIAN_EMAIL']
    elif fault == 'foreign-source':
        values['ATLASSIAN_EMAIL']['source'] = str(project / '.config/harness/mise.local.toml')
    elif fault == 'control':
        values['ATLASSIAN_API_TOKEN']['value'] = 'private-value\n'
    else:
        values['ATLASSIAN_BASE_URL']['value'] = 'https://another.invalid'
    with pytest.raises(ValueError) as error:
        Project.from_project(project, CREDS)
    assert 'synthetic-only' not in str(error.value)
    assert 'private-value' not in str(error.value)


@pytest.mark.parametrize('result', [
    subprocess.CompletedProcess([], 1, b'', b'private-value'),
    subprocess.CompletedProcess([], 0, b'private-value', b''),
])
def test_mise_failure_or_untrusted_output_is_redacted(project, monkeypatch, result):
    path = auth_file(project)
    provider(monkeypatch, path, result=result)
    with pytest.raises(ValueError) as error:
        Project.from_project(project, CREDS)
    assert 'private-value' not in str(error.value)


def test_timeout_is_bounded_and_redacted(project, monkeypatch):
    auth_file(project)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('mise', 10, output=b'private-value')
    monkeypatch.setattr(platform, 'native_executable', lambda *a, **k: sys.executable)
    monkeypatch.setattr('wiki_atlassian.config.shutil.which', lambda *a, **k: sys.executable)
    monkeypatch.setattr(platform, 'run_bounded', timeout)
    with pytest.raises(ValueError) as error:
        Project.from_project(project, CREDS)
    assert 'private-value' not in str(error.value)


def test_disabled_project_does_not_read_auth_or_start_provider(project, monkeypatch):
    auth_file(project)
    (project / 'doc/harness/manifest.yaml').write_text('wiki:\n  enabled: false\n', encoding='utf8')
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled projects must remain inert')
    monkeypatch.setattr(platform, 'private_source', forbidden)
    assert Project.from_project(project, CREDS) is None


def test_unsafe_credential_file_is_refused_before_provider(project, monkeypatch):
    path = auth_file(project)
    calls, _ = provider(monkeypatch, path)
    os.link(path, project / 'credential-hardlink')
    with pytest.raises(ValueError):
        Project.from_project(project, CREDS)
    assert not calls


def test_real_mise_loads_project_auth_for_a_desktop_environment(project):
    mise = shutil.which('mise')
    if mise is None:
        pytest.skip('mise is unavailable')
    path = project / 'mise.local.toml'
    content = '[env]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in CREDS.items())
    platform.atomic_write(path, content.encode('utf8'))
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(('ATLASSIAN_', 'MISE_', '__MISE_'))}
    subprocess.run([mise, 'trust', str(path)], cwd=project, env=environment,
                   check=True, capture_output=True, timeout=10)
    try:
        selected = Project.from_project(project, environment)
        assert selected.credentials.email == CREDS['ATLASSIAN_EMAIL']
        assert selected.credentials.api_token == CREDS['ATLASSIAN_API_TOKEN']
        assert not any(key.startswith('ATLASSIAN_') for key in environment)
    finally:
        subprocess.run([mise, 'trust', '--untrust', str(path)], cwd=project, env=environment,
                       check=True, capture_output=True, timeout=10)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX real provider process regression')
def test_posix_project_auth_uses_the_real_bounded_runner(project, monkeypatch):
    path = auth_file(project)
    mise = project / 'mise'
    entries = {key: {'source': str(path), 'value': value} for key, value in CREDS.items()}
    mise.write_text(f'#!{sys.executable}\nimport json\nprint({json.dumps(json.dumps(entries))})\n', encoding='utf8')
    mise.chmod(0o700)
    monkeypatch.setattr('wiki_atlassian.config.shutil.which', lambda *a, **k: str(mise))
    selected = Project.from_project(project, {})
    assert selected.credentials.email == CREDS['ATLASSIAN_EMAIL']
