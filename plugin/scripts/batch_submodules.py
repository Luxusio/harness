#!/usr/bin/env python3
"""Bounded, local-only submodule operations for the durable batch owner.

The caller owns serialization and the state journal.  Persist callbacks must
atomically save the supplied manifest before returning; this module never
writes WBS evidence or the batch journal.  Refusal always retains private data.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from _lib import _git_executable, _trusted_git_env

MAX_MODULES = 16
MAX_INVENTORY = 4096
MAX_HISTORY = 4096
MAX_MANIFEST_BYTES = 512 * 1024
OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


class SubmoduleError(RuntimeError):
    """S1 cannot prove this operation safe; retain the checkout and state."""


def _require(condition, message):
    if not condition:
        raise SubmoduleError(message)


def _conversion_policy(root):
    """Read only safe effective conversion values, never execute configured tools."""
    from wiki_platform import run_bounded
    env = _trusted_git_env()
    env.update(GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    policy = {}
    for key, default, allowed in (
            ('core.autocrlf', 'false', {'false', 'true', 'input'}),
            ('core.eol', 'native', {'lf', 'crlf', 'native'})):
        try:
            result = run_bounded(
                [_git_executable(root, env), '--no-pager', '--no-optional-locks', 'config', '--includes',
                 '--null', '--type=bool-or-str', '--get', key],
                cwd=root, env=env, timeout=10, limit=32)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SubmoduleError('Git conversion policy unavailable: ' + key) from exc
        except ValueError as exc:
            raise SubmoduleError('invalid Git conversion policy: ' + key) from exc
        _require(len(result.stdout) <= 32 and len(result.stderr) <= 4096
                 and result.returncode in (0, 1), 'invalid Git conversion policy: ' + key)
        if result.returncode == 1:
            _require(not result.stdout, 'invalid Git conversion policy: ' + key)
            value = default
        else:
            raw = result.stdout
            _require(raw.endswith(b'\0'), 'invalid Git conversion policy: ' + key)
            try:
                value = raw[:-1].decode('ascii').lower()
            except UnicodeError as exc:
                raise SubmoduleError('invalid Git conversion policy: ' + key) from exc
            _require(value in allowed, 'invalid Git conversion policy: ' + key)
        policy[key] = value
    return policy


def _git(root, *args, allow=False):
    env = _trusted_git_env()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    conversion = []
    if os.name == 'nt':
        for key, value in _conversion_policy(root).items():
            conversion.extend(('-c', key + '=' + value))
    command = [_git_executable(root, env), '--no-optional-locks', *conversion, '-c', 'core.fsmonitor=false',
               '-c', 'core.hooksPath=' + os.devnull, '-c', 'submodule.recurse=false',
               '-c', 'fetch.recurseSubmodules=false', '-c', 'protocol.allow=never',
               '-c', 'protocol.file.allow=always', '-c', 'maintenance.auto=false',
               '-c', 'gc.auto=0', '-c', 'pack.writeReverseIndex=false', '-c', 'core.fsync=all', '-c', 'core.fsyncMethod=fsync', *args]
    try:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SubmoduleError('local Git operation unavailable') from exc
    _require(len(result.stdout) <= 4 * 1024 * 1024, 'Git inventory exceeds bound')
    if not allow:
        _require(result.returncode == 0, 'local Git operation refused: ' + args[0])
        _require(not any(x in result.stderr for x in (b'Permission denied', b'could not open directory')), 'Git could not inspect all content')
    return result if allow else os.fsdecode(result.stdout).strip('\n')


def _safe(path, *, exists=True):
    path = os.path.abspath(path)
    if os.name == "nt":
        from wiki_platform import _native
        target = Path(path)
        missing = []
        while not os.path.lexists(target):
            _require(not exists, 'missing path: ' + path)
            missing.append(target.name)
            target = target.parent
        if target.is_dir():
            with _native._walk(target, owner_neutral=True):
                pass
        else:
            with _native._walk(target.parent, owner_neutral=True):
                handle = _native._open(target, 0, owner_neutral=True, allow_hardlinks=True)
                _native._close(handle)
        return path
    _require(os.path.realpath(path) == path, 'symlink or noncanonical path')
    current = Path('/')
    for part in Path(path).parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            _require(not exists, 'missing path: ' + str(current))
            break
        _require(not stat.S_ISLNK(info.st_mode), 'symlink metadata or checkout')
    return path


def _identity(path, directory=False):
    _safe(path)
    info = os.lstat(path)
    _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'unsafe metadata type')
    return [info.st_dev, info.st_ino]


def _rel(value):
    _require(isinstance(value, str) and value and len(value) <= 4096 and '\x00' not in value
             and not value.startswith('/') and all(p not in ('', '.', '..', '.git') for p in value.split('/')), 'invalid selected module path')
    return value


def _metadata(root):
    root = _safe(root)
    gitfile = os.path.join(root, '.git')
    _identity(gitfile, os.path.isdir(gitfile))
    _require(os.path.normcase(os.path.abspath(_git(root, 'rev-parse', '--show-toplevel'))) == os.path.normcase(root), 'foreign checkout binding')
    gd = os.path.abspath(_git(root, 'rev-parse', '--absolute-git-dir'))
    _safe(gd)
    common = os.path.abspath(os.path.join(root, _git(root, 'rev-parse', '--git-common-dir')))
    _require(common == gd, 'module linked worktrees are unsupported')
    return gd, {'gitdir': _identity(gd, True), 'gitfile': _identity(gitfile, os.path.isdir(gitfile))}


def _supported(root, gd):
    _require(_git(root, 'rev-parse', '--is-shallow-repository') == 'false', 'shallow module unsupported')
    _require(not os.path.lexists(os.path.join(gd, 'objects/info/alternates'))
             and not os.path.lexists(os.path.join(gd, 'objects/info/http-alternates')), 'alternate object stores unsupported')
    _require(not _git(root, 'for-each-ref', '--format=%(refname)', 'refs/replace/'), 'replacement refs unsupported')
    configured = _git(root, 'config', '--local', '--get-regexp', r'(^extensions\.partialclone$|^remote\..*\.promisor$|^remote\..*\.partialclonefilter$)', allow=True)
    _require(configured.returncode == 1, 'promisor or partial clone configuration unsupported')
    _require(not list(Path(gd, 'objects/pack').glob('*.promisor')), 'promisor objects unsupported')
    _require(not _git(root, 'ls-files', '--stage').startswith('160000 ')
             and '\n160000 ' not in _git(root, 'ls-files', '--stage'), 'recursive submodules unsupported')
    _require(not os.path.lexists(os.path.join(root, '.gitmodules')), 'recursive module declarations unsupported')
    # Walk actual content, including ignored directories. Git status alone hides
    # nested repositories, and forced removal would otherwise discard them.
    def fail(exc):
        raise SubmoduleError('cannot inspect module content') from exc
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        if current == root:
            dirs[:] = [d for d in dirs if d != '.git']
        else:
            _require('.git' not in dirs and '.git' not in files, 'nested repository unsupported')
        count += len(dirs) + len(files)
        _require(count <= 100000, 'module content inventory exceeds bound')


def reject_hidden_index_flags(root):
    """Refuse index flags which can hide unique working-tree bytes from Git."""
    entries = _git(root, '--literal-pathspecs', 'ls-files', '-v', '-z').split('\0')
    _require(not any(entry and (entry[0] == 'S' or entry[0].islower()) for entry in entries),
             'assume-unchanged or skip-worktree index flags must be cleared before S1 work')


def _clean(root, ignored=False):
    reject_hidden_index_flags(root)
    _require(not _git(root, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'), 'module checkout is dirty')
    if ignored:
        _require(not _git(root, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z'), 'unknown ignored module content')


def _tree(root, commit):
    links = {}
    gm = None
    for record in _git(root, 'ls-tree', '-r', '-z', commit).split('\0'):
        if not record:
            continue
        meta, path = record.split('\t', 1)
        mode, kind, oid = meta.split()
        if mode == '160000':
            links[_rel(path)] = oid
        if path == '.gitmodules':
            _require(mode == '100644', 'unsafe .gitmodules mode')
            gm = oid
    return links, gm


def _closure(root, objects):
    for oid in objects:
        _require(isinstance(oid, str) and OID.fullmatch(oid), 'invalid object identity')
        _git(root, 'cat-file', '-e', oid)
    if objects:
        # fsck checks the entire local object store, including non-commit refs.
        _git(root, 'fsck', '--full', '--no-dangling', '--no-reflogs', *sorted(objects))


def selection(repo, paths):
    """Read-only selection; paths name direct populated gitlink roots."""
    repo = _safe(repo)
    _require(isinstance(paths, list) and len(paths) <= MAX_MODULES and all(isinstance(p, str) for p in paths), 'invalid module selection')
    _require(len(set(paths)) == len(paths), 'duplicate selected module')
    links, gm = _tree(repo, 'HEAD')
    declared = set()
    if gm:
        for line in _git(repo, 'config', '-z', '-f', '.gitmodules', '--get-regexp', r'^submodule\..*\.path$').split('\0'):
            if line:
                declared.add(line.split('\n', 1)[1])
    result = []
    for path in paths:
        _rel(path)
        _require(path in links and path in declared, 'selection is not a declared direct gitlink')
        root = os.path.normpath(os.path.join(repo, path))
        gd, ident = _metadata(root)
        modules_dir = os.path.join(repo, '.git', 'modules')
        embedded = gd == os.path.join(root, '.git') and os.path.isdir(gd)
        _require(embedded or os.path.commonpath((gd, modules_dir)) == modules_dir, 'foreign main module gitdir')
        _supported(root, gd)
        _clean(root)
        _require(_git(root, 'rev-parse', 'HEAD') == links[path], 'main module differs from recorded gitlink')
        index = _git(repo, '--literal-pathspecs', 'ls-files', '--stage', '-z', '--', path)
        _require(index == f'160000 {links[path]} 0\t{path}\0', 'module index differs from committed gitlink')
        _closure(root, {links[path]})
        result.append(dict(path=path, initial_gitlink=links[path], main_gitdir=gd, main_identity=ident))
    return result


def _admin(repo, worktree):
    _safe(worktree)
    _identity(os.path.join(worktree, '.git'))
    gd = os.path.abspath(_git(worktree, 'rev-parse', '--absolute-git-dir'))
    _require(os.path.dirname(gd) == os.path.join(repo, '.git', 'worktrees'), 'worktree not registered under control repository')
    _identity(gd, True)
    back = Path(gd, 'gitdir')
    _identity(str(back))
    _require(os.path.normcase(os.path.abspath(back.read_text(encoding='utf-8' if os.name == 'nt' else None).strip())) == os.path.normcase(os.path.join(worktree, '.git')), 'worktree registration backpointer mismatch')
    _require(os.path.abspath(os.path.join(worktree, _git(worktree, 'rev-parse', '--git-common-dir'))) == os.path.join(repo, '.git'), 'foreign common Git directory')
    return gd


def manifest(repo, worktree, selections, *, batch_id, slug, spawn_head):
    """Construct preparation intent; caller must persist before prepare effects."""
    repo, worktree = _safe(repo), _safe(worktree)
    _require(selections and selection(repo, [x['path'] for x in selections]) == selections, 'selection identity changed')
    admin = _admin(repo, worktree)
    _require(_git(worktree, 'rev-parse', 'HEAD') == spawn_head, 'worktree differs from spawn')
    links, gm = _tree(repo, spawn_head)
    token = hashlib.sha256((batch_id + '\0' + slug + '\0' + worktree + '\0' + spawn_head).encode()).hexdigest()[:32]
    modules = []
    for entry in selections:
        item = dict(entry)
        suffix = hashlib.sha256(item['path'].encode()).hexdigest()[:24]
        item.update(private_gitdir=os.path.join(admin, 'modules', suffix, 'git'), branch=f'harness-s1/{token}/{suffix}',
                    phase='intent', private_identity=None, private_parent_identity=None, inventory=None, pins=None)
        modules.append(item)
    value = dict(version=1, repo=repo, worktree=worktree, batch_id=batch_id, slug=slug, spawn_head=spawn_head,
                 admin_dir=admin, admin_identity=_identity(admin, True), topology=links, gitmodules_oid=gm,
                 modules=modules, witness=None)
    return validate_manifest(value)


def validate_manifest(value):
    """Validate journal shape without relying on checkout existence."""
    _require(isinstance(value, dict) and set(value) == {'version','repo','worktree','batch_id','slug','spawn_head','admin_dir','admin_identity','topology','gitmodules_oid','modules','witness'}, 'invalid S1 manifest shape')
    _require(type(value['version']) is int and value['version'] == 1, 'unknown module protocol')
    _require(len(json.dumps(value).encode()) <= MAX_MANIFEST_BYTES, 'module manifest exceeds bound')
    for key in ('repo','worktree','admin_dir'):
        p = value[key]
        _require(isinstance(p, str) and os.path.isabs(p) and os.path.normpath(p) == p, 'invalid manifest path')
    for key in ('batch_id','slug'):
        _require(isinstance(value[key], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,256}', value[key]), 'invalid batch identity')
    _require(isinstance(value['spawn_head'], str) and OID.fullmatch(value['spawn_head']), 'invalid spawn identity')
    def ident(x):
        return isinstance(x, list) and len(x) == 2 and all(type(n) is int and n >= 0 for n in x)
    def metadata(x):
        return isinstance(x, dict) and set(x) == {'gitdir','gitfile'} and all(ident(a) for a in x.values())
    _require(ident(value['admin_identity']), 'invalid admin identity')
    _require(isinstance(value['topology'], dict) and len(value['topology']) <= MAX_INVENTORY, 'invalid topology')
    for p, oid in value['topology'].items():
        _rel(p)
        _require(isinstance(oid, str) and OID.fullmatch(oid), 'invalid topology object')
    _require(value['gitmodules_oid'] is None or isinstance(value['gitmodules_oid'], str) and OID.fullmatch(value['gitmodules_oid']), 'invalid gitmodules object')
    modules = value['modules']
    _require(isinstance(modules, list) and 1 <= len(modules) <= MAX_MODULES, 'invalid module count')
    seen = set()
    token = hashlib.sha256((value['batch_id'] + '\0' + value['slug'] + '\0' + value['worktree'] + '\0' + value['spawn_head']).encode()).hexdigest()[:32]
    for m in modules:
        _require(isinstance(m, dict) and set(m) == {'path','initial_gitlink','main_gitdir','main_identity','private_gitdir','branch','phase','private_identity','private_parent_identity','inventory','pins'}, 'invalid module record')
        p = _rel(m['path'])
        _require(p not in seen and value['topology'].get(p) == m['initial_gitlink'], 'invalid selected topology')
        seen.add(p)
        suffix = hashlib.sha256(p.encode()).hexdigest()[:24]
        _require(m['private_gitdir'] == os.path.join(value['admin_dir'], 'modules', suffix, 'git')
                 and m['branch'] == f'harness-s1/{token}/{suffix}', 'private identity derivation mismatch')
        _require(isinstance(m['main_gitdir'], str) and os.path.isabs(m['main_gitdir']) and metadata(m['main_identity']), 'invalid main identity')
        _require(m['phase'] in ('intent','owned','prepared'), 'invalid preparation phase')
        _require(m['private_parent_identity'] is None if m['phase'] == 'intent' else ident(m['private_parent_identity']), 'invalid private parent identity')
        _require(m['private_identity'] is None if m['phase'] == 'intent' else metadata(m['private_identity']), 'invalid private metadata identity')
        inv = m['inventory']
        if inv is not None:
            _require(isinstance(inv, dict) and set(inv) == {'refs','reflogs','gitlinks'}, 'invalid inventory')
            _require(isinstance(inv['refs'], dict) and all(isinstance(r, str) and r.startswith('refs/') for r in inv['refs']), 'invalid reference inventory')
            _require(all(isinstance(inv[k], list) for k in ('reflogs','gitlinks')), 'invalid commit inventory')
            objects = list(inv['refs'].values()) + inv['reflogs'] + inv['gitlinks']
            _require(len(objects) <= MAX_INVENTORY and all(isinstance(o, str) and OID.fullmatch(o) for o in objects), 'invalid or oversized object inventory')
        if m['pins'] is not None:
            _require(inv is not None and isinstance(m['pins'], dict), 'pins without inventory')
            objects = set(inv['refs'].values()) | set(inv['reflogs']) | set(inv['gitlinks'])
            _require(set(m['pins']) == objects and all(ref == _pin(value, m, oid) for oid, ref in m['pins'].items()), 'invalid preservation pin mapping')
    if value['witness'] is not None:
        _validate_witness(value, value['witness'])
    return value


def _bindings(repo, worktree, value):
    validate_manifest(value)
    _require(os.fspath(repo) == value['repo'] and os.fspath(worktree) == value['worktree'], 'manifest checkout mismatch')
    admin = _admin(repo, worktree)
    _require(admin == value['admin_dir'] and _identity(admin, True) == value['admin_identity'], 'worktree administration replaced')
    for m in value['modules']:
        gd, ident = _metadata(os.path.join(repo, m['path']))
        _require(gd == m['main_gitdir'] and ident == m['main_identity'], 'main module metadata replaced')
        _supported(os.path.join(repo, m['path']), gd)


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_preparation(root, gitdir, admin):
    """Make cloned bytes and directory entries durable before prepared publish."""
    count = 0
    for tree in (gitdir, root):
        for current, dirs, files in os.walk(tree, topdown=False, followlinks=False):
            count += len(dirs) + len(files)
            _require(count <= 100000, 'preparation durability inventory exceeds bound')
            for name in files:
                path = os.path.join(current, name)
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode):
                    _require(tree == root, 'symlink in private Git metadata')
                    continue
                _require(stat.S_ISREG(info.st_mode), 'nonregular preparation content')
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            _sync_directory(current)
    current = Path(gitdir).parent
    while current != Path(admin):
        _sync_directory(str(current))
        current = current.parent
    _sync_directory(admin)
    _sync_directory(os.path.dirname(root))


def _native_inventory(path):
    from wiki_platform import metadata_tree_inventory
    try:
        # Match the owning Native batch archive's bounded tree policy.
        return metadata_tree_inventory(path, max_entries=100000, max_bytes=8 * 1024 ** 3)
    except ValueError as exc:
        raise SubmoduleError('private preparation inventory refused (100000 entries, 8 GiB per tree); '
                             'retained: ' + path + ': ' + str(exc)) from exc


def _native_subset(present, expected):
    # A stopped copy may not have sealed its newly created directories yet.
    return all(name in expected and (entry == expected[name] or entry['kind'] == 'dir'
               and dict(entry, readonly=expected[name]['readonly']) == expected[name]
               and not entry['readonly']) for name, entry in present.items())


def _native_copy_tree(source, destination, inventory, overrides=None):
    """Resume only byte-identical subsets of an owned publication stage."""
    from wiki_platform import (ensure_private_directory, metadata_copy, metadata_write,
                               metadata_set_readonly, safe_metadata_snapshot)
    overrides = overrides or {}
    expected = copy.deepcopy(inventory)
    for name, data in overrides.items():
        _require(name in expected and expected[name]['kind'] == 'file', 'missing clone binding')
        expected[name].update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), readonly=False)
    ensure_private_directory(destination)
    present = _native_inventory(destination)
    _require(_native_subset(present, expected),
             'foreign or changed private publication stage retained: ' + destination)
    for name, entry in expected.items():
        target = os.path.join(destination, *name.split('/'))
        if entry['kind'] == 'dir':
            ensure_private_directory(target)
        elif name in overrides:
            if name in present:
                data, _ = safe_metadata_snapshot(target, MAX_MANIFEST_BYTES)
                _require(data == overrides[name], 'private binding changed')
            else:
                metadata_write(target, overrides[name], expected=None)
        else:
            metadata_copy(os.path.join(source, *name.split('/')), target,
                          max_bytes=entry['size'], expected_sha256=entry['sha256'],
                          expected_size=entry['size'], readonly=entry['readonly'])
    for name in sorted(expected, key=lambda name: name.count('/'), reverse=True):
        entry = expected[name]
        if entry['kind'] == 'dir':
            metadata_set_readonly(os.path.join(destination, *name.split('/')),
                                  entry['readonly'], directory=True)
    _require(_native_inventory(destination) == expected, 'private publication copy changed')
    _require(_native_inventory(source) == inventory, 'private clone changed during copy')
    return expected


def _native_remove_tree(path, inventory):
    """Retire only the exact owned copy whose surviving peer was checked."""
    from wiki_platform import metadata_remove, metadata_remove_empty
    present = _native_inventory(path)
    _require(_native_subset(present, inventory),
             'foreign or changed private cleanup content retained: ' + path)
    for name in sorted(present, key=lambda name: (name.count('/'), name), reverse=True):
        entry = present[name]
        target = os.path.join(path, *name.split('/'))
        if entry['kind'] == 'dir':
            metadata_remove_empty(target)
        else:
            metadata_remove(target, expected_sha256=entry['sha256'], max_bytes=entry['size'])
    metadata_remove_empty(path)


def _native_clone_proof(repo, root, gd, m):
    actual, ident = _metadata(root)
    _require(actual == gd and _git(root, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + m['branch']
             and _git(root, 'rev-parse', 'HEAD') == m['initial_gitlink']
             and os.path.normcase(os.path.abspath(_git(root, 'config', '--local', '--get', 'remote.origin.url')))
                 == os.path.normcase(os.path.join(repo, m['path'])),
             'incomplete or foreign private clone retained: ' + root)
    _supported(root, gd)
    _clean(root, True)
    _closure(root, {m['initial_gitlink']})
    return ident


def _prepare_native(repo, worktree, value, m, persist):
    """Publish flushed private copies; the existing owned checkpoint admits replay."""
    from wiki_platform import (ensure_private_directory, metadata_remove_empty,
                               metadata_rename_directory, safe_metadata_snapshot)
    root, gd = os.path.join(worktree, m['path']), m['private_gitdir']
    parent = os.path.dirname(gd)
    clone = os.path.join(parent, 'clone')
    clone_root, clone_gd = os.path.join(clone, 'worktree'), os.path.join(clone, 'git')
    git_stage, root_stage = os.path.join(parent, 'publish-git'), os.path.join(parent, 'publish-worktree')
    if m['phase'] == 'intent':
        _require(not os.path.lexists(parent) and (not os.path.lexists(root)
                 or os.path.isdir(root) and not os.listdir(root)), 'unknown partial module preparation retained')
        ensure_private_directory(parent)
        m['private_parent_identity'] = _identity(parent, True)
        m['private_identity'] = {'gitdir': [0, 0], 'gitfile': [0, 0]}
        m['phase'] = 'owned'
        persist(value)
    _require(_identity(parent, True) == m['private_parent_identity'], 'private preparation container replaced')
    _require(set(os.listdir(parent)) <= {'clone', 'publish-git', 'publish-worktree', 'git'},
             'unknown private preparation content retained: ' + parent)
    final_worktree = root.replace('\\', '/').replace('"', '\\"')
    config_suffix = ('\n[core]\n\tworktree = "' + final_worktree + '"\n').encode('utf-8')
    gitfile = ('gitdir: ' + gd.replace('\\', '/') + '\n').encode('utf-8')
    if os.path.lexists(gd) and os.path.lexists(os.path.join(root, '.git')):
        ident = _native_clone_proof(repo, root, gd, m)
        git_inventory, root_inventory = _native_inventory(gd), _native_inventory(root)
        config, _ = safe_metadata_snapshot(os.path.join(gd, 'config'), MAX_MANIFEST_BYTES)
        actual_gitfile, _ = safe_metadata_snapshot(os.path.join(root, '.git'), MAX_MANIFEST_BYTES)
        _require(config.endswith(config_suffix) and actual_gitfile == gitfile,
                 'private final binding changed')
        for stage, inventory in ((git_stage, git_inventory), (root_stage, root_inventory)):
            if os.path.lexists(stage):
                _native_remove_tree(stage, inventory)
        if os.path.lexists(clone):
            _require(set(os.listdir(clone)) <= {'git', 'worktree'},
                     'unknown private clone content retained: ' + clone)
            # Reverse only the two exact binding changes. This proves each
            # remaining source byte survives in the published clone, even if
            # an earlier cleanup stopped after retiring some source entries.
            originals = ((clone_gd, git_inventory, 'config', config[:-len(config_suffix)]),
                         (clone_root, root_inventory, '.git',
                          ('gitdir: ' + clone_gd.replace('\\', '/') + '\n').encode('utf-8')))
            for path, inventory, name, data in originals:
                expected = copy.deepcopy(inventory)
                expected[name].update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), readonly=False)
                if os.path.lexists(path):
                    _native_remove_tree(path, expected)
            metadata_remove_empty(clone)
        m['private_identity'] = ident
        m['phase'] = 'prepared'
        persist(value)
        return
    if not os.path.lexists(clone) and not os.path.lexists(gd):
        _require(not os.path.lexists(git_stage) and not os.path.lexists(root_stage),
                 'publication stage without completed clone retained: ' + parent)
        ensure_private_directory(clone)
        conversion = [arg for key, setting in _conversion_policy(os.path.join(repo, m['path'])).items()
                      for arg in ('--config', key + '=' + setting)]
        _git(repo, 'clone', '--template=', '--no-local', '--no-hardlinks', '--no-checkout', '--no-tags',
             *conversion, '--separate-git-dir', clone_gd, '--', os.path.join(repo, m['path']), clone_root)
        _git(clone_root, 'checkout', '-b', m['branch'], m['initial_gitlink'])
    if os.path.lexists(clone):
        # Partial Git output is never erased or restarted. The owner must
        # inspect the retained clone path if this completed-clone proof fails.
        _require(set(os.listdir(clone)) == {'git', 'worktree'},
                 'incomplete private clone retained for owner inspection: ' + clone)
        _native_clone_proof(repo, clone_root, clone_gd, m)
        git_inventory, root_inventory = _native_inventory(clone_gd), _native_inventory(clone_root)
        config, _ = safe_metadata_snapshot(os.path.join(clone_gd, 'config'), MAX_MANIFEST_BYTES)
        config += config_suffix
        for source, stage, final, inventory, overrides in (
                (clone_gd, git_stage, gd, git_inventory, {'config': config}),
                (clone_root, root_stage, root, root_inventory, {'.git': gitfile})):
            expected = copy.deepcopy(inventory)
            for name, data in overrides.items():
                expected[name].update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), readonly=False)
            if os.path.lexists(final) and (final != root or os.listdir(root)):
                _require(_native_inventory(final) == expected, 'foreign private publication retained: ' + final)
                if os.path.lexists(stage):
                    _native_remove_tree(stage, expected)
            else:
                _native_copy_tree(source, stage, inventory, overrides)
                if final == root and os.path.lexists(root):
                    metadata_remove_empty(root)
                metadata_rename_directory(stage, final)
        ident = _native_clone_proof(repo, root, gd, m)
        # Final Git must resolve the recorded paths, including copied index
        # and core.worktree, before original staging bytes can be retired.
        _native_remove_tree(clone_root, root_inventory)
        _native_remove_tree(clone_gd, git_inventory)
        metadata_remove_empty(clone)
    else:
        _require(not os.path.lexists(git_stage) and not os.path.lexists(root_stage),
                 'publication stage without completed clone retained: ' + parent)
        ident = _native_clone_proof(repo, root, gd, m)
    m['private_identity'] = ident
    m['phase'] = 'prepared'
    persist(value)


def prepare(repo, worktree, value, persist):
    _bindings(repo, worktree, value)
    persist(value)  # writeahead: no directory or Git side effect before this
    for m in value['modules']:
        root = os.path.join(worktree, m['path'])
        gd = m['private_gitdir']
        _safe(root, exists=False)
        _safe(gd, exists=False)
        if os.name == 'nt' and m['phase'] != 'prepared':
            _prepare_native(repo, worktree, value, m, persist)
            _validate_private(worktree, m, clean=True)
            continue
        if m['phase'] == 'intent':
            _require(not os.path.lexists(os.path.dirname(gd)) and (not os.path.lexists(root) or os.path.isdir(root) and not os.listdir(root)), 'unknown partial module preparation retained')
            Path(gd).parent.parent.mkdir(exist_ok=True)
            Path(gd).parent.mkdir()
            m['private_parent_identity'] = _identity(os.path.dirname(gd), True)
            # Persist ownership before clone. gitfile is not present yet; its
            # identity is pinned only after clone, so interrupted clones refuse.
            m['private_identity'] = {'gitdir': [0, 0], 'gitfile': [0, 0]}
            m['phase'] = 'owned'
            _sync_directory(os.path.dirname(gd))
            _sync_directory(str(Path(gd).parent.parent))
            _sync_directory(value['admin_dir'])
            persist(value)
            _git(repo, 'clone', '--template=', '--no-local', '--no-hardlinks', '--no-checkout', '--no-tags', '--separate-git-dir', gd, '--', os.path.join(repo, m['path']), root)
            _git(root, 'checkout', '-b', m['branch'], m['initial_gitlink'])
            actual, ident = _metadata(root)
            _require(actual == gd and _identity(os.path.dirname(gd), True) == m['private_parent_identity'], 'private clone identity changed')
            _sync_preparation(root, gd, value['admin_dir'])
            m['private_identity'] = ident
            m['phase'] = 'prepared'
            persist(value)
        elif m['phase'] == 'owned':
            _require(_identity(os.path.dirname(gd), True) == m['private_parent_identity'], 'private preparation container replaced')
            actual, ident = _metadata(root)
            _require(actual == gd and _git(root, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + m['branch']
                     and _git(root, 'rev-parse', 'HEAD') == m['initial_gitlink']
                     and _git(root, 'config', '--local', '--get', 'remote.origin.url') == os.path.join(repo, m['path']), 'incomplete or foreign private clone retained')
            _supported(root, gd)
            _clean(root, True)
            _closure(root, {m['initial_gitlink']})
            _sync_preparation(root, gd, value['admin_dir'])
            m['private_identity'] = ident
            m['phase'] = 'prepared'
            persist(value)
        _validate_private(worktree, m, clean=True)
    return value


def _validate_private(worktree, m, clean=False, ignored=False):
    _require(m['phase'] == 'prepared', 'module preparation is incomplete')
    root = os.path.join(worktree, m['path'])
    gd, ident = _metadata(root)
    _require(gd == m['private_gitdir'] and ident == m['private_identity'] and _identity(os.path.dirname(gd), True) == m['private_parent_identity'], 'private module metadata replaced')
    _require(_git(root, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + m['branch'], 'module development branch changed or detached')
    _supported(root, gd)
    if clean:
        _clean(root, ignored)


def _history(worktree, value):
    _require(_git(worktree, 'merge-base', '--is-ancestor', value['spawn_head'], 'HEAD', allow=True).returncode == 0, 'spawn history no longer reachable')
    commits = _git(worktree, 'rev-list', f'--max-count={MAX_HISTORY + 1}', value['spawn_head'] + '..HEAD').splitlines()
    _require(len(commits) <= MAX_HISTORY, 'source history exceeds bound')
    objects = {m['path']: {m['initial_gitlink']} for m in value['modules']}
    selected = set(objects)
    for commit in [value['spawn_head'], *commits]:
        links, gm = _tree(worktree, commit)
        _require(set(links) == set(value['topology']) and gm == value['gitmodules_oid'], 'submodule topology changed in source history')
        if commit != value['spawn_head'] and _git(value['repo'], 'merge-base', '--is-ancestor', commit, 'HEAD', allow=True).returncode == 1:
            parents = _git(worktree, 'rev-list', '--parents', '-n', '1', commit).split()[1:]
            _require(len(parents) == 1, 'merge commits unsupported in source history')
            previous, _ = _tree(worktree, parents[0])
            _require(all(links[p] == previous.get(p) for p in links if p not in selected), 'source changed an unselected gitlink')
        for path in objects:
            objects[path].add(links[path])
    return objects


def validate(repo, worktree, value, phase='resume'):
    _require(phase in ('resume','prepare','finish','preserve','remove'), 'unknown validation phase')
    _bindings(repo, worktree, value)
    for m in value['modules']:
        _validate_private(worktree, m, clean=phase != 'resume', ignored=phase == 'remove')
    if phase != 'resume':
        _history(worktree, value)
        links, _ = _tree(worktree, 'HEAD')
        for m in value['modules']:
            _require(_git(os.path.join(worktree, m['path']), 'rev-parse', 'HEAD') == links[m['path']], 'module commit must be recorded in superproject gitlink')


def _inventory(worktree, m, history):
    root = os.path.join(worktree, m['path'])
    refs = {}
    for line in _git(root, 'for-each-ref', '--format=%(refname) %(objectname)').splitlines():
        name, oid = line.split(' ')
        refs[name] = oid
    reflog_objects = set(_git(root, 'reflog', 'show', '--all', '--format=%H').splitlines())
    logs = Path(m['private_gitdir'], 'logs')
    if logs.exists():
        for current, dirs, files in os.walk(logs, followlinks=False):
            for name in dirs:
                _safe(os.path.join(current, name))
            for name in files:
                logfile = os.path.join(current, name)
                _identity(logfile)
                _require(os.path.getsize(logfile) <= MAX_MANIFEST_BYTES, 'reflog exceeds bound')
                for line in Path(logfile).read_text(encoding='utf-8' if os.name == 'nt' else None).splitlines():
                    fields = line.split(' ', 2)
                    _require(len(fields) == 3 and all(OID.fullmatch(o) for o in fields[:2]), 'malformed reflog')
                    reflog_objects.update(o for o in fields[:2] if set(o) != {'0'})
                    _require(len(reflog_objects) <= MAX_INVENTORY, 'reflog inventory exceeds bound')
    reflogs = sorted(reflog_objects)
    inv = dict(refs=refs, reflogs=reflogs, gitlinks=sorted(history[m['path']]))
    _require(len(refs) + len(reflogs) + len(inv['gitlinks']) <= MAX_INVENTORY, 'module inventory exceeds bound')
    return inv


def _pin(value, m, oid):
    token = hashlib.sha256((value['batch_id'] + '\0' + value['slug'] + '\0' + value['worktree'] + '\0' + value['spawn_head'] + '\0' + m['path']).encode()).hexdigest()
    return 'refs/harness/batch-s1/' + token + '/' + oid


def preserve(repo, worktree, value, persist):
    validate(repo, worktree, value, 'preserve')
    history = _history(worktree, value)
    proposed = copy.deepcopy(value)
    for m in proposed['modules']:
        inv = _inventory(worktree, m, history)
        objects = set(inv['refs'].values()) | set(inv['reflogs']) | set(inv['gitlinks'])
        m['inventory'] = inv
        m['pins'] = {oid: _pin(value, m, oid) for oid in sorted(objects)}
        _closure(os.path.join(worktree, m['path']), objects)
    # Check the complete multi-module payload, including long pin names,
    # before fetching any objects or publishing any reference.
    validate_manifest(proposed)
    # Pins are deterministic intent, never completion authority. Publish the
    # entire payload through its owner before effects, so the aggregate batch
    # state bound is checked too. Every consumer verifies actual refs/closure.
    value.clear()
    value.update(proposed)
    persist(value)
    for m in value['modules']:
        inv, pins = m['inventory'], m['pins']
        objects = set(pins)
        root, main = os.path.join(worktree, m['path']), os.path.join(repo, m['path'])
        for oid, ref in pins.items():
            previous = _git(main, 'rev-parse', '--verify', '--quiet', ref, allow=True)
            if previous.returncode == 0:
                _require(os.fsdecode(previous.stdout).strip() == oid, 'preservation pin collision')
            else:
                _require(previous.returncode == 1, 'cannot inspect preservation pin')
                _git(main, 'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules', root, oid)
                _git(main, 'update-ref', ref, oid, '0' * len(oid))
        _closure(main, objects)
        _require(_inventory(worktree, m, history) == inv, 'module inventory changed during preservation')
    return value


def _proof(repo, worktree, value):
    history = _history(worktree, value)
    for m in value['modules']:
        _require(m['inventory'] is not None and m['pins'] is not None, 'module objects have not been preserved')
        _require(_inventory(worktree, m, history) == m['inventory'], 'module refs or reflogs changed after preservation')
        main = os.path.join(repo, m['path'])
        for oid, ref in m['pins'].items():
            _require(_git(main, 'show-ref', '--verify', '--hash', ref) == oid, 'preservation pin missing or changed')
        _closure(main, set(m['pins']))


def _validate_witness(value, w):
    _require(isinstance(w, dict) and set(w) == {'destination_ref','old_tip','target_tip','worktree','target','modules'}, 'invalid checkout witness')
    _require(w['target'] in ('main','lead') and w['worktree'] == value['worktree'], 'witness checkout mismatch')
    _require(isinstance(w['destination_ref'], str) and w['destination_ref'].startswith('refs/heads/'), 'invalid witness destination')
    _require(all(isinstance(w[k], str) and OID.fullmatch(w[k]) for k in ('old_tip','target_tip')), 'invalid witness tips')
    _require(isinstance(w['modules'], list) and len(w['modules']) == len(value['modules']), 'invalid witness modules')
    for entry, m in zip(w['modules'], value['modules']):
        _require(isinstance(entry, dict) and set(entry) == {'path','old_head','target_head'} and entry['path'] == m['path']
                 and all(isinstance(entry[k], str) and OID.fullmatch(entry[k]) for k in ('old_head','target_head')), 'invalid module witness')


def witness(repo, worktree, value, destination_ref, old_tip, target_tip, target='main'):
    """Create a pre-effect witness; caller persists it before rebase/ff."""
    _bindings(repo, worktree, value)
    _proof(repo, worktree, value)
    root = repo if target == 'main' else worktree
    _require(_git(root, 'symbolic-ref', '-q', 'HEAD') == destination_ref, 'destination branch changed')
    _require(_git(root, 'rev-parse', 'HEAD') == old_tip, 'destination tip changed')
    links, gm = _tree(repo, target_tip)
    _require(set(links) == set(value['topology']) and gm == value['gitmodules_oid'], 'target topology differs')
    entries = []
    for m in value['modules']:
        module = os.path.join(root, m['path'])
        _clean(module)
        old = _git(module, 'rev-parse', 'HEAD')
        _require(links[m['path']] in m['pins'], 'target gitlink not preserved')
        entries.append(dict(path=m['path'], old_head=old, target_head=links[m['path']]))
    w = dict(destination_ref=destination_ref, old_tip=old_tip, target_tip=target_tip, worktree=value['worktree'], target=target, modules=entries)
    _validate_witness(value, w)
    validate_manifest(dict(value, witness=w))
    return w


def reconcile_checkout(repo, value, w, target='main'):
    _bindings(repo, value['worktree'], value)
    _validate_witness(value, w)
    # The serialized pin map records intent only. Recovery must verify durable
    # destination refs themselves before changing any module checkout.
    preserved_proof(repo, value)
    _require(w['target'] == target, 'wrong witness target')
    root = repo if target == 'main' else value['worktree']
    _require(_git(root, 'symbolic-ref', '-q', 'HEAD') == w['destination_ref'], 'destination branch differs from witness')
    current = _git(root, 'rev-parse', 'HEAD')
    _require(current in (w['old_tip'], w['target_tip']), 'destination advanced beyond witness')
    if current == w['old_tip'] and current != w['target_tip']:
        for entry in w['modules']:
            module = os.path.join(root, entry['path'])
            _clean(module)
            _require(_git(module, 'rev-parse', 'HEAD') == entry['old_head'], 'pre-integration checkout moved')
        return
    links, gm = _tree(root, current)
    _require(set(links) == set(value['topology']) and gm == value['gitmodules_oid'], 'landed topology differs')
    selected = {m['path'] for m in value['modules']}
    # Exclude only selected gitlink paths; all other staged/unstaged/untracked
    # changes must still refuse before any checkout is updated.
    status = _git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--ignore-submodules=none')
    for entry in status.split('\0'):
        if entry:
            _require(entry[:2] == ' M' and entry[3:] in selected, 'unrelated destination dirt')
    for m, entry in zip(value['modules'], w['modules']):
        module = os.path.join(root, m['path'])
        _clean(module)
        _require(links[m['path']] == entry['target_head'], 'witness gitlink mismatch')
        head = _git(module, 'rev-parse', 'HEAD')
        _require(head in (entry['old_head'], entry['target_head']), 'foreign module checkout movement')
        _require(m['pins'] and entry['target_head'] in m['pins'], 'missing preserved target proof')
        _closure(module, {entry['target_head']})
        if head != entry['target_head']:
            if target == 'lead':
                _require(_git(module, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + m['branch'], 'lead module branch changed')
                _git(module, 'reset', '--keep', entry['target_head'])
            else:
                _git(module, 'checkout', '--detach', entry['target_head'])
        _clean(module)


def _disposable_metadata(worktree, m):
    """Prove that force-removal cannot discard unknown administrative data.

    Private clones start with an empty template. Only the native metadata
    forms whose contents are checked below can become disposable; unusual
    layouts and auxiliary tools' files remain for explicit owner disposition.
    """
    gd = m['private_gitdir']
    parent = os.path.dirname(gd)
    _require(os.listdir(parent) == ['git'], 'unknown private metadata container content')
    root = os.path.join(worktree, m['path'])
    inv = m['inventory']
    _require(inv is not None and m['pins'] is not None, 'missing metadata preservation intent')
    config = _git(root, 'config', '--local', '--null', '--list')
    for entry in filter(None, config.split('\0')):
        key, sep, value = entry.partition('\n')
        _require(sep, 'unreadable private Git configuration')
        allowed = key in {'core.repositoryformatversion', 'core.filemode', 'core.bare',
                          'core.logallrefupdates', 'core.ignorecase', 'core.precomposeunicode',
                          'remote.origin.url', 'remote.origin.fetch', 'remote.origin.tagopt',
                          'user.name', 'user.email'}
        if os.name == 'nt':
            allowed = allowed or key == 'core.symlinks' and value == 'false'
            allowed = allowed or key == 'core.autocrlf' and value in {'false', 'true', 'input'}
            allowed = allowed or key == 'core.eol' and value in {'lf', 'crlf', 'native'}
            allowed = allowed or key == 'core.worktree' and os.path.normcase(os.path.abspath(value)) == os.path.normcase(root)
        if key.startswith('branch.') and key.endswith(('.remote', '.merge')):
            branch, kind = key[7:].rsplit('.', 1)
            allowed = 'refs/heads/' + branch in inv['refs'] and (
                value == 'origin' if kind == 'remote' else value.startswith('refs/heads/'))
        _require(allowed, 'unsupported private Git configuration retained')
    _closure(root, set(m['pins']))
    reachable = set(_git(root, 'rev-list', '--objects', '--no-object-names', *sorted(m['pins'])).splitlines())
    all_objects = set(_git(root, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines())
    _require(all_objects <= reachable, 'unpreserved private Git objects retained')
    count = 0
    files = []
    def unreadable(exc):
        raise SubmoduleError('cannot inspect private Git metadata') from exc
    for current, dirs, names in os.walk(gd, followlinks=False, onerror=unreadable):
        count += len(dirs) + len(names)
        _require(count <= 100000, 'private metadata inventory exceeds bound')
        for name in dirs + names:
            path = os.path.join(current, name)
            info = os.lstat(path)
            _require(not stat.S_ISLNK(info.st_mode) and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)),
                     'unsafe private metadata entry')
            rel = os.path.relpath(path, gd).replace(os.sep, '/')
            if stat.S_ISDIR(info.st_mode):
                native = rel in {'objects', 'objects/info', 'objects/pack', 'refs', 'logs'}
                native = native or bool(re.fullmatch(r'objects/[0-9a-f]{2}', rel))
                native = native or any(ref.startswith(rel + '/') for ref in inv['refs'])
                native = native or rel == 'logs/refs' or any(('logs/' + ref).startswith(rel + '/') for ref in inv['refs'])
                # Empty standard ref namespaces are native clone output.
                native = native or rel in {'refs/heads', 'refs/tags', 'refs/remotes'}
                _require(native, 'unknown private metadata directory retained: ' + rel)
            else:
                files.append((rel, path))
    for rel, path in files:
        if re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{38}(?:[0-9a-f]{24})?', rel):
            _require(rel[8:].replace('/', '') in all_objects, 'unknown loose object retained')
            continue
        if re.fullmatch(r'objects/pack/pack-[0-9a-f]{40}(?:[0-9a-f]{24})?\.(pack|idx)', rel):
            index = path.rsplit('.', 1)[0] + '.idx'
            _identity(index)
            _git(root, 'verify-pack', index)
            continue
        _require(os.path.getsize(path) <= MAX_MANIFEST_BYTES, 'private metadata file exceeds bound')
        if rel == 'index':
            # Git clean-status checks parse the binary index and compare it
            # with HEAD; its bytes must never be decoded as text.
            continue
        known_text = rel in {'HEAD', 'config', 'COMMIT_EDITMSG', 'ORIG_HEAD', 'packed-refs', 'logs/HEAD'}
        known_text = known_text or rel in inv['refs'] or rel.startswith('logs/') and rel[5:] in inv['refs']
        _require(known_text, 'unknown private metadata file retained: ' + rel)
        try:
            text = Path(path).read_text(encoding='utf-8' if os.name == 'nt' else None)
        except (OSError, UnicodeError) as exc:
            raise SubmoduleError('unreadable private metadata retained: ' + rel) from exc
        if rel == 'HEAD':
            _require(text == 'ref: refs/heads/' + m['branch'] + '\n', 'unexpected private HEAD content')
        elif rel == 'config':
            _require(not any(line.lstrip().startswith(('#', ';')) for line in text.splitlines()),
                     'unproven private config comments retained')
        elif rel == 'COMMIT_EDITMSG':
            _require(text.strip('\n') == _git(root, 'log', '-1', '--format=%B'), 'unproven commit-message contents retained')
        elif rel == 'ORIG_HEAD':
            _require(text.strip() in m['pins'], 'unpreserved original HEAD retained')
        elif rel in inv['refs']:
            if text.startswith('ref: '):
                target = text[5:].strip()
                _require(target in inv['refs'] and inv['refs'][target] == inv['refs'][rel], 'unproven symbolic ref')
            else:
                _require(text == inv['refs'][rel] + '\n', 'unproven loose ref content')
        elif rel == 'packed-refs':
            last = None
            for line in text.splitlines():
                if line.startswith('#'):
                    _require(line.startswith('# pack-refs with: ') and all(word in {'peeled','fully-peeled','sorted'} for word in line[18:].split()), 'unknown packed-ref comment')
                elif line.startswith('^'):
                    _require(last is not None and line[1:] == _git(root, 'rev-parse', last + '^{}'), 'unproven peeled ref')
                else:
                    oid, sep, ref = line.partition(' ')
                    _require(sep and inv['refs'].get(ref) == oid, 'unproven packed reference')
                    last = ref
        elif rel == 'logs/HEAD' or rel.startswith('logs/') and rel[5:] in inv['refs']:
            for line in text.splitlines():
                fields = line.split(' ', 2)
                _require(len(fields) == 3 and all(set(oid) == {'0'} or oid in m['pins'] for oid in fields[:2]), 'unpreserved reflog entry')
        else:
            raise SubmoduleError('unknown private metadata file retained: ' + rel)

def removal_proof(repo, worktree, value):
    """Module proof only; harvest must additionally prove outer archived bytes."""
    validate(repo, worktree, value, 'remove')
    _proof(repo, worktree, value)
    modules_dir = os.path.join(value['admin_dir'], 'modules')
    expected = {os.path.basename(os.path.dirname(m['private_gitdir'])) for m in value['modules']}
    _require(set(os.listdir(modules_dir)) == expected, 'unknown module administration content retained')
    for module in value['modules']:
        _disposable_metadata(worktree, module)
    selected = {m['path'] for m in value['modules']}
    for path in value['topology']:
        if path not in selected:
            _require(not os.path.lexists(os.path.join(worktree, path, '.git')), 'unexpected initialized module')
    return {'version': 1, 'paths': sorted(selected), 'preserved': True}


def preserved_proof(repo, value):
    """Validate durable main pins after private worktree removal (no mutation)."""
    validate_manifest(value)
    _require(os.fspath(repo) == value['repo'], 'manifest repository mismatch')
    for m in value['modules']:
        main = os.path.join(repo, m['path'])
        gd, ident = _metadata(main)
        _require(gd == m['main_gitdir'] and ident == m['main_identity'], 'main module metadata replaced')
        _supported(main, gd)
        _require(m['inventory'] is not None and m['pins'] is not None, 'missing preserved inventory')
        for oid, ref in m['pins'].items():
            _require(_git(main, 'show-ref', '--verify', '--hash', ref) == oid, 'preservation pin missing or changed')
        _closure(main, set(m['pins']))
    return {'version': 1, 'preserved': True}
