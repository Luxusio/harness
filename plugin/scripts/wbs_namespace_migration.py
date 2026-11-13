"""Explicit import of retired execution namespaces into the one WBS namespace.

Only the MCP migration owner writes. Original payloads remain historical;
current authority is published once and the old control is retired last.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import goal_migration as storage

ARCHIVE = 'doc/harness/archive/wbs-namespace-migration'
MANIFEST = ARCHIVE + '/manifest.json'
HISTORY = ARCHIVE + '/journals'
MAX_JOURNALS = 32
OLD_ROOT = 'doc/harness/tasks'
NEW_ROOT = 'doc/harness/wbs'
OLD_ID = re.compile(r'TASK__[A-Za-z0-9_.-]{1,180}\Z')
MAX_FILES = 32768
MAX_BYTES = 8 * 1024 * 1024 * 1024
MAX_MANIFEST = 16 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError('WBS namespace migration: ' + message)


def _new_id(value):
    require(isinstance(value, str) and OLD_ID.fullmatch(value), 'invalid retired execution identity')
    return 'WBS__' + value[6:]


def _link_at(parent, name, relative):
    """Capture literal historical link bytes; the target is never opened."""
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    require(stat.S_ISLNK(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
            and before.st_size <= 4096, 'unsafe historical link: ' + relative)
    target = os.readlink(os.fsencode(name), dir_fd=parent)
    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
    require(identity(before) == identity(after) and len(target) == before.st_size,
            'historical link changed while reading: ' + relative)
    return dict(link=base64.b64encode(target).decode(), sha256=hashlib.sha256(target).hexdigest(),
                size=len(target), mode=stat.S_IMODE(before.st_mode))


def _link_file(repo, relative):
    if os.name == "nt":
        raise ValueError('WBS namespace migration: native historical reparse links are unsupported; original payload is preserved: ' + relative)
    parts = storage._parts(relative)
    with storage._directory(repo, parts[:-1]) as (parent, bound):
        result = _link_at(parent, parts[-1], relative)
        bound()
        return result


def _hash_at(parent, name, relative, *, destination=None, publication=None, descriptor=None):
    """Stream a sibling through an already pinned no-follow directory."""
    fd = os.dup(descriptor) if descriptor is not None else os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        before = os.fstat(fd)
        paired = bool(publication and before.st_nlink == 2 and storage._same(before, os.stat(publication, dir_fd=parent, follow_symlinks=False)))
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and (before.st_nlink == 1 or paired) and not before.st_mode & 0o7022, 'unsafe payload: ' + relative)
        digest, count = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            count += len(chunk)
            require(count <= MAX_BYTES, 'payload byte budget exceeded: ' + relative)
            digest.update(chunk)
            if destination is not None:
                view = memoryview(chunk)
                while view:
                    written = os.write(destination, view)
                    require(written > 0, 'short payload copy')
                    view = view[written:]
        after = os.fstat(fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
        require(identity(before) == identity(after) and storage._same(after, os.stat(name, dir_fd=parent, follow_symlinks=False)),
                'payload changed while reading: ' + relative)
        if paired:
            require(storage._same(after, os.stat(publication, dir_fd=parent, follow_symlinks=False)), 'payload publication alias changed: ' + relative)
        return dict(sha256=digest.hexdigest(), size=count, mode=stat.S_IMODE(before.st_mode))
    finally:
        os.close(fd)


def _hash_file(repo, relative, *, destination=None):
    parts = storage._parts(relative)
    if os.name == "nt":
        require(destination is None, 'native payload hashing cannot write a POSIX destination descriptor')
        from wiki_platform import _native
        with storage._directory(repo, parts[:-1]) as (parent, bound):
            path = parent / parts[-1]
            handle = _native._open(path, _native.GENERIC_READ, share=_native.FILE_SHARE_READ)
            try:
                before = _native._metadata_stamp(handle)
                require(before[3] <= MAX_BYTES, 'payload byte budget exceeded: ' + relative)
                digest, count = hashlib.sha256(), 0
                for chunk in _native._file_chunks(handle, MAX_BYTES, chunk_size=65536):
                    count += len(chunk)
                    digest.update(chunk)
                require(count == before[3] and _native._metadata_stamp(handle) == before,
                        'payload changed while reading: ' + relative)
                named = _native._open(path, 0)
                try:
                    require(_native._metadata_stamp(named) == before,
                            'payload name changed while reading: ' + relative)
                finally:
                    _native._close(named)
                bound()
                return dict(sha256=digest.hexdigest(), size=count,
                            mode=0o444 if before[4] & 1 else 0o600)
            finally:
                _native._close(handle)
    with storage._directory(repo, parts[:-1]) as (parent, bound):
        result = _hash_at(parent, parts[-1], relative, destination=destination)
        bound()
        return result


def _tree(repo, relative):
    """Pin each directory once, then verify its binding after all siblings."""
    if os.name == "nt":
        from wiki_platform import metadata_tree_inventory
        inventory = metadata_tree_inventory(Path(repo).joinpath(*storage._parts(relative)),
                                            max_entries=MAX_FILES, max_bytes=MAX_BYTES, allow_control_lock=True)
        entries, siblings = {}, {}
        for name, row in inventory.items():
            parts = storage._parts(name)
            require(len(parts) <= 128, 'payload directory-depth budget exceeded: ' + name)
            parent = tuple(parts[:-1])
            siblings[parent] = siblings.get(parent, 0) + 1
            require(siblings[parent] <= 4096, 'directory-entry budget exceeded: ' + name)
            if name == '.control.lock':
                # Inventory already checks owner/DACL/type/link count and
                # charges this runtime lock against the aggregate budgets.
                require(row['kind'] == 'file' and row['size'] == 0
                        and row['sha256'] == hashlib.sha256(b'').hexdigest(),
                        'unsafe execution coordination lock: ' + relative)
                continue
            readonly = row['readonly']
            if row['kind'] == 'dir':
                entries[name] = dict(directory=True, mode=0o555 if readonly else 0o700)
            else:
                entries[name] = dict(sha256=row['sha256'], size=row['size'], mode=0o444 if readonly else 0o600)
        return entries
    entries, total = {}, 0
    with storage._directory(repo, storage._parts(relative)) as (root, bound):
        def frame(fd, path, parent=None, name=None):
            before = os.fstat(fd)
            require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid() and not before.st_mode & 0o7022,
                    'unsafe payload directory: ' + path)
            names = sorted(os.listdir(fd))
            require(len(names) <= 4096, 'directory-entry budget exceeded: ' + path)
            return dict(fd=fd, path=path, parent=parent, name=name, before=before, names=names, remaining=iter(names))
        frames = [frame(root, relative)]
        try:
            while frames:
                current = frames[-1]
                name = next(current['remaining'], None)
                if name is None:
                    after = os.fstat(current['fd'])
                    before = current['before']
                    require((before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_mtime_ns, before.st_ctime_ns)
                            == (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_mtime_ns, after.st_ctime_ns)
                            and sorted(os.listdir(current['fd'])) == current['names'], 'payload directory changed: ' + current['path'])
                    if current['parent'] is not None:
                        require(storage._same(after, os.stat(current['name'], dir_fd=current['parent'], follow_symlinks=False)),
                                'payload directory rebound: ' + current['path'])
                        os.close(current['fd'])
                    frames.pop()
                    continue
                require(name not in ('.', '..') and '/' not in name, 'invalid payload name')
                path = current['path'] + '/' + name
                key = path[len(relative) + 1:]
                require(len(entries) < MAX_FILES, 'payload file-count budget exceeded: ' + relative)
                info = os.stat(name, dir_fd=current['fd'], follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    require(len(frames) < 128, 'payload directory-depth budget exceeded: ' + path)
                    child = os.open(name, storage._DIR_FLAGS, dir_fd=current['fd'])
                    try:
                        require(storage._same(info, os.fstat(child)), 'payload directory rebound: ' + path)
                        next_frame = frame(child, path, current['fd'], name)
                    except BaseException:
                        os.close(child)
                        raise
                    entries[key] = dict(directory=True, mode=stat.S_IMODE(info.st_mode))
                    frames.append(next_frame)
                elif stat.S_ISLNK(info.st_mode):
                    require(key.startswith('work/'), 'link outside historical work payload: ' + path)
                    entries[key] = _link_at(current['fd'], name, path)
                    total += entries[key]['size']
                    require(total <= MAX_BYTES, 'payload byte budget exceeded: ' + relative)
                else:
                    entries[key] = _hash_at(current['fd'], name, path)
                    total += entries[key]['size']
                    require(total <= MAX_BYTES, 'payload byte budget exceeded: ' + relative)
            bound()
        finally:
            for current in reversed(frames):
                if current['parent'] is not None:
                    os.close(current['fd'])
    return entries


def _project_roots(repo):
    from codex_hook_registration import workspace_roots
    from _lib import is_harness_enabled_repo
    found = workspace_roots(str(repo))
    require(found and str(repo) in found, 'worktree discovery unavailable; restore the registered checkout identity')
    if found[0] != str(repo):
        require(not is_harness_enabled_repo(found[0]), 'migrate from the surviving initialized common repository root')
        return [str(repo)]
    return found


def _common_git_root(repo):
    from codex_hook_registration import workspace_roots
    found = workspace_roots(str(repo))
    require(found and str(repo) in found, 'common Git directory discovery unavailable')
    return Path(found[0])


@contextmanager
def _change_workspace(repo, change):
    if change['source'] != '.git/info/exclude':
        with storage._control_workspace(repo, change['root']) as root:
            yield root
        return
    common = _common_git_root(repo)
    require(change['root'] == str(common), 'Git-local ignore destination changed')
    if os.name == "nt":
        from wiki_platform import trusted_metadata_directory
        with trusted_metadata_directory(common):
            yield common
        return
    token = storage._ROOT.set(None)
    fd, pinned = None, None
    try:
        fd = storage._root_fd(common)
        pinned = storage._ROOT.set((str(common), fd))
        yield common
    finally:
        if pinned is not None:
            storage._ROOT.reset(pinned)
        if fd is not None:
            os.close(fd)
        storage._ROOT.reset(token)


def _has_git(repo):
    metadata = Path(repo) / '.git'
    # Setup also supports manifest-only projects and an empty .git boundary
    # sentinel. Ignore scaffolding must not launch Git for those placeholders.
    return metadata.is_file() or metadata.is_dir() and os.path.lexists(metadata / 'HEAD')


def _runtime_ignored(repo, paths=()):
    from _lib import _trusted_git_env, _git_executable
    paths = sorted({b'doc/harness/wbs/.harness-runtime-probe', b'doc/harness/archive/wbs-namespace-migration/.harness-runtime-probe', *(os.fsencode(path) for path in paths)})
    require(len(paths) <= MAX_FILES + 4096 and sum(len(path) for path in paths) <= MAX_MANIFEST, 'planned runtime ignore path budget exceeded')
    environment = _trusted_git_env()
    result = subprocess.run([_git_executable(repo, environment), '-C', str(repo), 'check-ignore', '--stdin', '-z'], input=b'\0'.join(paths) + b'\0',
                            capture_output=True, timeout=10, env=environment)
    require(result.returncode in (0, 1), 'cannot inspect Git ignore policy at ' + str(repo) + ': ' + result.stderr[:1000].decode(errors='replace'))
    return set(result.stdout.split(b'\0')) - {b''} == set(paths)


def _require_runtime_ignored(repo, paths=()):
    if _has_git(repo):
        require(_runtime_ignored(repo, paths), 'runtime metadata remains visible to Git in ' + str(repo)
                + '; project ignore rules override local excludes. Update project ignore rules for /doc/harness/wbs/ and /doc/harness/archive/wbs-namespace-migration/, then retry.')


def ensure_runtime_ignored(repo, *, paths=()):
    """Owner-only scaffold repair; no import, source edits or new state ledger."""
    from _lib import _trusted_control_writer
    if not _trusted_control_writer():
        raise PermissionError('runtime ignore update requires the WBS-control MCP owner')
    repo = Path(repo).absolute()
    if not _has_git(repo) or _runtime_ignored(repo, paths):
        return False
    from codex_hook_registration import workspace_roots
    roots = workspace_roots(str(repo))
    require(roots and str(repo) in roots, 'runtime ignore update cannot resolve the registered common Git directory')
    common = Path(roots[0])
    token = storage._ROOT.set(None)
    try:
        path = '.git/info/exclude'
        try:
            with storage._locked_directory(common, ['.git', 'info'], create=True, blocking=True) as (directory, bound):
                before = storage._read(common, path)
                after = _exclude_bytes(before)
                if after != before:
                    storage._write(common, path, after, expected=storage._hash(before))
                bound()
                _require_runtime_ignored(repo, paths)
                return after != before
        except (OSError, ValueError) as exc:
            if 'runtime metadata remains visible to Git' in str(exc):
                raise
            raise ValueError('WBS namespace migration: unsafe Git-local ignore path ' + str(common / path) + '; secure that metadata or explicitly ignore /doc/harness/wbs/ and /doc/harness/archive/wbs-namespace-migration/ in the project .gitignore') from exc
    finally:
        storage._ROOT.reset(token)


def _exclude_bytes(before):
    rules = [b'/doc/harness/wbs/', b'/doc/harness/archive/wbs-namespace-migration/']
    if before is not None and before.splitlines()[-len(rules):] == rules:
        return before
    prefix = before or b''
    return prefix + (b'\n' if prefix and not prefix.endswith(b'\n') else b'') + b'\n'.join(rules) + b'\n'


def _encoded(value):
    return base64.b64encode(storage._encode(value)).decode()


def _transform_identity(value, mappings):
    """Transform only owned control structures, never arbitrary evidence files."""
    if isinstance(value, list):
        return [_transform_identity(item, mappings) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    keys = {'task_id': 'wbs_id', 'task_dir': 'wbs_dir', 'owner_task_id': 'owner_wbs_id', 'task_status': 'wbs_status'}
    for key, item in value.items():
        new_key = keys.get(key, key)
        require(new_key not in result, 'mixed execution namespace in control structure')
        if key in ('task_id', 'owner_task_id') and item is not None:
            item = _new_id(item)
        elif key == 'task_dir' and item is not None:
            require(item in mappings, 'binding references an unavailable retired root: ' + str(item))
            item = mappings[item]
        elif key == 'archived' and isinstance(item, str):
            item = mappings.get(item, item)
        else:
            item = _transform_identity(item, mappings)
        result[new_key] = item
    return result


def _binding_root(binding, roots):
    if not isinstance(binding, dict) or not isinstance(binding.get('task_id'), str) or not OLD_ID.fullmatch(binding['task_id']):
        return None
    return next((root for root in roots if binding.get('task_dir') == str(Path(root) / OLD_ROOT / binding['task_id'])), None)


def _live_binding(repo, binding, roots):
    root = _binding_root(binding, roots)
    if root is None:
        return None
    with storage._control_workspace(repo, root) as source:
        path = OLD_ROOT + '/' + binding['task_id']
        raw = storage._read(source, path + '/TASK.json', 16384)
        control = storage._migration_control(storage._control_json(raw, source / path / 'TASK.json')) if raw else {}
        blocked = os.path.lexists(source / path / 'BLOCKED.md')
        if control and not control['closed'] and not blocked and binding.get('run_id') == control['run_id']:
            return root
    return None


def _marker(repo, root, name, raw, roots):
    from _lib import sanitize_session_id
    if not name.endswith('.json') or name == 'default.json' or sanitize_session_id(name[:-5]) != name[:-5]:
        return 'historical', None
    try:
        value = storage._json(raw)
    except ValueError as exc:
        raise ValueError('WBS namespace migration: invalid exact session marker ' + str(Path(root) / OLD_ROOT / '.active_sessions' / name)) from exc
    if not isinstance(value, dict):
        return 'historical', None
    fence = 'conflicts' in value or value.get('conflicts_overflow') is True
    if value.get('session_id') != name[:-5]:
        require(not fence, 'unresolved conflict fence has invalid session identity: ' + str(Path(root) / OLD_ROOT / '.active_sessions' / name))
        return 'historical', None
    return ('fence' if fence else 'active' if _live_binding(repo, value, roots) else 'historical'), value


def _marker_transform(value, mappings, roots):
    aliases = dict(mappings)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            if 'task_dir' in item:
                root = _binding_root(item, roots)
                require(root is not None, 'conflict/session reference is outside available registered execution roots: ' + str(item.get('task_dir')))
                aliases[item['task_dir']] = str(Path(root) / NEW_ROOT / _new_id(item['task_id']))
            pending.extend(item.values())
    return _transform_identity(value, aliases)


def _validate_entry(name, row):
    require(isinstance(row, dict) and type(row.get('mode')) is int and 0 <= row['mode'] <= 0o777,
            'invalid payload entry mode')
    if 'link' in row:
        require(name.startswith('work/') and set(row) == {'link', 'sha256', 'size', 'mode'}, 'invalid historical link scope')
        target = base64.b64decode(row['link'], validate=True)
        require(0 < len(target) <= 4096 and row['size'] == len(target)
                and row['sha256'] == hashlib.sha256(target).hexdigest(), 'invalid historical link descriptor')
    elif row.get('directory') is True:
        require(set(row) == {'directory', 'mode'} and not row['mode'] & 0o022, 'invalid directory entry')
    else:
        require(set(row) == {'sha256', 'size', 'mode'} and isinstance(row['sha256'], str)
                and re.fullmatch('[0-9a-f]{64}', row['sha256']) and type(row['size']) is int
                and 0 <= row['size'] <= MAX_BYTES and not row['mode'] & 0o022, 'invalid regular payload entry')


def _archive_prefix(journal):
    return journal.get('archive_prefix', ARCHIVE + '/original')


def journals(repo):
    """Explicit import/disposal lookup: current first, immutable history next."""
    repo = Path(repo).absolute()
    current = _journal(repo)
    if current is not None:
        yield MANIFEST, current
    entries = storage._entries(repo, HISTORY) or []
    require(len(entries) <= MAX_JOURNALS, 'historical journal budget exceeded')
    for name, _ in entries:
        require(re.fullmatch('[0-9a-f]{64}\\.json', name), 'invalid historical journal name')
        relative = HISTORY + '/' + name
        yield relative, _journal(repo, relative)


def _next_plan(repo, current):
    if current is None:
        return _plan(repo)
    if current['status'] != 'complete':
        return current
    raw = storage._read_publication(repo, MANIFEST, MAX_MANIFEST)
    prefix = ARCHIVE + '/epochs/' + hashlib.sha256(raw).hexdigest() + '/original'
    proposed = _plan(repo, archive_prefix=prefix)
    return current if proposed['status'] == 'complete' else proposed


def _journal(repo, relative=MANIFEST):
    raw = storage._read_publication(repo, relative, MAX_MANIFEST)
    if raw is None:
        return None
    if relative != MANIFEST:
        require(re.fullmatch(re.escape(HISTORY) + r'/[0-9a-f]{64}\.json', relative)
                and Path(relative).stem == hashlib.sha256(raw).hexdigest(), 'historical journal digest mismatch')
    data = storage._json(raw)
    require(isinstance(data, dict) and set(data) - {'archive_prefix'} == {'version', 'status', 'trees', 'changes', 'retained'}
            and data['version'] == 1 and data['status'] in ('prepared', 'complete')
            and isinstance(data['trees'], list) and isinstance(data['changes'], list) and isinstance(data['retained'], list), 'invalid migration manifest')
    if relative != MANIFEST:
        require(data['status'] == 'complete', 'historical journal is not complete')
    prefix = _archive_prefix(data)
    require(prefix == ARCHIVE + '/original' or re.fullmatch(re.escape(ARCHIVE) + r'/epochs/[0-9a-f]{64}/original', prefix), 'invalid original archive prefix')
    require(len(data['trees']) <= 256 and len(data['changes']) <= 4096, 'manifest record budget exceeded')
    roots = {str(repo)}
    from _lib import resolve_registered_worktree
    for tree in data['trees']:
        require(isinstance(tree, dict) and set(tree) == {'root', 'source', 'destination', 'entries', 'replacement'}, 'invalid tree manifest')
        root = tree['root']
        require(isinstance(root, str) and root == str(Path(root).absolute())
                and (root == str(repo) or data['status'] == 'complete' or resolve_registered_worktree(str(repo), root) == root), 'unregistered migration worktree')
        roots.add(root)
        require(re.fullmatch(r'doc/harness/(?:tasks|archive/batch)/TASK__[A-Za-z0-9_.-]{1,180}', tree['source'])
                and tree['destination'] == tree['source'].replace('doc/harness/tasks/TASK__', NEW_ROOT + '/WBS__', 1).replace('doc/harness/archive/batch/TASK__', 'doc/harness/archive/batch/WBS__', 1), 'invalid tree relocation')
        require(isinstance(tree['entries'], dict) and len(tree['entries']) <= MAX_FILES
                and isinstance(tree['replacement'], dict) and set(tree['replacement']) <= {'WBS.json', 'WBS.control.json'}, 'invalid payload manifest')
        for name, row in tree['entries'].items():
            storage._parts(name)
            _validate_entry(name, row)
        require('TASK.json' in tree['entries'] or 'WBS.json' in tree['entries'], 'relocation has no original authority or intent')
        for name, text in tree['replacement'].items():
            decoded = base64.b64decode(text, validate=True)
            require(len(decoded) <= storage.LIMIT, 'oversized replacement')
            storage._json(decoded)
    seen_sources, seen_destinations = set(), set()
    for tree in data['trees']:
        for seen, name in ((seen_sources, tree['source']), (seen_destinations, tree['destination'])):
            key = (tree['root'], name)
            require(key not in seen, 'duplicate tree ownership')
            seen.add(key)
    for change in data['changes']:
        require(isinstance(change, dict) and set(change) == {'root', 'source', 'destination', 'before', 'after'}
                and isinstance(change['root'], str)
                and (change['root'] in roots or data['status'] == 'complete'
                     or change['source'] == '.git/info/exclude' and change['root'] == str(_common_git_root(repo))
                     or resolve_registered_worktree(str(repo), change['root']) == change['root']), 'invalid control change')
        source, dest = change['source'], change['destination']
        require(isinstance(source, str) and isinstance(dest, str)
                and (source == '.git/info/exclude' and dest == source
                     or re.fullmatch(r'doc/harness/runtime/batches/[A-Za-z0-9_.-]+\.json', source) and dest == source
                     or re.fullmatch(r'doc/harness/tasks/\.active_sessions/[A-Za-z0-9_.-]+\.json', source)
                     and dest == source.replace(OLD_ROOT, NEW_ROOT, 1)
                     or (re.fullmatch(r'doc/harness/runtime/batches/[A-Za-z0-9_.-]+\.json', source)
                         or re.fullmatch(r'doc/harness/tasks/TASK__[A-Za-z0-9_.-]{1,180}/TASK\.json', source)
                         or source == OLD_ROOT + '/.active' or source.startswith(OLD_ROOT + '/.active_sessions/')
                         and len(storage._parts(source)) == 5) and dest == prefix + '/' + source), 'invalid control relocation')
        for key in ('before', 'after'):
            if key == 'before' and source == '.git/info/exclude' and change[key] is None:
                continue
            raw = base64.b64decode(change[key], validate=True)
            require(len(raw) <= storage.LIMIT, 'oversized control change')
            if not dest.startswith(prefix + '/') and change['source'] != '.git/info/exclude':
                storage._json(raw)
        if dest.startswith(prefix + '/'):
            require(change['before'] == change['after'], 'legacy focus cannot grant authority')
    if data['status'] != 'complete':
        _validate_semantics(repo, data)
    return data


def _validate_semantics(repo, data):
    prefix = _archive_prefix(data)
    mappings = {str(Path(t['root']) / t['source']): str(Path(t['root']) / t['destination']) for t in data['trees']}
    archive_hashes = {}
    for tree in data['trees']:
        with storage._control_workspace(repo, tree['root']) as source:
            relative = tree['source'] + '/TASK.json'
            raw = storage._read(source, relative, 16384)
            control, expected = None, {}
            if 'TASK.json' in tree['entries']:
                if raw is None:
                    raw = storage._read_publication(source, prefix + '/' + relative, 16384)
                require(raw is not None and storage._hash(raw) == tree['entries']['TASK.json']['sha256'], 'original authority changed')
                original = storage._json(raw)
                control = storage._migration_control(original)
                require(control, 'invalid original authority')
                expected = {} if original == control else {'WBS.control.json': _encoded(control)}
            else:
                require(raw is None, 'intent-only root unexpectedly acquired authority')
            raw_state = storage._read(source, tree['source'] + '/WBS.json')
            if raw_state is not None:
                state = storage._json(raw_state)
                if control:
                    require(state.get('run_id') == control['run_id'], 'state and authority run differ')
                else:
                    _require_intent(state, tree['source'])
                converted = copy.deepcopy(state)
                for field in ('batch', 'batch_history'):
                    if field in converted:
                        converted[field] = _transform_identity(converted[field], mappings)
                if converted != state:
                    expected['WBS.json'] = _encoded(converted)
            else:
                require(control is not None, 'intent-only root lost its WBS state')
            require(tree['replacement'] == expected, 'journal changes unrelated authority or evidence')
        if '/archive/batch/' in tree['source']:
            entries = {name: None if row.get('directory') else row['sha256'] for name, row in _destination_entries(tree).items()}
            archive_hashes[str(Path(tree['root']) / tree['destination'])] = 'sha256:' + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    retired_pool_changes = [change for change in data['changes'] if change['source'].startswith('doc/harness/runtime/batches/') and change['destination'].startswith(prefix + '/')]
    if retired_pool_changes:
        from codex_hook_registration import workspace_roots
        needed_batches = _batch_references(repo, _project_roots(repo), archive_prefix=prefix)
    for change in data['changes']:
        if change['destination'].startswith(prefix + '/'):
            if change['source'].startswith('doc/harness/runtime/batches/'):
                pool = storage._json(base64.b64decode(change['before'], validate=True))
                require(not _pool_needs_owner(repo, pool) and pool.get('batch_id') not in needed_batches, 'historical pool acquired current references or live resources')
            elif re.fullmatch(r'doc/harness/tasks/TASK__[A-Za-z0-9_.-]{1,180}/TASK\.json', change['source']):
                original = storage._json(base64.b64decode(change['before'], validate=True))
                control = storage._migration_control(original)
                require(bool(control and control['closed']) or _historical(original), 'cannot retire current open authority as detached history')
            continue
        if change['source'] == '.git/info/exclude':
            before = base64.b64decode(change['before'], validate=True) if change['before'] is not None else None
            require(base64.b64decode(change['after'], validate=True) == _exclude_bytes(before), 'journal changes unrelated Git excludes')
            continue
        before = storage._json(base64.b64decode(change['before'], validate=True))
        if '/runtime/batches/' in change['source']:
            expected = _transform_identity(storage._without_close_fingerprint(before), mappings)
        else:
            from codex_hook_registration import workspace_roots
            expected = _marker_transform(before, mappings, _project_roots(repo))
        if '/runtime/batches/' in change['source']:
            require(all(item.get('status') not in ('running', 'reserved') for item in before.get('requests', [])), 'live worker in prepared migration')
            for item in expected['requests']:
                checkpoint = item.get('checkpoint') or {}
                archived = checkpoint.get('harvest', {}).get('archived')
                if archived in archive_hashes:
                    checkpoint['archive_fingerprint'] = archive_hashes[archived]
            import batch_state
            batch_state.validate(expected, str(repo), Path(change['source']).stem)
        else:
            require(before.get('session_id') == Path(change['source']).stem, 'session identity differs from marker')
        require(storage._json(base64.b64decode(change['after'], validate=True)) == expected, 'journal changes unrelated execution control')


def _require_intent(state, path):
    import wbs
    require(isinstance(state, dict) and state.get('pack_only') is True and state.get('run_id') == ''
            and state.get('nodes') == [] and state.get('checks') == {} and bool(state.get('batch')),
            'referenced control-less root is not unstarted work-pack intent: ' + path)
    wbs.validate_state(state)


def _historical(data):
    return storage._historical_control(data)


def _pool_needs_owner(repo, pool):
    """A disposed historical pool's pointer is not current parent authority."""
    items = pool.get('requests')
    if pool.get('status') != 'closed' or pool.get('halted') is not False or not items:
        return True
    if any(item.get('status') != 'integrated' for item in items):
        return True
    import batch_state
    try:
        for item in items:
            batch_state.disposal_complete(str(repo), item)
    except Exception:
        # An uncertain resource observation never releases the owner reference.
        return True
    return False


def _batch_references(repo, roots, required=(), *, archive_prefix=None):
    """Read only declared live/required graph metadata, never past payloads."""
    references, needed, intake_owners = set(required), set(), set()
    for name, _ in storage._entries(repo, 'doc/harness/runtime/batches') or []:
        if not name.endswith('.json'):
            continue
        pool = storage._json(storage._read(repo, 'doc/harness/runtime/batches/' + name))
        owner = pool.get('owner_task_id') or pool.get('owner_wbs_id')
        if isinstance(owner, str):
            old_id = 'TASK__' + owner[5:] if owner.startswith('WBS__') else owner
            intake_owners.add((str(repo), old_id))
            if _pool_needs_owner(repo, pool):
                references.add((str(repo), old_id))
    for name, _ in storage._entries(repo, 'doc/harness/goals') or []:
        if not name.endswith('.json'):
            continue
        goal = storage._json(storage._read(repo, 'doc/harness/goals/' + name))
        for child in goal.get('tasks', []):
            if not isinstance(child, dict):
                continue
            owner = child.get('task_id') or child.get('wbs_id')
            if isinstance(owner, str):
                references.add((str(repo), 'TASK__' + owner[5:] if owner.startswith('WBS__') else owner))
            spec = child.get('batch')
            if isinstance(spec, dict) and isinstance(spec.get('batch_id'), str):
                needed.add(spec['batch_id'])
    for root in roots:
        with storage._control_workspace(repo, root) as source:
            for namespace, prefix, filename in ((OLD_ROOT, 'TASK__', 'TASK.json'), (NEW_ROOT, 'WBS__', 'WBS.control.json')):
                for name, info in storage._entries(source, namespace) or []:
                    if not name.startswith(prefix) or not stat.S_ISDIR(info.st_mode):
                        continue
                    directory = namespace + '/' + name
                    path = directory + '/' + filename
                    raw = storage._read(source, path, 16384) if os.path.lexists(source / path) else None
                    control = storage._migration_control(storage._control_json(raw, source / path)) if raw is not None else {}
                    required_owner = (root, 'TASK__' + name[len(prefix):]) in references
                    if control and control['closed'] and not required_owner:
                        continue
                    if namespace == OLD_ROOT and not control and not required_owner:
                        if raw is not None or (root, name) not in intake_owners:
                            continue
                        archived_control = storage._read(source, (archive_prefix or ARCHIVE + '/original') + '/' + path, 16384)
                        if archived_control is not None:
                            continue  # Missing retired control must not revive old intent.
                    state_path = directory + '/WBS.json'
                    raw_state = storage._read(source, state_path) if os.path.lexists(source / state_path) else None
                    if raw_state is None:
                        continue
                    state = storage._json(raw_state)
                    intent = isinstance(state, dict) and state.get('pack_only') is True and state.get('run_id') == ''
                    if not (control and not control['closed'] or required_owner or intent):
                        continue
                    require(isinstance(state, dict), 'invalid current WBS metadata: ' + str(source / state_path))
                    history = state.get('batch_history', [])
                    require(isinstance(history, list) and len(history) <= 32, 'invalid current WBS pack history: ' + str(source / state_path))
                    for container in [state] + history:
                        require(isinstance(container, dict), 'invalid current WBS pack reference')
                        spec = container.get('batch')
                        if spec is not None:
                            require(isinstance(spec, dict) and isinstance(spec.get('batch_id'), str), 'invalid current WBS pack identity')
                            needed.add(spec['batch_id'])
    return needed


def _already_imported(repo, root, directory):
    """A completed import is custody, never an ID-only execution fallback."""
    destination = directory.replace(OLD_ROOT + '/TASK__', NEW_ROOT + '/WBS__', 1)
    with storage._control_workspace(repo, root) as source:
        raw_current = storage._read(source, destination + '/WBS.control.json', 16384)
        if raw_current is None:
            return False
        current = storage._migration_control(storage._control_json(raw_current, source / destination / 'WBS.control.json'))
        if not current:
            return False
        for _, journal in journals(repo):
            if journal['status'] != 'complete':
                continue
            for tree in journal['trees']:
                entry = tree['entries'].get('TASK.json')
                if tree['root'] != str(root) or tree['source'] != directory or tree['destination'] != destination or not entry:
                    continue
                original_path = _archive_prefix(journal) + '/' + directory + '/TASK.json'
                original = storage._read(source, original_path, 16384)
                require(original is not None and storage._hash(original) == entry['sha256'], 'completed import original authority changed: ' + str(source / original_path))
                before = storage._migration_control(storage._control_json(original, source / original_path))
                if before and before['run_id'] == current['run_id']:
                    return True
    return False


def _plan(repo, *, archive_prefix=None):
    prefix = archive_prefix or ARCHIVE + '/original'
    from _lib import _validate_wbs_control, resolve_registered_worktree
    discovered = _project_roots(repo)
    roots = set(discovered)
    require(len(roots) <= 256, 'worktree discovery budget exceeded')
    # workspace_roots deliberately skips unavailable/uninitialized checkouts;
    # migration must distinguish those from an empty registry.
    registry = storage._entries(repo, '.git/worktrees') if os.path.lexists(repo / '.git/worktrees') else []
    for name, _ in registry or []:
        raw = storage._read(repo, '.git/worktrees/' + name + '/gitdir', 4096)
        require(raw is not None, 'registered worktree discovery unavailable: ' + name)
        target = Path(os.fsdecode(raw).strip()).parent
        require(target.is_absolute() and target.is_dir(), 'registered worktree unavailable: ' + str(target))
        if str(target) not in roots:
            require(not os.path.lexists(target / OLD_ROOT) and not os.path.lexists(target / NEW_ROOT), 'registered execution workspace omitted; restore its own Harness manifest: ' + str(target))
    pools = []
    for name, _ in storage._entries(repo, 'doc/harness/runtime/batches') or []:
        if name == '.lock':
            continue
        require(re.fullmatch('[A-Za-z0-9_.-]+\\.json', name), 'unsafe pool record')
        relative = 'doc/harness/runtime/batches/' + name
        raw = storage._read(repo, relative)
        value = storage._json(raw)
        if 'owner_task_id' not in value and not any('task_id' in item for item in value.get('requests', [])):
            continue
        require(isinstance(value.get('requests'), list), 'invalid retired pool')
        for item in value['requests']:
            require(item.get('status') not in ('running', 'reserved'),
                    'stop and return or explicitly recover worker before migration: ' + str(item.get('task_id')))
            worktree = item.get('worktree')
            if worktree and os.path.isdir(worktree):
                require(_common_git_root(repo) == repo, 'local-only project migration cannot change external pool workspaces')
                require(resolve_registered_worktree(str(repo), worktree) == worktree, 'unregistered pool worktree')
                roots.add(worktree)
        pools.append((relative, raw, value))
    trees, mappings, retained, references = [], {}, [], set()
    for path, _, pool in pools:
        if pool.get('owner_task_id') and _pool_needs_owner(repo, pool):
            references.add((str(repo), pool['owner_task_id']))
        for item in pool['requests']:
            if item.get('worktree') in roots:
                references.add((item['worktree'], item['task_id']))
    for name, _ in storage._entries(repo, 'doc/harness/goals') or []:
        if name.endswith('.json'):
            goal = storage._json(storage._read(repo, 'doc/harness/goals/' + name))
            for child in goal.get('tasks', []):
                if isinstance(child, dict) and isinstance(child.get('task_id'), str):
                    references.add((str(repo), child['task_id']))
    markers = []
    for root in sorted(roots):
        with storage._control_workspace(repo, root) as source:
            for name, _ in storage._entries(source, OLD_ROOT + '/.active_sessions') or []:
                path = OLD_ROOT + '/.active_sessions/' + name
                raw = storage._read(source, path)
                kind, value = _marker(repo, root, name, raw, roots)
                markers.append((root, path, raw, kind, value))
                bindings = value.get('conflicts', []) if kind == 'fence' else [value] if kind == 'active' else []
                for binding in bindings if isinstance(bindings, list) else []:
                    live_root = _live_binding(repo, binding, roots)
                    if live_root is not None:
                        references.add((live_root, binding['task_id']))
    needed_batches = _batch_references(repo, roots, references, archive_prefix=prefix)
    retired_pools = [row for row in pools if not _pool_needs_owner(repo, row[2]) and row[2].get('batch_id') not in needed_batches]
    pools = [row for row in pools if row not in retired_pools]
    for _, _, pool in pools:
        if pool.get('owner_task_id'):
            references.add((str(repo), pool['owner_task_id']))
    retired_controls = []
    for root in sorted(roots):
        with storage._control_workspace(repo, root) as source:
            for name, info in storage._entries(source, OLD_ROOT) or []:
                if not OLD_ID.fullmatch(name):
                    continue
                require(stat.S_ISDIR(info.st_mode), 'unsafe retired root: ' + name)
                directory = OLD_ROOT + '/' + name
                raw = storage._read(source, directory + '/TASK.json', 16384) if os.path.lexists(source / directory / 'TASK.json') else None
                if raw is None:
                    if (root, name) in references and _already_imported(repo, root, directory):
                        retained.append(str(source / directory))
                        continue
                    intent = storage._read(source, directory + '/WBS.json') if (root, name) in references else None
                    if (root, name) in references and intent is not None:
                        _require_intent(storage._json(intent), str(source / directory))
                        new = NEW_ROOT + '/' + _new_id(name)
                        require(not os.path.lexists(source / new), 'destination already exists: ' + str(source / new))
                        mappings[str(source / directory)] = str(source / new)
                        trees.append(dict(root=root, source=directory, destination=new, entries=_tree(source, directory), replacement={}))
                    else:
                        retained.append(str(source / directory))
                    continue
                data = storage._control_json(raw, source / directory / 'TASK.json')
                control = storage._migration_control(data)
                if control and control['closed'] and (root, name) not in references:
                    retained.append(str(source / directory))
                    retired_controls.append((root, directory + '/TASK.json', raw))
                    continue
                if not control:
                    require((root, name) not in references and _historical(data), 'unsupported retired control: ' + str(source / directory))
                    retained.append(str(source / directory))
                    retired_controls.append((root, directory + '/TASK.json', raw))
                    continue
                new = NEW_ROOT + '/' + _new_id(name)
                require(not os.path.lexists(source / new), 'destination already exists: ' + str(source / new))
                mappings[str(source / directory)] = str(source / new)
                replacement = {} if control == data else {'WBS.control.json': _encoded(control)}
                trees.append(dict(root=root, source=directory, destination=new, entries=_tree(source, directory), replacement=replacement))
    # Preserved archives are copied only when a current pool actually owns them.
    for _, _, pool in pools:
        for item in pool['requests']:
            checkpoint = item.get('checkpoint') or {}
            archive = checkpoint.get('harvest', {}).get('archived')
            if not archive:
                continue
            old = 'doc/harness/archive/batch/' + item['task_id']
            require(archive == str(repo / old), 'retired archive identity mismatch')
            import batch_state
            require(batch_state.fingerprint(archive) == checkpoint.get('archive_fingerprint'), 'preexisting archive fingerprint drift')
            if archive in mappings:
                continue
            new = 'doc/harness/archive/batch/' + _new_id(item['task_id'])
            require(not os.path.lexists(repo / new), 'archive destination already exists')
            mappings[archive] = str(repo / new)
            original = storage._json(storage._read(repo, old + '/TASK.json', 16384))
            control = storage._migration_control(original)
            require(control, 'invalid archived control')
            replacement = {} if control == original else {'WBS.control.json': _encoded(control)}
            trees.append(dict(root=str(repo), source=old, destination=new, entries=_tree(repo, old), replacement=replacement))
    require(len(trees) <= 256, 'root-count budget exceeded')
    require(sum(len(t['entries']) for t in trees) <= MAX_FILES, 'total payload file-count budget exceeded')
    require(sum(row.get('size', 0) for t in trees for row in t['entries'].values()) <= MAX_BYTES, 'total payload byte budget exceeded')
    for tree in trees:
        with storage._control_workspace(repo, tree['root']) as source:
            state = storage._read(source, tree['source'] + '/WBS.json')
            if state:
                value = storage._json(state)
                import wbs
                wbs.validate_state(value)
                converted = copy.deepcopy(value)
                for key in ('batch', 'batch_history'):
                    if key in converted:
                        converted[key] = _transform_identity(converted[key], mappings)
                if converted != value:
                    tree['replacement']['WBS.json'] = _encoded(converted)
    archive_hashes = {}
    for tree in trees:
        if '/archive/batch/' in tree['source']:
            entries = {}
            for name, row in tree['entries'].items():
                if 'link' in row:
                    continue
                dest = 'WBS.control.json' if name == 'TASK.json' else name
                entries[dest] = None if row.get('directory') else hashlib.sha256(base64.b64decode(tree['replacement'][dest])).hexdigest() if dest in tree['replacement'] else row['sha256']
            archive_hashes[str(repo / tree['destination'])] = 'sha256:' + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
    changes = []
    for root, path, raw in retired_controls:
        changes.append(dict(root=root, source=path, destination=prefix + '/' + path, before=base64.b64encode(raw).decode(), after=base64.b64encode(raw).decode()))
    for path, raw, _ in retired_pools:
        changes.append(dict(root=str(repo), source=path, destination=prefix + '/' + path, before=base64.b64encode(raw).decode(), after=base64.b64encode(raw).decode()))
    for root, path, raw, kind, value in markers:
        if kind == 'historical':
            changes.append(dict(root=root, source=path, destination=prefix + '/' + path, before=base64.b64encode(raw).decode(), after=base64.b64encode(raw).decode()))
        else:
            after = _marker_transform(value, mappings, roots)
            destination = path.replace(OLD_ROOT, NEW_ROOT, 1)
            require(not os.path.lexists(Path(root) / destination), 'session marker destination already exists: ' + str(Path(root) / destination))
            changes.append(dict(root=root, source=path, destination=destination, before=base64.b64encode(raw).decode(), after=_encoded(after)))
    for root in sorted(roots):
        with storage._control_workspace(repo, root) as source:
            legacy_focus = storage._read(source, OLD_ROOT + '/.active', 4096)
            if legacy_focus is not None:
                path = OLD_ROOT + '/.active'
                changes.append(dict(root=root, source=path, destination=prefix + '/' + path, before=base64.b64encode(legacy_focus).decode(), after=base64.b64encode(legacy_focus).decode()))
    for path, raw, pool in pools:
        converted = _transform_identity(storage._without_close_fingerprint(pool), mappings)
        for item in converted['requests']:
            checkpoint = item.get('checkpoint') or {}
            archive = checkpoint.get('harvest', {}).get('archived')
            if archive in archive_hashes:
                checkpoint['archive_fingerprint'] = archive_hashes[archive]
        import batch_state
        batch_state.validate(converted, str(repo), Path(path).stem)
        changes.append(dict(root=str(repo), source=path, destination=path, before=base64.b64encode(raw).decode(), after=_encoded(converted)))
    if (trees or changes) and _has_git(repo) and not _runtime_ignored(repo):
        path = '.git/info/exclude'
        common = _common_git_root(repo)
        try:
            probe = dict(root=str(common), source=path)
            with _change_workspace(repo, probe) as git_root:
                before = storage._read(git_root, path)
        except (OSError, ValueError) as exc:
            raise ValueError('WBS namespace migration: unsafe Git-local ignore path ' + str(common / path) + '; secure that metadata or explicitly ignore /doc/harness/wbs/ and /doc/harness/archive/wbs-namespace-migration/ in the project .gitignore') from exc
        after = _exclude_bytes(before)
        if before != after:
            changes.append(dict(root=str(common), source=path, destination=path, before=base64.b64encode(before).decode() if before is not None else None, after=base64.b64encode(after).decode()))
    journal = dict(version=1, status='prepared' if trees or changes else 'complete', trees=trees, changes=changes, retained=retained, archive_prefix=prefix)
    require(len(storage._encode(journal)) <= MAX_MANIFEST, 'migration manifest budget exceeded')
    return journal


def _copy(repo, source, destination, expected, *, journal=None):
    if os.name == "nt":
        from wiki_platform import metadata_copy
        # The caller supplies its authenticated prepared journal under the
        # migration/session/WBS locks. A matching readonly destination is a
        # validated replay of that publication, not a request for a new flush.
        require(isinstance(journal, dict) and journal.get('status') == 'prepared',
                'native publication/replay requires the authenticated prepared journal; '
                'arbitrary or imported incomplete destinations require explicit reconciliation')
        admitted = False
        for tree in journal['trees']:
            prefix = tree['destination'] + '/'
            if tree['root'] != str(repo) or not destination.startswith(prefix):
                continue
            name = destination[len(prefix):]
            original = 'TASK.json' if name == 'WBS.control.json' else name
            sources = {tree['source'] + '/' + original}
            if original == 'TASK.json':
                sources.add(_archive_prefix(journal) + '/' + tree['source'] + '/TASK.json')
            admitted = (name not in tree['replacement'] and source in sources
                        and tree['entries'].get(original) == expected)
            break
        require(admitted, 'native copy does not match the authenticated prepared journal')
        # New publication preflushes and seals its retained write handle before
        # write-through rename. Imported incomplete copies do not establish
        # that Native publication guarantee and are unsupported recovery input.
        metadata_copy(Path(repo).joinpath(*storage._parts(source)),
                      Path(repo).joinpath(*storage._parts(destination)),
                      max_bytes=MAX_BYTES, expected_sha256=expected['sha256'],
                      expected_size=expected['size'], readonly=not bool(expected['mode'] & 0o200))
        return
    parts = storage._parts(destination)
    with storage._directory(repo, parts[:-1], create=True) as (parent, bound):
        name, temporary = parts[-1], storage._publication_stage(parts[-1])
        try:
            exists = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            exists = None
        if exists is not None:
            visible = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            try:
                opened = os.fstat(visible)
                require(storage._same(exists, opened), 'visible payload inode changed')
                paired = temporary if opened.st_nlink == 2 else None
                require(_hash_at(parent, name, destination, publication=paired, descriptor=visible) == expected, 'destination payload changed: ' + destination)
                if paired:
                    canonical = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    alias = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                    require(storage._same(canonical, alias) and storage._same(canonical, opened)
                            and canonical.st_nlink == alias.st_nlink == 2, 'payload publication pair changed')
                    os.unlink(temporary, dir_fd=parent)
                stable = os.fstat(visible)
                require(stable.st_nlink == 1, 'visible payload has unaccounted aliases')
                # A prior writer may have renamed successfully without reaching
                # its directory fsync. Replay must establish both durability
                # steps rather than treating visibility as a commit point.
                os.fsync(visible)
                after = os.fstat(visible)
                identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                require(identity(stable) == identity(after)
                        and storage._same(after, os.stat(name, dir_fd=parent, follow_symlinks=False)), 'visible payload changed before custody')
                bound()
                os.fsync(parent)
            finally:
                os.close(visible)
            return
        try:
            stage = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            stage = None
        if stage is not None:
            recovered = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            try:
                opened = os.fstat(recovered)
                require(storage._same(stage, opened) and opened.st_nlink == 1, 'interrupted payload stage rebound')
                require(_hash_at(parent, temporary, destination, descriptor=recovered) == expected, 'interrupted payload stage changed')
                # An orphan can contain all expected bytes without ever having
                # reached the original writer's fsync. Flush the held inode,
                # then revalidate it before making the recovered name visible.
                os.fsync(recovered)
                after = os.fstat(recovered)
                identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                require(identity(opened) == identity(after)
                        and storage._same(after, os.stat(temporary, dir_fd=parent, follow_symlinks=False)), 'interrupted payload stage changed before publication')
                bound()
                storage._rename_new(parent, temporary, name)
                os.fsync(parent)
            finally:
                os.close(recovered)
            return
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            require(_hash_file(repo, source, destination=fd) == expected, 'source payload changed: ' + source)
            os.fchmod(fd, expected['mode'])
            os.fsync(fd)
            bound()
            stage = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(fd)
            require(storage._same(stage, opened) and opened.st_nlink == 1, 'payload staging name changed')
            storage._rename_new(parent, temporary, name)
            os.fsync(parent)
        finally:
            try:
                try:
                    stage = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    stage = None
                if stage is not None:
                    opened = os.fstat(fd)
                    if storage._same(stage, opened) and opened.st_nlink == 1:
                        os.unlink(temporary, dir_fd=parent)
            finally:
                os.close(fd)


def _destination_entries(tree):
    result = {}
    for name, row in tree['entries'].items():
        if 'link' in row:
            continue
        name = 'WBS.control.json' if name == 'TASK.json' else name
        if name in tree['replacement']:
            raw = base64.b64decode(tree['replacement'][name], validate=True)
            row = dict(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), mode=0o600)
        result[name] = row
    return result


def _publish_change(repo, change):
    with _change_workspace(repo, change) as source:
        before, after = (base64.b64decode(change[key]) if change[key] is not None else None for key in ('before', 'after'))
        observed = storage._read_publication(source, change['source'])
        require(observed in (before, after, None) if change['source'] != change['destination'] else observed in (before, after), 'control source changed')
        existing = storage._read_publication(source, change['destination'])
        require(existing in (None, after) if change['source'] != change['destination'] else existing in (before, after), 'control destination changed')
        if existing != after:
            storage._write(source, change['destination'], after, expected=storage._hash(existing))
        else:
            storage._normalize_publication(source, change['destination'], after)


def _planned_runtime_paths(repo, journal):
    result = {str(repo): {MANIFEST, ARCHIVE + '/.lock'}}
    for tree in journal['trees']:
        paths = result.setdefault(tree['root'], set())
        paths.update(tree['destination'] + '/' + name for name, row in _destination_entries(tree).items() if not row.get('directory'))
        if 'TASK.json' in tree['entries']:
            paths.add(_archive_prefix(journal) + '/' + tree['source'] + '/TASK.json')
    for change in journal['changes']:
        if change['source'] != '.git/info/exclude':
            result.setdefault(change['root'], set()).add(change['destination'])
    return result


def _report(journal):
    return dict(retained_historical_links=[str(Path(t['root']) / t['source'] / name) for t in journal['trees'] for name, row in t['entries'].items() if 'link' in row], status=journal['status'], wbs_roots=[str(Path(t['root']) / t['destination']) for t in journal['trees']],
                retained_historical_roots=journal['retained'], historical_payloads='Original evidence retained; retired controls do not authorize current execution.')


@storage._pinned
def preview(repo):
    journal = _journal(repo)
    return _report(_next_plan(repo, journal))


@storage._pinned
def apply(repo):
    initial_current = _journal(repo)
    initial = _next_plan(repo, initial_current)
    if initial['status'] == 'complete':
        return _report(initial)
    with ExitStack() as stack:
        stack.enter_context(storage._file_lock(repo, ARCHIVE + '/.lock'))
        current = _journal(repo)
        journal = initial if current == initial_current else _next_plan(repo, current)
        if journal['status'] == 'complete':
            return _report(journal)
        discovered = _project_roots(repo)
        roots = sorted(set(discovered) | {t['root'] for t in journal['trees']} | {c['root'] for c in journal['changes'] if c['source'] != '.git/info/exclude'})
        for root in roots:
            with storage._control_workspace(repo, root) as source:
                for namespace in (OLD_ROOT, NEW_ROOT):
                    stack.enter_context(storage._locked_directory(
                        source, storage._parts(namespace + '/.active_sessions'), create=True,
                        busy_message='WBS namespace migration: session writer active; retry after it exits'))
                for directory in (OLD_ROOT, NEW_ROOT):
                    for name, _ in storage._entries(source, directory) or []:
                        if OLD_ID.fullmatch(name) or re.fullmatch(r'WBS__[A-Za-z0-9_.-]{1,180}', name):
                            stack.enter_context(storage._locked_directory(
                                source, storage._parts(directory + '/' + name),
                                busy_message='WBS namespace migration: execution writer active; stop it before migration'))
        if os.path.lexists(repo / 'doc/harness/runtime/batches'):
            stack.enter_context(storage._file_lock(repo, 'doc/harness/runtime/batches/.lock'))
        if current is None or current['status'] == 'complete':
            require(journal == _plan(repo, archive_prefix=_archive_prefix(journal)), 'migration inputs changed during admission')
            _validate_semantics(repo, journal)
            before = storage._read_publication(repo, MANIFEST, MAX_MANIFEST)
            if current is not None:
                existing_history = storage._entries(repo, HISTORY) or []
                require(len(existing_history) < MAX_JOURNALS, 'historical journal budget exceeded')
                path = HISTORY + '/' + hashlib.sha256(before).hexdigest() + '.json'
                _require_runtime_ignored(repo, (path,))
                prior = storage._read_publication(repo, path, MAX_MANIFEST)
                require(prior is None or prior == before, 'conflicting historical journal bytes')
                if prior is None:
                    storage._write(repo, path, before)
                else:
                    storage._normalize_publication(repo, path, before)
            storage._write(repo, MANIFEST, storage._encode(journal), expected=storage._hash(before))
        planned_paths = _planned_runtime_paths(repo, journal)
        for change in journal['changes']:
            if change['source'] == '.git/info/exclude':
                _publish_change(repo, change)
        for root in roots:
            _require_runtime_ignored(Path(root), planned_paths.get(root, ()))
        for tree in journal['trees']:
            with storage._control_workspace(repo, tree['root']) as source:
                entries = _tree(source, tree['source'])
                original_control = tree['entries'].get('TASK.json')
                if original_control and 'TASK.json' not in entries:
                    entries['TASK.json'] = original_control  # Retired only after complete publication.
                require(entries == tree['entries'], 'historical source payload changed')
                archive = _archive_prefix(journal) + '/' + tree['source'] + '/TASK.json'
                raw = storage._read(source, tree['source'] + '/TASK.json', 16384)
                if raw is not None:
                    archived = storage._read_publication(source, archive)
                    require(archived is None or archived == raw, 'original control archive changed')
                    if archived is None:
                        storage._write(source, archive, raw)
                    else:
                        storage._normalize_publication(source, archive, raw)
                for name, row in sorted(tree['entries'].items(), key=lambda item: (item[0].count('/'), item[0])):
                    if 'link' in row:
                        continue
                    dest = tree['destination'] + '/' + ('WBS.control.json' if name == 'TASK.json' else name)
                    if row.get('directory'):
                        with storage._directory(source, storage._parts(dest), create=True):
                            pass
                    elif ('WBS.control.json' if name == 'TASK.json' else name) in tree['replacement']:
                        after = base64.b64decode(tree['replacement']['WBS.control.json' if name == 'TASK.json' else name])
                        existing = storage._read_publication(source, dest)
                        require(existing is None or existing == after, 'replacement destination changed')
                        if existing is None:
                            storage._write(source, dest, after)
                        else:
                            storage._normalize_publication(source, dest, after)
                    else:
                        control_source = tree['source'] + '/' + name
                        if name == 'TASK.json' and raw is None:
                            control_source = archive
                        _copy(source, control_source, dest, row, journal=journal)
                # Freeze copied snapshots only after their children are published.
                for name, row in sorted(tree['entries'].items(), key=lambda item: -item[0].count('/')):
                    if row.get('directory'):
                        with storage._directory(source, storage._parts(tree['destination'] + '/' + name)) as (fd, bound):
                            if os.name == "nt":
                                from wiki_platform import metadata_set_readonly
                                metadata_set_readonly(fd, not bool(row['mode'] & 0o200), directory=True)
                            else:
                                os.fchmod(fd, row['mode'])
                            bound()
        for tree in journal['trees']:
            with storage._control_workspace(repo, tree['root']) as source:
                original = _tree(source, tree['source'])
                if 'TASK.json' not in original and 'TASK.json' in tree['entries']:
                    original['TASK.json'] = tree['entries']['TASK.json']
                require(original == tree['entries'], 'historical source payload changed before retirement')
                require(_tree(source, tree['destination']) == _destination_entries(tree), 'published payload differs from prepared source')
        for change in journal['changes']:
            _publish_change(repo, change)
        for root in roots:
            _require_runtime_ignored(Path(root), planned_paths.get(root, ()))
        for tree in journal['trees']:
            with storage._control_workspace(repo, tree['root']) as source:
                control = tree['entries'].get('TASK.json')
                if control and tree['source'].startswith(OLD_ROOT + '/'):
                    storage._retire(source, tree['source'] + '/TASK.json', control['sha256'])
        for change in journal['changes']:
            if change['source'] != change['destination']:
                with storage._control_workspace(repo, change['root']) as source:
                    storage._retire(source, change['source'], storage._hash(base64.b64decode(change['before'])))
        journal['status'] = 'complete'
        storage._write(repo, MANIFEST, storage._encode(journal))
        return _report(journal)


def _require_committed(repo):
    if os.path.lexists(repo / MANIFEST):
        raw = storage._read_publication(repo, MANIFEST, MAX_MANIFEST)
        journal = storage._json(raw)
        require(isinstance(journal, dict) and journal.get('version') == 1 and journal.get('status') == 'complete', 'interrupted migration; resume wbs(action="migrate", apply=true)')


def require_complete(repo):
    """A status/read never migrates; retired authority requires explicit import."""
    repo = Path(repo).absolute()
    _require_committed(repo)
    # A lead must not escape a partially committed common-repository import.
    # Read Git's small gitfile only when this checkout has retired state; no
    # source hashing, subprocess or extra persistent index is involved.
    if (repo / '.git').is_file():
        from _lib import _read_regular_text_file
        gitfile = _read_regular_text_file(str(repo / '.git'), max_size=4096)
        if gitfile and gitfile.startswith('gitdir: '):
            gitdir = Path(gitfile[8:].strip())
            gitdir = Path(os.path.abspath(repo / gitdir))
            if gitdir.parent.name == 'worktrees' and gitdir.parent.parent.name == '.git':
                common = gitdir.parents[2]
                from _lib import is_harness_enabled_repo
                if os.path.lexists(common / MANIFEST) and is_harness_enabled_repo(str(common)):
                    from _lib import resolve_registered_worktree
                    require(resolve_registered_worktree(str(common), str(repo)) == str(repo), 'unregistered migration checkout')
                    _require_committed(common)
    if not os.path.lexists(repo / OLD_ROOT):
        return
    for name, _ in storage._entries(repo, OLD_ROOT) or []:
        if OLD_ID.fullmatch(name):
            require(not os.path.lexists(repo / OLD_ROOT / name / 'TASK.json'),
                    'retired execution controls exist; use wbs(action="migrate") preview/apply')
