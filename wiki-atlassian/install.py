#!/usr/bin/env python3
"""Prepare and register the native Python wiki MCP without a build toolchain."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
HELPERS = ROOT.parent / 'plugin/scripts'
sys.path.insert(0, str(HELPERS))
from wiki_platform import (atomic_write, ensure_private_directory, file_lock, run_bounded,
                           safe_read, safe_metadata_read, trusted_metadata_directory)

PAYLOAD_DIRS = ('.claude-plugin', '.codex-plugin', 'skills', 'agents', 'scripts', 'wiki_atlassian')
SHARED_HELPERS = ('personal_feedback.py', 'document_destination.py', 'wiki_platform.py', '_wiki_windows.py')
VERSION = re.compile(r'0\.2\.0\+h[0-9a-f]{16}')


class RegistrationFailure(Exception):
    """Per-host failures were already rendered without sensitive CLI output."""


def marketplace_bytes(relative):
    return (json.dumps({'name': 'knowledge-documents', 'owner': {'name': 'knowledge-documents'},
        'plugins': [{'name': 'wiki-atlassian', 'source': relative}]}, indent=2) + '\n').encode('utf-8')


def _source_files():
    result = {}
    required = ('main', 'models', 'config', 'remote', 'storage', 'markdown', 'replica',
                'changes', 'search', 'service', 'multispace', 'protocol', 'tools', 'references', 'cursors', 'policy')
    if any(not (ROOT / 'wiki_atlassian' / (name + '.py')).is_file() for name in required):
        raise ValueError('Python wiki source is incomplete; preserve the current installation')
    for name in PAYLOAD_DIRS:
        directory = ROOT / name
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('Plugin source directory is unavailable or unsafe')
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [part for part in dirs if part != '__pycache__']
            for part in dirs:
                path = Path(base) / part
                if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                    raise ValueError('Plugin source may not contain linked directories')
            for part in files:
                if part.endswith(('.pyc', '.pyo')):
                    continue
                path = Path(base) / part
                result[path.relative_to(ROOT).as_posix()] = safe_read(path, 16 * 1024 * 1024)
    for name in ('.mcp.json', 'PROVENANCE.md', 'README.md', 'pyproject.toml'):
        result[name] = safe_read(ROOT / name, 1024 * 1024)
    for name in SHARED_HELPERS:
        path = HELPERS / name
        result['scripts/' + name] = safe_read(path, 1024 * 1024)
    return result


def _inventory(root, private=True):
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        if private:
            ensure_private_directory(Path(base))
        else:
            with trusted_metadata_directory(Path(base)):
                pass
        for part in dirs:
            if private:
                ensure_private_directory(Path(base) / part)
            else:
                with trusted_metadata_directory(Path(base) / part):
                    pass
        for part in files:
            path = Path(base) / part
            result[path.relative_to(root).as_posix()] = (safe_read(path, 16 * 1024 * 1024, private=True)
                if private else safe_metadata_read(path, 16 * 1024 * 1024))
    return result


def prepare(data_dir):
    """Publish one immutable payload under a symlink-free stable marketplace."""
    data_dir = Path(data_dir)
    if not data_dir.is_absolute() or '..' in data_dir.parts:
        raise ValueError('Plugin data directory must be an absolute canonical path')
    if sys.version_info < (3, 12):
        raise ValueError('Python 3.12 or later is required')
    try:
        import yaml
        if not hasattr(yaml, 'SafeLoader'):
            raise ImportError()
    except ImportError:
        raise ValueError('Install PyYAML for the Python interpreter running this installer') from None
    parent = data_dir / 'marketplace/versions'
    ensure_private_directory(parent)
    files = _source_files()
    interpreter = str(Path(sys.executable).absolute())
    digest = hashlib.sha256((interpreter + '\0' + str(parent) + '\0').encode('utf-8'))
    for name, content in sorted(files.items()):
        digest.update(name.encode('utf-8') + b'\0' + content + b'\0')
    version = '0.2.0+h' + digest.hexdigest()[:16]
    destination = parent / version
    for name in ('.claude-plugin/plugin.json', '.codex-plugin/plugin.json'):
        value = json.loads(files[name])
        value['version'] = version
        files[name] = (json.dumps(value, indent=2) + '\n').encode('utf-8')
    transport = json.loads(files['.mcp.json'])
    transport['mcpServers']['atlassian']['command'] = interpreter
    transport['mcpServers']['atlassian']['args'] = ['-I', '-B', str(destination / 'plugin/scripts/launch.py')]
    files['.mcp.json'] = (json.dumps(transport, indent=2) + '\n').encode('utf-8')
    with file_lock(parent / 'publish.lock', blocking=False):
        if os.path.lexists(destination):
            ensure_private_directory(destination)
            if _inventory(destination / 'plugin') != files:
                raise ValueError('Existing immutable plugin payload differs; preserve it and inspect')
            return destination
        stage = parent / ('.install-' + secrets.token_hex(16))
        ensure_private_directory(stage)
        try:
            plugin = stage / 'plugin'
            ensure_private_directory(plugin)
            for name, content in files.items():
                path = plugin.joinpath(*name.split('/'))
                ensure_private_directory(path.parent)
                atomic_write(path, content)
            if _inventory(plugin) != files:
                raise ValueError('Staged plugin payload changed; publication refused')
            # All handles owned by inventory/read have closed, including on Windows.
            os.rename(stage, destination)
            ensure_private_directory(destination)
            if _inventory(destination / 'plugin') != files:
                raise ValueError('Published plugin payload changed; registration refused')
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return destination


@contextmanager
def marketplace(payload):
    payload = Path(payload)
    if not payload.is_absolute() or payload.parent.name != 'versions' or not VERSION.fullmatch(payload.name):
        raise ValueError('Use a prepared immutable plugin payload')
    ensure_private_directory(payload)
    _inventory(payload / 'plugin')
    root = payload.parent.parent
    metadata = root / '.claude-plugin'
    ensure_private_directory(metadata)
    with file_lock(root / 'install.lock', blocking=False):
        path = metadata / 'marketplace.json'
        try:
            old = safe_read(path, 65536, private=True)
        except FileNotFoundError:
            old = None
        if old is not None:
            value = json.loads(old)
            if value.get('name') != 'knowledge-documents' or not isinstance(value.get('plugins'), list) or len(value['plugins']) != 1:
                raise ValueError('Existing marketplace metadata is not managed by this installer')
            item = value['plugins'][0]
            if item.get('name') != 'wiki-atlassian':
                raise ValueError('Existing marketplace contains another plugin; preserve it')
            selected = item.get('source')
            if selected != './plugin':
                if not isinstance(selected, str) or not re.fullmatch(r'\./versions/0\.2\.0\+h[0-9a-f]{16}/plugin', selected):
                    raise ValueError('Existing marketplace selection is not a managed payload')
        atomic_write(path, marketplace_bytes('./versions/' + payload.name + '/plugin'))
        try:
            yield root
        except BaseException:
            # Never erase either payload. Restore only the exact selection we own.
            if safe_read(path, 65536, private=True) == marketplace_bytes('./versions/' + payload.name + '/plugin'):
                if old is None:
                    path.unlink()
                else:
                    atomic_write(path, old)
            raise


def _host_command(runtime):
    executable = shutil.which(runtime + '.exe') if os.name == 'nt' else None
    executable = executable or shutil.which(runtime)
    if executable is None:
        raise ValueError(runtime.title() + ' CLI is unavailable')
    path = Path(executable)
    if os.name == 'nt' and path.suffix.lower() in ('.cmd', '.bat'):
        # Do not interpolate user paths into cmd.exe. Resolve known npm layouts
        # to a real native executable or a Node program with literal argv.
        modules = path.parent / 'node_modules'
        if runtime == 'codex':
            targets = {'AMD64': 'x86_64-pc-windows-msvc', 'ARM64': 'aarch64-pc-windows-msvc'}
            target = targets.get(os.environ.get('PROCESSOR_ARCHITECTURE', '').upper())
            candidates = [modules / '@openai/codex/vendor' / str(target) / 'codex/codex.exe']
            if target:
                candidates.append(modules / ('@openai/codex-win32-' + ('arm64' if target.startswith('aarch64') else 'x64')) / 'vendor' / target / 'codex/codex.exe')
            for candidate in candidates:
                if candidate.is_file() and not candidate.is_symlink():
                    safe_read(candidate, 256 * 1024 * 1024)
                    return [str(candidate)]
        elif runtime == 'claude':
            script = modules / '@anthropic-ai/claude-code/cli.js'
            node = shutil.which('node.exe')
            if node and script.is_file() and not script.is_symlink():
                safe_read(script, 64 * 1024 * 1024)
                return [node, str(script)]
        raise ValueError('Native host executable unavailable; install the native CLI or a supported npm layout without a shell wrapper')
    return [str(path)]


def _verify_selected(runtime, payload, command):
    """Check the host's selection and actual cache, not just CLI exit status."""
    selector = 'wiki-atlassian@knowledge-documents'
    argv = [*command, 'plugin', 'list', '--json']
    if runtime == 'codex':
        argv += ['--marketplace', 'knowledge-documents']
    result = run_bounded(argv, cwd=ROOT, env=dict(os.environ), timeout=60, limit=2 * 1024 * 1024)
    try:
        if result.returncode:
            raise ValueError()
        data = json.loads(result.stdout.decode('utf-8'))
        pending, rows = [data], []
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                if value.get('pluginId' if runtime == 'codex' else 'id') == selector:
                    if runtime == 'codex' or value.get('scope') == 'user':
                        rows.append(value)
                pending.extend(v for v in value.values() if isinstance(v, (dict, list)))
        if len(rows) != 1 or rows[0].get('version') != payload.name or rows[0].get('enabled') is not True:
            raise ValueError()
        home = Path(os.environ.get('CODEX_HOME' if runtime == 'codex' else 'CLAUDE_CONFIG_DIR')
                    or Path.home() / ('.codex' if runtime == 'codex' else '.claude')).expanduser().absolute()
        parent = home / 'plugins/cache/knowledge-documents/wiki-atlassian'
        if runtime == 'codex':
            if rows[0].get('installed') is not True:
                raise ValueError()
            cache = parent / payload.name
        else:
            cache = Path(rows[0]['installPath'])
            if not cache.is_absolute() or cache.parent != parent or cache.name not in (payload.name, payload.name.replace('+', '-')):
                raise ValueError()
        if _inventory(cache, private=False) != _inventory(payload / 'plugin'):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError):
        raise ValueError('Host selection or cached Python payload differs from the prepared plugin; preserve existing versions and retry the update') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=('codex', 'claude'), action='append')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    try:
        payload = prepare(Path.home() / '.local/share/wiki-atlassian')
        runtimes = args.runtime or [name for name in ('codex', 'claude') if shutil.which(name)]
        if not runtimes and not args.prepare_only:
            raise ValueError('No supported host CLI found; use --prepare-only for a portable payload')
        failed = False
        with marketplace(payload) as source:
            if args.prepare_only:
                print(source)
                return 0
            for runtime in runtimes:
                try:
                    command = _host_command(runtime)
                    operations = [['plugin', 'marketplace', 'add', str(source)],
                                  ['plugin', 'add' if runtime == 'codex' else 'install', 'wiki-atlassian@knowledge-documents']]
                    if runtime == 'claude':
                        operations.append(['plugin', 'update', 'wiki-atlassian@knowledge-documents', '--scope', 'user'])
                    for argv in operations:
                        result = run_bounded([*command, *argv], cwd=ROOT, env=dict(os.environ), timeout=60, limit=2 * 1024 * 1024)
                        if result.returncode:
                            raise ValueError('Host CLI registration failed; existing payloads are preserved')
                    _verify_selected(runtime, payload, command)
                    print(runtime.title() + ': SUCCESS')
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    failed = True
                    print(runtime.title() + ': FAILED\n  Error: ' + (str(exc) if isinstance(exc, ValueError) else 'Host CLI could not complete registration'))
            if failed:
                raise RegistrationFailure()
        return int(failed)
    except RegistrationFailure:
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'Private payload operation failed; check file permissions and the selected interpreter'
        print('Wiki: FAILED\n  Error: ' + reason, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
