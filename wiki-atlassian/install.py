#!/usr/bin/env python3
"""Prepare and register the native Python wiki MCP without a build toolchain."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
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
from mise_python import select_python
from native_cli import host_command, owned_python_argv

PAYLOAD_DIRS = ('.claude-plugin', '.codex-plugin', 'skills', 'agents', 'scripts', 'wiki_atlassian')
SHARED_HELPERS = ('personal_feedback.py', 'document_destination.py', 'wiki_platform.py', '_wiki_windows.py', 'mise_python.py', 'native_cli.py')
VERSION = re.compile(r'0\.2\.0\+h[0-9a-f]{16}')
LEGACY_SOURCE = re.compile(r'\./packages/(0\.1\.0\+h[0-9a-f]{16})/plugin')
LEGACY_MAX_ENTRIES, LEGACY_MAX_BYTES = 1024, 128 * 1024 * 1024


class RegistrationFailure(Exception):
    """Per-host failures were already rendered without sensitive CLI output."""


def marketplace_bytes(relative):
    return (json.dumps({'name': 'knowledge-documents', 'owner': {'name': 'knowledge-documents'},
        'plugins': [{'name': 'wiki-atlassian', 'source': relative}]}, indent=2) + '\n').encode('utf-8')


def _io_path(path):
    value = os.path.abspath(path)
    if os.name != 'nt':
        return value
    if value.startswith(("\\\\?\\", "\\\\.\\")) or '..' in Path(path).parts:
        raise ValueError('Canonical ordinary paths required')
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _raise_walk(error):
    raise error


def _source_files():
    if os.name == 'nt':
        from wiki_platform import source_snapshot, trusted_source_directory
        result = {}
        for name in PAYLOAD_DIRS:
            directory = ROOT / name
            with trusted_source_directory(directory):
                for base, dirs, files in os.walk(_io_path(directory), followlinks=False, onerror=_raise_walk):
                    logical = directory / os.path.relpath(base, _io_path(directory))
                    dirs[:] = [part for part in dirs if part != '__pycache__']
                    for part in dirs:
                        with trusted_source_directory(logical / part):
                            pass
                    for part in files:
                        if not part.endswith(('.pyc', '.pyo')):
                            path = logical / part
                            result[path.relative_to(ROOT).as_posix()] = source_snapshot(path, 16 * 1024 * 1024)[0]
        required = ('main', 'models', 'config', 'remote', 'storage', 'markdown', 'replica',
                    'changes', 'search', 'service', 'multispace', 'protocol', 'tools', 'references', 'cursors', 'policy')
        if any('wiki_atlassian/' + name + '.py' not in result for name in required):
            raise ValueError('Python wiki source is incomplete; preserve the current installation')
        for name in ('.mcp.json', 'PROVENANCE.md', 'README.md', 'pyproject.toml'):
            result[name] = source_snapshot(ROOT / name, 1024 * 1024)[0]
        for name in SHARED_HELPERS:
            result['scripts/' + name] = source_snapshot(HELPERS / name, 1024 * 1024)[0]
        return result
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


def _inventory(root, private=True, *, legacy=False):
    if os.name == 'nt':
        from wiki_platform import metadata_tree_inventory, safe_metadata_snapshot
        if private:
            ensure_private_directory(root)
        rows = metadata_tree_inventory(root, max_entries=LEGACY_MAX_ENTRIES if legacy else 100000,
                                       max_bytes=LEGACY_MAX_BYTES if legacy else 256 * 1024 * 1024)
        result = {}
        for name, item in rows.items():
            if item['kind'] != 'file':
                continue
            limit = 64 * 1024 * 1024 if legacy and name == 'plugin/lib/wiki-atlassian.jar' else 16 * 1024 * 1024
            content, _ = safe_metadata_snapshot(Path(root) / name, limit)
            if hashlib.sha256(content).hexdigest() != item['sha256']:
                raise ValueError('Payload changed during inventory')
            result[name] = content
        return result
    def refuse_partial(error):
        raise error

    result = {}
    total, entries = 0, 0
    for base, dirs, files in os.walk(root, followlinks=False, onerror=refuse_partial if legacy else None):
        entries += len(dirs) + len(files)
        if legacy and entries > LEGACY_MAX_ENTRIES:
            raise ValueError('Legacy payload exceeds the supported inventory limit')
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
            name = path.relative_to(root).as_posix()
            limit = 64 * 1024 * 1024 if legacy and name == 'plugin/lib/wiki-atlassian.jar' else 16 * 1024 * 1024
            if legacy:
                limit = min(limit, LEGACY_MAX_BYTES - total)
            content = (safe_read(path, limit, private=True)
                if private else safe_metadata_read(path, limit))
            total += len(content)
            result[name] = content
    return result


def prepare(data_dir):
    """Publish one immutable payload under a symlink-free stable marketplace."""
    data_dir = Path(data_dir)
    if not data_dir.is_absolute() or '..' in data_dir.parts:
        raise ValueError('Plugin data directory must be an absolute canonical path')
    interpreter = select_python(require_yaml=True)
    parent = data_dir / 'marketplace/versions'
    ensure_private_directory(parent)
    files = _source_files()
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
    if os.name == 'nt':
        transport['mcpServers']['atlassian']['args'] = owned_python_argv(interpreter, destination / 'plugin/scripts/launch.py')[1:]
    files['.mcp.json'] = (json.dumps(transport, indent=2) + '\n').encode('utf-8')
    with file_lock(parent / 'publish.lock', blocking=False):
        if os.path.lexists(_io_path(destination)):
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
            if os.name == 'nt':
                from wiki_platform import metadata_rename_directory
                metadata_rename_directory(stage, destination)
            else:
                os.rename(stage, destination)
            ensure_private_directory(destination)
            if _inventory(destination / 'plugin') != files:
                raise ValueError('Published plugin payload changed; registration refused')
        finally:
            if os.path.lexists(_io_path(stage)):
                if os.name == 'nt':
                    from wiki_platform import metadata_tree_inventory, metadata_remove, metadata_remove_empty
                    rows = metadata_tree_inventory(stage, max_entries=100000, max_bytes=256 * 1024 * 1024)
                    for name, item in rows.items():
                        if item['kind'] == 'file':
                            metadata_remove(stage / name, expected_sha256=item['sha256'], max_bytes=256 * 1024 * 1024)
                    for name in sorted((name for name, item in rows.items() if item['kind'] == 'dir'), key=lambda name: name.count('/'), reverse=True):
                        metadata_remove_empty(stage / name)
                    metadata_remove_empty(stage)
                else:
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
                legacy = LEGACY_SOURCE.fullmatch(selected) if isinstance(selected, str) else None
                if legacy:
                    _legacy_selection(root, old, selected, legacy[1])
                elif not isinstance(selected, str) or not re.fullmatch(r'\./versions/0\.2\.0\+h[0-9a-f]{16}/plugin', selected):
                    raise ValueError('Existing marketplace selection is not a managed payload')
        atomic_write(path, marketplace_bytes('./versions/' + payload.name + '/plugin'))
        try:
            yield root
        except BaseException:
            # Never erase either payload. Restore only the exact selection we own.
            if safe_read(path, 65536, private=True) == marketplace_bytes('./versions/' + payload.name + '/plugin'):
                if old is None:
                    if os.name == 'nt':
                        from wiki_platform import metadata_remove
                        metadata_remove(path, expected_sha256=hashlib.sha256(marketplace_bytes('./versions/' + payload.name + '/plugin')).hexdigest(), max_bytes=65536)
                    else:
                        path.unlink()
                else:
                    atomic_write(path, old)
            raise


def _legacy_selection(root, metadata, selected, version):
    """Recognize the nonlinked Windows 0.1 layout without running its payload."""
    error = 'Existing legacy marketplace payload differs; preserve it and inspect'
    if metadata != marketplace_bytes(selected):
        raise ValueError(error)
    package = root / 'packages' / version
    # Reading the complete tree checks custody, reparse points, hardlinks and
    # foreign-write access, including files we retain but never execute.
    with trusted_metadata_directory(package):
        for name in ('.claude-plugin', '.codex-plugin', 'skills', 'agents', 'scripts', 'lib'):
            with trusted_metadata_directory(package / 'plugin' / name):
                pass
        # A historical fat JAR can exceed the Python payload's per-file limit.
        files = _inventory(package, private=False, legacy=True)
    if files.get('.claude-plugin/marketplace.json') != marketplace_bytes('./plugin'):
        raise ValueError(error)
    for name in ('scripts/launch.py', 'lib/wiki-atlassian.jar', 'README.md', 'PROVENANCE.md'):
        if not files.get('plugin/' + name):
            raise ValueError(error)
    description = 'Shared Atlassian wiki knowledge and native Confluence document operations, independent of project workflows.'
    expected = {'name': 'wiki-atlassian', 'version': version, 'description': description}
    for directory in ('.claude-plugin', '.codex-plugin'):
        manifest = dict(expected)
        if directory == '.codex-plugin':
            manifest['skills'] = './skills/'
        manifest['mcpServers'] = './.mcp.json'
        content = (json.dumps(manifest, indent=2) + '\n').encode()
        if files.get('plugin/' + directory + '/plugin.json', b'').replace(b'\r\n', b'\n') != content:
            raise ValueError(error)
    raw = files.get('plugin/.mcp.json', b'')
    transport = json.loads(raw)
    servers = transport.get('mcpServers') if isinstance(transport, dict) else None
    server = servers.get('wiki_atlassian') if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise ValueError(error)
    command = server.get('command')
    executable = PureWindowsPath(command) if isinstance(command, str) else PureWindowsPath()
    if (not executable.is_absolute() or executable.name.lower() != 'java.exe' or
            '..' in executable.parts or '\x00' in str(executable) or
            str(executable).startswith(('\\\\?\\', '\\\\.\\'))):
        raise ValueError(error)
    expected = {'mcpServers': {'wiki_atlassian': {'command': command,
        'args': ['-jar', str(package / 'plugin/lib/wiki-atlassian.jar')],
        'env_vars': ['HOME', 'JAVA_HOME', 'WIKI_ATLASSIAN_JAVA', 'ATLASSIAN_EMAIL',
                     'ATLASSIAN_API_TOKEN', 'ATLASSIAN_BASE_URL', 'SystemRoot']}}}
    if raw.replace(b'\r\n', b'\n') != (json.dumps(expected, indent=2) + '\n').encode():
        raise ValueError(error)


def _host_command(runtime):
    return host_command(runtime)


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


def _failure_reason(exc, generic):
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, OSError):
        operation = getattr(exc, '_wiki_operation', None)
        code = getattr(exc, '_wiki_native_code', None)
        if type(operation) is str and operation == 'FileRenameInfoEx' and type(code) is int and 0 <= code <= 0xFFFFFFFF:
            return f'FileRenameInfoEx publication failed; native Windows code {code}'
    return generic


def main():
    if os.name == "nt":
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=('codex', 'claude'), action='append')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    try:
        runtimes = args.runtime or [name for name in ('codex', 'claude') if shutil.which(name)]
        if not runtimes and not args.prepare_only:
            raise ValueError('No supported host CLI found; use --prepare-only for a portable payload')
        commands = {} if args.prepare_only else {runtime: _host_command(runtime) for runtime in runtimes}
        payload = prepare(Path.home() / '.local/share/wiki-atlassian')
        failed = False
        with marketplace(payload) as source:
            if args.prepare_only:
                print(source)
                return 0
            for runtime in runtimes:
                try:
                    command = commands[runtime]
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
                    print(runtime.title() + ': FAILED\n  Error: ' + _failure_reason(exc, 'Host CLI could not complete registration'))
            if failed:
                raise RegistrationFailure()
        return int(failed)
    except RegistrationFailure:
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        reason = _failure_reason(exc, 'Private payload operation failed; check file permissions and the selected interpreter')
        print('Wiki: FAILED\n  Error: ' + reason, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
