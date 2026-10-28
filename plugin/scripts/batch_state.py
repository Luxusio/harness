#!/usr/bin/env python3
"""Durable, non-authoritative batch coordinator. See REQ__batch-state-pool-recovery."""
from __future__ import annotations

import argparse
import copy
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import batch_finish as finish_helper
import batch_harvest
import batch_preflight
import batch_submodules
from _lib import find_repo_root, read_task_control, task_control_status, resolve_registered_worktree, _trusted_git_env, uuid7_timestamp_ms

LIMIT = 1024 * 1024
MAX_BATCHES = 100
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STATES = {'queued', 'reserved', 'running', 'returned', 'integrating', 'integrated', 'blocked', 'failed', 'kept', 'abandoned', 'recovery-required'}
LIVE = {'reserved', 'running', 'integrating'}
FREE = {'queued', 'integrated'}
EXTENSION_FIELDS = {'extra_scopes', 'scope_grants'}
MAX_SCOPE_GRANTS = 16
EXTENSION_DIRTY_PATH_LIMIT = 4096
EXTENSION_DIRTY_BYTE_LIMIT = 64 * 1024 * 1024
EXTENSION_SNAPSHOT_SECONDS = 10
FULL_HEAD = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')


class Refusal(RuntimeError):
    pass


class Busy(Refusal):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def safe(path, directory=False):
    info = os.lstat(path)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.getuid() and not info.st_mode & 0o022
            and (directory or info.st_nlink == 1), f'unsafe metadata path: {path}')


def parents(path, create=False):
    current = os.path.abspath(path)
    missing = []
    while not os.path.lexists(current):
        missing.append(current)
        current = os.path.dirname(current)
    # Validate every existing component, including symlinked ancestors.
    part = '/'
    metadata = False
    for component in current.strip('/').split('/'):
        part = os.path.join(part, component)
        info = os.lstat(part)
        require(stat.S_ISDIR(info.st_mode), f'unsafe metadata parent: {part}')
        metadata = metadata or component == 'doc'
        if metadata:
            safe(part, True)
    safe(current, True)
    if create:
        for item in reversed(missing):
            os.mkdir(item, 0o700)
    elif missing:
        raise Refusal(f'missing state directory: {path}')


def read_json(path):
    safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid(), 'unsafe opened file')
        raw = handle.read(LIMIT + 1)
    require(len(raw) <= LIMIT, 'JSON exceeds 1 MiB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise Refusal(f'invalid JSON: {path}') from exc


def write_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    require(len(raw) <= LIMIT, 'state exceeds 1 MiB')
    parents(os.path.dirname(path))
    if os.path.lexists(path):
        safe(path)
    fd, temporary = tempfile.mkstemp(prefix='.batch-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def requests_valid(items):
    require(isinstance(items, list) and 0 < len(items) <= 100, 'requests must contain 1..100 entries')
    slugs = set()
    for item in items:
        require(isinstance(item, dict), 'invalid request')
        slug = item.get('slug')
        require(isinstance(slug, str) and IDENT.fullmatch(slug) and slug not in slugs, 'invalid or duplicate slug')
        slugs.add(slug)
        require(isinstance(item.get('request'), str) and bool(item['request'].strip()), 'empty request')
        scopes = item.get('scopes')
        require(isinstance(scopes, list) and scopes and len(scopes) <= 100, 'invalid scopes')
        for scope in scopes:
            require(isinstance(scope, str) and scope and not os.path.isabs(scope)
                    and '\x00' not in scope and '..' not in scope.split('/'), 'scope must be a literal relative path')
        if 'submodules' in item:
            modules = item['submodules']
            require(isinstance(modules, list) and len(modules) <= batch_submodules.MAX_MODULES
                    and all(isinstance(p, str) and p and len(p) <= 4096 and not p.startswith('/')
                            and '\x00' not in p and all(x not in ('', '.', '..', '.git') for x in p.split('/')) for p in modules)
                    and len(set(modules)) == len(modules), 'invalid submodule selection')
        deps = item.get('depends_on', [])
        require(isinstance(deps, list) and all(isinstance(x, str) for x in deps) and len(set(deps)) == len(deps), 'invalid dependencies')
    visiting, visited = set(), set()
    mapping = {x['slug']: x for x in items}
    def visit(slug):
        require(slug in slugs and slug not in visiting, 'unknown dependency or dependency cycle')
        if slug in visited:
            return
        visiting.add(slug)
        for dep in mapping[slug].get('depends_on', []):
            visit(dep)
        visiting.remove(slug)
        visited.add(slug)
    for slug in slugs:
        visit(slug)


def literal_scope(path):
    return (isinstance(path, str) and 0 < len(path) <= 4096 and not os.path.isabs(path)
            and '\x00' not in path and all(p not in ('', '.', '..', '.git') for p in path.split('/')))


def grant_valid(grant):
    require(isinstance(grant, dict) and set(grant) == {'scopes', 'expected_head', 'expected_run_id', 'worker_id', 'reason', 'granted_at'},
            'invalid scope grant')
    require(isinstance(grant['scopes'], list) and 0 < len(grant['scopes']) <= 100
            and all(literal_scope(p) for p in grant['scopes'])
            and len(set(grant['scopes'])) == len(grant['scopes']), 'invalid grant scopes')
    require(isinstance(grant['expected_head'], str) and FULL_HEAD.fullmatch(grant['expected_head']), 'invalid grant HEAD')
    uuid7_timestamp_ms(grant['expected_run_id'])
    require(isinstance(grant['worker_id'], str) and 0 < len(grant['worker_id']) <= 256, 'invalid grant worker')
    require(isinstance(grant['reason'], str) and 0 < len(grant['reason']) <= 2000 and grant['reason'].strip(), 'invalid grant reason')
    require(isinstance(grant['granted_at'], str) and grant['granted_at'], 'invalid grant time')


def validate_extensions(item, repo, schema):
    if not EXTENSION_FIELDS.intersection(item):
        return
    require(schema == 3 and not item.get('submodules'), 'execution extensions require ordinary schema 3')
    extras, grants = item.get('extra_scopes'), item.get('scope_grants')
    require(isinstance(extras, dict) and extras and len(extras) <= 100 * MAX_SCOPE_GRANTS, 'invalid extra scopes')
    require(isinstance(grants, list) and 0 < len(grants) <= MAX_SCOPE_GRANTS, 'invalid grant history')
    for path, resolved in extras.items():
        require(literal_scope(path) and path not in item['scopes'] and isinstance(resolved, str)
                and os.path.isabs(resolved) and os.path.commonpath((repo, resolved)) == repo
                and os.path.normpath(resolved) == resolved, 'invalid extra scope binding')
    for grant in grants:
        grant_valid(grant)
        require(grant['expected_run_id'] == item.get('run_id') and set(grant['scopes']) <= set(extras),
                'grant identity mismatch')
    require(set(extras) == {p for grant in grants for p in grant['scopes']}, 'grant history differs from extra scopes')


def validate(data, repo, batch_id):
    require(isinstance(data, dict) and type(data.get('schema_version')) is int and data['schema_version'] in (1, 2, 3), 'unknown state schema')
    require(data.get('repo') == repo and data.get('batch_id') == batch_id, 'state identity mismatch')
    require(data.get('owner_task_id') is None or isinstance(data['owner_task_id'], str) and batch_harvest.TASK_ID_RE.fullmatch(data['owner_task_id']), 'invalid pool owner')
    require(type(data.get('max_leads')) is int and 1 <= data['max_leads'] <= 8, 'invalid capacity')
    require(isinstance(data.get('destination_ref'), str) and data['destination_ref'].startswith('refs/heads/'), 'invalid destination')
    require(data.get('status') in {'open', 'closed'} and type(data.get('halted')) is bool, 'invalid batch status')
    for key in ('initial_head', 'created_at', 'updated_at', 'cap_source'):
        require(isinstance(data.get(key), str) and data[key], f'invalid {key}')
    requests_valid(data.get('requests'))
    if data['schema_version'] != 3:
        require(not EXTENSION_FIELDS.intersection(data)
                and all(not EXTENSION_FIELDS.intersection(item) for item in data['requests']),
                'execution extensions require schema 3')
    selected_batch = any(item.get('submodules') for item in data['requests'])
    require((data['schema_version'] == 2) == selected_batch, 'selected modules require schema 2')
    require(len(json.dumps(data).encode()) <= LIMIT, 'state exceeds 1 MiB')
    for item in data['requests']:
        require('close_fingerprint' not in item and (not isinstance(item.get('checkpoint'), dict) or 'close_fingerprint' not in item['checkpoint']), 'retired close fingerprint requires explicit wbs migrate')
        require('closed' not in item or type(item['closed']) is bool, 'invalid closed state')
        if data['schema_version'] == 1 or not item.get('submodules'):
            require(not any(k in item for k in ('submodules', 'submodule_manifest', 'ownership_scopes')), 'S1 fields in an ordinary request')
        require(item.get('status') in STATES and item.get('task_id') == 'TASK__' + item['slug'], 'invalid request state')
        require(isinstance(item.get('resolved_scopes'), list) and len(item['resolved_scopes']) == len(item['scopes'])
                and all(isinstance(p, str) and os.path.isabs(p) and os.path.commonpath((repo, p)) == repo for p in item['resolved_scopes']), 'invalid scope bindings')
        validate_extensions(item, repo, data['schema_version'])
        require(isinstance(item.get('depends_on'), list), 'missing dependency list')
        if item.get('submodules'):
            require(item.get('ownership_scopes') == module_ownership(repo, item), 'invalid whole-module ownership')
            if item.get('worktree'):
                manifest = item.get('submodule_manifest')
                try:
                    batch_submodules.validate_manifest(manifest)
                except batch_submodules.SubmoduleError as exc:
                    raise Refusal('invalid submodule manifest: ' + str(exc)) from exc
                require(all(manifest.get(k) == v for k, v in dict(repo=repo, batch_id=batch_id, slug=item['slug'],
                        worktree=item['worktree'], spawn_head=item.get('spawn_head')).items()), 'module manifest work identity mismatch')
                require([m['path'] for m in manifest['modules']] == item['submodules'], 'module manifest selection mismatch')
                require(all(isinstance(item.get(k), str) and item[k] for k in ('worker_id', 'branch', 'spawn_head')),
                        'module preparation lacks bound worker identity')
                if item['status'] not in {'reserved', 'abandoned'}:
                    require(all(m['phase'] == 'prepared' for m in manifest['modules']), 'running module preparation is incomplete')
            else:
                require('submodule_manifest' not in item, 'unbound module manifest')
        if item['status'] == 'reserved':
            require(isinstance(item.get('spawn_head'), str) and finish_helper.COMMIT_RE.fullmatch(item['spawn_head']), 'invalid reservation HEAD')
        if item['status'] in {'running', 'returned', 'integrating', 'integrated', 'kept'}:
            require(all(isinstance(item.get(k), str) and item[k] for k in ('worker_id', 'worktree', 'branch', 'spawn_head')), 'missing work binding')
        for key in ('worker_id', 'worktree', 'branch', 'spawn_head', 'run_id'):
            require(item.get(key) is None or isinstance(item[key], str), f'invalid {key}')
        if item.get('worktree'):
            require(os.path.isabs(item['worktree']) and item['worktree'] == os.path.realpath(item['worktree']), 'invalid worktree path')
            require(isinstance(item.get('branch'), str) and finish_helper.BRANCH_RE.fullmatch(item['branch']), 'invalid bound branch')
        if item.get('result') is not None:
            result = item['result']
            require(isinstance(result, dict) and result.get('verdict') in {'closed', 'blocked', 'failed'}, 'invalid stored result')
            require(all(result.get(k) == item.get(k) for k in ('task_id', 'worktree', 'branch')), 'stored result identity mismatch')
        if 'creation_intent' in item:
            intent = item['creation_intent']
            require(isinstance(intent, dict) and set(intent) == {'worktree', 'branch', 'spawn_head', 'token'}
                    and isinstance(intent.get('token'), str) and re.fullmatch(r'[0-9a-f]{64}', intent['token'])
                    and item['status'] != 'queued'
                    and isinstance(intent.get('spawn_head'), str) and finish_helper.COMMIT_RE.fullmatch(intent['spawn_head'])
                    and intent['spawn_head'] == item.get('spawn_head')
                    and intent.get('worktree') == os.path.join(repo, '.claude/worktrees', 'harness-' + intent['token'][:24])
                    and intent.get('branch') == 'harness/' + intent['token'], 'invalid creation intent')
            if item.get('worktree'):
                require(all(item[k] == intent[k] for k in ('worktree', 'branch')), 'creation intent binding changed')
        if 'retention' in item:
            require(item['retention'] == retention_contract(batch_id, item), 'invalid retention identity')
        if item['status'] in {'returned', 'integrating', 'integrated', 'kept', 'recovery-required'}:
            require(isinstance(item.get('result'), dict) and item['result'].get('verdict') == 'closed'
                    and isinstance(item.get('run_id'), str) and item.get('closed') is True, 'missing closed result identity')
        if 'checkpoint' in item:
            checkpoint = item['checkpoint']
            require(isinstance(checkpoint, dict) and checkpoint.get('stage') in {'started', 'rebase', 'integrated', 'harvested'}, 'invalid checkpoint')
            require(checkpoint.get('destination_ref') == data['destination_ref'] and checkpoint.get('run_id') == item.get('run_id')
                    and checkpoint.get('closed') is True and item.get('closed') is True, 'checkpoint identity mismatch')
            require(isinstance(checkpoint.get('branch_tip'), str) and finish_helper.COMMIT_RE.fullmatch(checkpoint['branch_tip']), 'invalid checkpoint tip')
            if 'recovery_commit' in checkpoint:
                require(checkpoint['recovery_commit'] == checkpoint['branch_tip'] and re.fullmatch(r'[0-9a-f]{40}', checkpoint['recovery_commit']), 'invalid recovery commit acknowledgement')
            if checkpoint.get('harvest'):
                require(isinstance(checkpoint['harvest'], dict) and checkpoint['harvest'].get('archived') == os.path.join(repo, 'doc/harness/archive/batch', item['task_id']), 'invalid checkpoint archive')
    return data


def load_all(directory, repo):
    names = [name for name in os.listdir(directory) if name.endswith('.json')]
    require(len(names) <= MAX_BATCHES, 'batch history limit reached; retained ownership must not be discarded')
    records = {}
    ownership = {key: set() for key in ('task_id', 'worktree', 'branch', 'worker_id')}
    for name in sorted(names):
        batch_id = name[:-5]
        require(IDENT.fullmatch(batch_id), 'invalid batch filename')
        data = validate(read_json(os.path.join(directory, name)), repo, batch_id)
        records[batch_id] = data
        for item in data['requests']:
            for key in ownership:
                value = item.get(key) or item.get('creation_intent', {}).get(key)
                if value and (key == 'task_id' or item['status'] != 'integrated'):
                    require(value not in ownership[key], f'duplicate {key} ownership')
                    ownership[key].add(value)
    require(sum(any(x['status'] in LIVE for x in d['requests']) for d in records.values()) <= 1, 'multiple active pools')
    return records


def cap(repo, explicit, managed=False):
    """Managed WBS pools use development capacity; standalone pools use batch capacity.

    An explicit operational limit may further reduce managed capacity.
    The returned cap is pinned at initialization, including later prepare replays.
    """
    explicit_cap = None
    if explicit is not None:
        require(re.fullmatch(r'[0-9]+', explicit) and int(explicit) > 0, 'max-leads must be a positive integer')
        explicit_cap = min(8, int(explicit))
        if not managed:
            return explicit_cap, 'explicit'
    section, key, default = ('develop', 'fanout_cap', 4) if managed else ('batch', 'max_leads', 3)
    path = os.path.join(repo, 'doc/harness/manifest.yaml')

    def effective(value, source):
        if explicit_cap is not None and explicit_cap < value:
            return explicit_cap, 'explicit'
        return value, source

    def scalar(value):
        # Quotes and booleans deliberately do not become integers.
        if re.fullmatch(r'[0-9]+', value) and int(value) > 0:
            return effective(min(8, int(value)), 'develop.fanout_cap' if managed else 'manifest')
        return effective(default, 'default-invalid-manifest')

    if os.path.isfile(path):
        inside, child_indent = False, None
        for line in open(path, encoding='utf-8'):
            section_line = re.match(r'^' + section + r':\s*([^#]*?)(?:\s+#.*)?$', line)
            if section_line:
                value = section_line.group(1).strip()
                # A scalar-only inline mapping is also an unambiguous cap declaration.
                if re.fullmatch(r'\{[^{}]*\}', value):
                    # Commas inside quoted scalars are not mapping separators.
                    entry_pattern = r"(?:[^,\"']|\"(?:\\.|[^\"\\])*\"|'(?:''|[^'])*')+"
                    entries = re.findall(entry_pattern, value[1:-1])
                    for entry in entries:
                        field = re.fullmatch(r"\s*(?:" + key + "|'" + key + "'|\"" + key + r"\")\s*:\s*(.*?)\s*", entry)
                        if field:
                            return scalar(field.group(1))
                    return effective(default, 'default')
                inside, child_indent = not value, None
                continue
            if not inside or not line.strip() or line.lstrip().startswith('#'):
                continue
            indent = len(line) - len(line.lstrip())
            if not indent:
                inside = False
                continue
            if child_indent is None:
                child_indent = indent
            # A nested key belongs to its own mapping, never to develop/batch.
            match = re.match(r'^\s+' + key + r':\s*([^#]*?)(?:\s+#.*)?$', line)
            if indent == child_indent and match:
                return scalar(match.group(1).strip())
    return effective(default, 'default')


def no_git_operation(repo):
    for marker in ('rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'sequencer', 'BISECT_LOG', 'BISECT_START'):
        path = finish_helper._git_out(repo, 'rev-parse', '--git-path', marker).strip()
        require(not os.path.exists(path if os.path.isabs(path) else os.path.join(repo, path)), 'Git operation in progress')


def main_boundary(repo, expected=None, scopes=None):
    ref = finish_helper._git(repo, 'symbolic-ref', '-q', 'HEAD').stdout.strip()
    require(ref.startswith('refs/heads/') and (expected is None or expected == ref), 'detached or changed destination branch')
    no_git_operation(repo)
    try:
        finish_helper.require_disjoint_dirty(repo, scopes)
    except finish_helper.FinishError as exc:
        raise Refusal(str(exc)) from exc
    return ref, finish_helper._resolve_commit(repo, ref)


def module_ownership(repo, item):
    roots = [os.path.join(repo, path) for path in item['submodules']]
    scopes = item['resolved_scopes']
    require(all(any(os.path.commonpath((root, scope)) in (root, scope) for scope in scopes)
                for root in roots), 'selected module is outside request scopes')
    result = []
    for scope in scopes:
        boundary = next((root for root in roots if os.path.commonpath((root, scope)) == root), scope)
        if boundary not in result:
            result.append(boundary)
    return result


def effective_scopes(item):
    return list(item['scopes']) + list(item.get('extra_scopes', {}))


def ownership(item):
    return list(item.get('ownership_scopes', item['resolved_scopes'])) + list(item.get('extra_scopes', {}).values())


def migrate_ordinary(data):
    # Only a successful owner publication upgrades an ordinary legacy record.
    if data['schema_version'] == 1:
        data['schema_version'] = 3


def ordinary_admission_scopes(repo, requests, managed=False):
    """Explicit ordinary owner boundary shared by intake and execution."""
    if not managed or any(item.get('submodules') for item in requests):
        return None
    return [scope for item in requests for scope in
            (ownership(item) if 'resolved_scopes' in item else
             [os.path.join(repo, path) for path in effective_scopes(item)])]


def normalize_requests(items):
    result = []
    for item in items:
        normalized = {k: item.get(k, []) for k in ('slug', 'request', 'scopes', 'depends_on')}
        if item.get('submodules'):
            normalized['submodules'] = list(item['submodules'])
        result.append(normalized)
    return result


def reconcile_modules(repo, item):
    manifest = item.get('submodule_manifest')
    if manifest and manifest['witness'] is not None:
        witness = manifest['witness']
        if os.path.exists(item['worktree']):
            batch_submodules.reconcile_checkout(repo, manifest, witness, target=witness['target'])
        else:
            require(witness['target'] == 'main'
                    and finish_helper._git(repo, 'symbolic-ref', '-q', 'HEAD').stdout.strip() == witness['destination_ref']
                    and finish_helper._resolve_commit(repo, witness['destination_ref']) == witness['target_tip'],
                    'removed S1 worktree destination differs from integration witness')


def preflight(repo, item, dirty_scopes=None):
    requests = {item['slug']: effective_scopes(item)}
    require(not batch_preflight._globs_and_lists(repo, requests), 'scope names no existing path and looks like a glob or comma list')
    report = (batch_preflight.preflight(repo, requests, {item['slug']: item['submodules']})
              if item.get('submodules') else batch_preflight.preflight(repo, requests, dirty_scopes=dirty_scopes))
    require(report['verdict'] == 'ok', 'preflight refused: ' + json.dumps(report))
    resolved = [x['resolved'] for x in report['scopes']]
    expected = item.get('resolved_scopes', []) + list(item.get('extra_scopes', {}).values())
    require('resolved_scopes' not in item or resolved == expected, 'scope resolution changed')
    if item.get('submodules'):
        boundaries = report['ownership_scopes'][item['slug']]
        require('ownership_scopes' not in item or item['ownership_scopes'] == boundaries, 'module ownership changed')
        item['ownership_scopes'] = boundaries
        return resolved, report['off_limits_by_request'][item['slug']]
    return resolved, report['off_limits']


def overlap(left, right):
    return any(os.path.commonpath((a, b)) in (a, b) for a in left for b in right)


def bound(repo, item):
    require(os.path.isdir(item['worktree']), 'retained worktree is missing; preserve branch ' + item['branch'] + ' and restore the exact registered checkout before resume; do not start a replacement task')
    require(resolve_registered_worktree(repo, item['worktree']), 'worktree is not linked; inspect retained registration and use targeted pool recovery')
    ref = finish_helper._git(item['worktree'], 'symbolic-ref', '-q', 'HEAD').stdout.strip()
    require(ref == 'refs/heads/' + item['branch'], 'worktree branch changed')
    tip = finish_helper._resolve_commit(repo, ref)
    require(tip, 'retained branch commit is missing; restore the original ref/object before resume, preserving task/run evidence')
    return tip


def control(item, base=None, closed=False):
    directory = os.path.join(base or item['worktree'], 'doc/harness/tasks', item['task_id'])
    if base is not None:
        directory = base
    data = read_task_control(directory)
    require(data, 'task control missing or invalid')
    require(item.get('run_id') in (None, data['run_id']), 'task generation changed')
    if closed:
        require(task_control_status(directory, data) == 'closed', 'task is not validly closed')
    return data


def resume_control(item, observe=False):
    """Pin an unobserved running generation; otherwise require exact presence."""
    directory = os.path.join(item['worktree'], 'doc/harness/tasks', item['task_id'])
    if not item.get('run_id') and not observe:
        require(not os.path.lexists(directory), 'task appeared after recorded absence')
        return None
    if not os.path.lexists(directory):
        require(not item.get('run_id'), 'previously observed task is missing')
        return None
    proof = control(item)
    status = task_control_status(directory, proof)
    require(status != 'closed', 'closed task requires genuine final result or integration reconciliation, not resume')
    require(status in {'open', 'blocked'}, 'task control is not valid open/blocked work')
    return proof['run_id']


def no_main_task(repo):
    # Focus markers identify a session's active task, not every open task.
    # A stopped coordinator can leave valid open control without a marker.
    directory = os.path.join(repo, 'doc/harness/tasks')
    if not os.path.lexists(directory):
        return
    parents(directory)
    with os.scandir(directory) as entries:
        for entry in entries:
            if batch_harvest.TASK_ID_RE.fullmatch(entry.name):
                safe(entry.path, True)
                require(task_control_status(entry.path) != 'open', 'main checkout has an open task')


def _fingerprint_entries(path):
    """Validated archive entries shared by hashing and explicit migration."""
    parents(path)
    batch_harvest._refuse_non_regular_tree(path)
    for root, dirs, files in os.walk(path):
        safe(root, True)
        for name in files:
            safe(os.path.join(root, name))
    return batch_harvest._tree_entries(path)


def fingerprint(path):
    return 'sha256:' + hashlib.sha256(json.dumps(_fingerprint_entries(path), sort_keys=True).encode()).hexdigest()


def sync_harvest(repo, archive):
    """Make copied evidence and its publication directory entries durable."""
    def sync(path, directory=False):
        safe(path, directory)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    for root, dirs, files in os.walk(archive, topdown=False):
        for name in files:
            sync(os.path.join(root, name))
        sync(root, True)
    learnings = os.path.join(repo, 'doc/harness/learnings.jsonl')
    if os.path.lexists(learnings):
        sync(learnings)
    parent = os.path.dirname(archive)
    while parent != os.path.dirname(repo):
        sync(parent, True)
        parent = os.path.dirname(parent)


def retention_contract(batch_id, item):
    return dict(batch_id=batch_id, slug=item['slug'], spawn_head=item['spawn_head'])


def active_pool(records, batch_id):
    require(not any(key != batch_id and any(x['status'] in LIVE for x in data['requests']) for key, data in records.items()), 'another batch has an active pool')


def held(records, target):
    return [x for data in records.values() for x in data['requests'] if x is not target and x['status'] not in FREE
            and x.get('disposition') != 'never-dispatched']



def disposal_complete(repo, item):
    """A stored integrated flag cannot conceal surviving owned Git resources."""
    require(not os.path.lexists(item['worktree']), 'integrated worktree still exists')
    listing = finish_helper._git_out(repo, 'worktree', 'list', '--porcelain', '-z')
    require('worktree ' + item['worktree'] not in listing.split('\0'), 'integrated worktree is still registered')
    result = finish_helper._git(repo, 'show-ref', '--verify', '--quiet', 'refs/heads/' + item['branch'])
    require(result.returncode == 1, 'integrated branch still exists or cannot be inspected')

def observation(repo, item):
    result = {'next_action': {'queued': 'claim', 'reserved': 'bind or explicitly release unused reservation', 'running': 'observe actual worker; recorded state does not prove liveness', 'returned': 'finish', 'integrated': 'none', 'abandoned': 'retained work requires explicit disposition'}.get(item['status'], 'recover or resume with stopped worker assertion')}
    if item['status'] == 'abandoned' and not item.get('worktree'):
        result['next_action'] = ('never dispatched; no external work retained' if item.get('disposition') == 'never-dispatched'
                                 else 'unknown external work retained; explicit disposition required')
    if item['status'] == 'reserved' and item.get('worktree'):
        result['next_action'] = 'bind same-worktree bootstrap; after failed bootstrap confirm worker stopped and resume same reservation'
    if item['status'] == 'integrated':
        result.update(worktree_exists=os.path.lexists(item['worktree']),
                      branch_tip=finish_helper._resolve_commit(repo, 'refs/heads/' + item['branch']))
        try:
            disposal_complete(repo, item)
        except Exception as exc:
            result.update(identity_error=str(exc), next_action='recover; integrated resources remain: ' + str(exc))
        return result
    intent = item.get('creation_intent', {})
    if intent and not item.get('worktree'):
        result.update(worktree_exists=os.path.lexists(intent['worktree']),
                      branch_tip=finish_helper._resolve_commit(repo, 'refs/heads/' + intent['branch']),
                      next_action='prepare exact managed creation, then bind actual worker')
    if item.get('worktree'):
        result['worktree_exists'] = os.path.exists(item['worktree'])
        result['branch_tip'] = finish_helper._resolve_commit(repo, 'refs/heads/' + item['branch'])
        try:
            result['registered_tip'] = bound(repo, item)
            result['task_status'] = task_control_status(os.path.join(item['worktree'], 'doc/harness/tasks', item['task_id']))
        except Exception as exc:
            result['identity_error'] = str(exc)
            result['next_action'] = 'preserve retained branch/evidence and restore exact registration before same-task resume: ' + str(exc)
        if not result['worktree_exists'] and result['branch_tip']:
            result['next_action'] = 'recover; remaining branch: ' + item['branch']
    return result


def require_unused_task_ids(repo, requests, records):
    ids = {x['task_id'] for data in records.values() for x in data['requests']}
    for item in requests:
        task_id = 'TASK__' + item['slug']
        require(task_id not in ids
                and not os.path.lexists(os.path.join(repo, 'doc/harness/archive/batch', task_id))
                and not os.path.lexists(os.path.join(repo, 'doc/harness/tasks', task_id)),
                'task/archive identity already used')


def _extension_git(worktree, *args):
    """Read raw Git paths without optional index writes or caller Git overrides."""
    try:
        result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false', *args],
                                cwd=worktree, env=_trusted_git_env(), capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal('cannot inspect scope extension Git state') from exc
    require(result.returncode == 0 and not result.stderr, 'cannot inspect scope extension Git state')
    return result.stdout


def extension_witness(repo, data, item, head, run_id):
    require(bound(repo, item) == head, 'expected HEAD differs from bound branch')
    require(_extension_git(item['worktree'], 'rev-parse', '--verify', 'HEAD').strip().decode('ascii') == head,
            'worktree HEAD differs from expected HEAD')
    proof = control(item)
    require(proof['run_id'] == run_id and task_control_status(
        os.path.join(item['worktree'], 'doc/harness/tasks', item['task_id']), proof) in {'open', 'blocked'},
        'expected run must identify open or blocked task')
    require(not proof['closed'], 'task already has close evidence')
    require(item.get('retention') == retention_contract(data['batch_id'], item), 'bootstrap retention identity missing')
    finish_helper.validate_retention(item['worktree'], item['retention'])
    no_git_operation(item['worktree'])
    return proof


def _extension_dirty_snapshot(worktree, paths):
    """Witness only enumerated pending source bytes and index entries, or refuse."""
    require(len(paths) <= EXTENSION_DIRTY_PATH_LIMIT, 'too many dirty paths for scope extension')
    deadline = time.monotonic() + EXTENSION_SNAPSHOT_SECONDS
    remaining = EXTENSION_DIRTY_BYTE_LIMIT
    files, index = [], []
    def within_deadline():
        require(time.monotonic() <= deadline, 'dirty source snapshot time limit exceeded')
    for offset in range(0, len(paths), 32):
        within_deadline()
        # Literal pathspecs preserve filenames containing Git pathspec metacharacters.
        raw = _extension_git(worktree, '--literal-pathspecs', 'ls-files', '--stage', '-z', '--',
                             *(os.fsdecode(p) for p in paths[offset:offset + 32]))
        require(not raw or raw.endswith(b'\0'), 'invalid dirty index listing')
        for entry in raw.split(b'\0')[:-1]:
            fields, separator, path = entry.partition(b'\t')
            metadata = fields.split(b' ')
            require(separator and path in paths and len(metadata) == 3
                    and metadata[0] in {b'100644', b'100755'} and metadata[2] == b'0'
                    and FULL_HEAD.fullmatch(metadata[1].decode('ascii')), 'unsupported dirty index entry')
        index.append(raw)
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)
    for raw in paths:
        within_deadline()
        parts = os.fsdecode(raw).split('/')
        directory = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = None
        try:
            try:
                for part in parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    os.close(directory)
                    directory = child
                descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                files.append((raw, None))
                continue
            except OSError as exc:
                raise Refusal('cannot safely inspect dirty source path: ' + repr(os.fsdecode(raw))) from exc
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                    'dirty source must be a regular file without aliases')
            require(before.st_size <= remaining, 'dirty source snapshot byte limit exceeded')
            digest = hashlib.sha256()
            with os.fdopen(descriptor, 'rb') as handle:
                descriptor = None
                while True:
                    within_deadline()
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    require(remaining >= 0, 'dirty source snapshot byte limit exceeded')
                    digest.update(chunk)
                require(identity(before) == identity(os.fstat(handle.fileno())),
                        'dirty source changed while reading')
                # The pathname must still name the opened regular file.
                require(identity(before) == identity(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)),
                        'dirty source path changed while reading')
            files.append((raw, identity(before), digest.digest()))
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)
    within_deadline()
    return tuple(index), tuple(files)


def extension_paths(repo, data, item, head):
    worktree = item['worktree']
    flags = _extension_git(worktree, 'ls-files', '-v', '-z')
    require(not flags or flags.endswith(b'\0'), 'invalid Git index flag listing')
    for entry in flags.split(b'\0')[:-1]:
        require(len(entry) > 2 and entry[1:2] == b' ' and entry[0] in b'HSMRCK?hsmrck', 'invalid Git index flag listing')
        require(entry[:1] != b'S' and not entry[:1].islower(),
                'clear assume-unchanged and skip-worktree index flags before scope extension')
    base = _extension_git(worktree, 'merge-base', data['destination_ref'], head).strip()
    require(FULL_HEAD.fullmatch(base.decode('ascii')), 'invalid source/destination merge-base')
    listings = [
        _extension_git(worktree, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', base.decode('ascii'), head, '--'),
        _extension_git(worktree, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', '--cached', '--'),
        _extension_git(worktree, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', '--'),
        _extension_git(worktree, 'ls-files', '--others', '--exclude-standard', '-z'),
    ]
    old = ownership(item)
    for listing in listings:
        require(not listing or listing.endswith(b'\0'), 'invalid Git path listing')
        for raw in listing.split(b'\0')[:-1]:
            path = os.fsdecode(raw)
            if path == finish_helper.RETENTION_MARKER:
                continue  # marker witness was validated before inspecting paths
            require(literal_scope(path), 'unsafe changed path before scope extension')
            absolute = os.path.realpath(os.path.join(repo, path))
            local = os.path.realpath(os.path.join(worktree, path))
            require(os.path.relpath(local, worktree) == os.path.relpath(absolute, repo),
                    'changed path resolves differently in worktree')
            require(any(os.path.commonpath((absolute, scope)) == scope for scope in old),
                    'prior changes exceed old execution ownership: ' + repr(path))
    dirty = sorted({path for listing in listings[1:] for path in listing.split(b'\0')[:-1]
                    if path != os.fsencode(finish_helper.RETENTION_MARKER)})
    return tuple(listings), _extension_dirty_snapshot(worktree, dirty)


def extend_scopes(repo, data, item, records, args, save):
    require(data['schema_version'] in {1, 3} and not any(x.get('submodules') for x in data['requests']),
            'scope extension requires an ordinary pool')
    historical_result = item.get('result')
    resumable_result = (item.get('resuming') is True and isinstance(historical_result, dict)
                        and historical_result.get('verdict') in {'blocked', 'failed'})
    require(item['status'] == 'running' and ('result' not in item or resumable_result)
            and not any(k in item for k in ('checkpoint', 'finish_result'))
            and not item.get('closed'), 'scope extension requires running pre-result work')
    require(args.worker_stopped is True and args.worker_id == item.get('worker_id') and args.worker_id,
            'scope extension requires exact stopped bound worker')
    scopes = args.scopes
    require(isinstance(scopes, list) and 0 < len(scopes) <= 100 and all(literal_scope(p) for p in scopes)
            and len(set(scopes)) == len(scopes), 'invalid extension scopes')
    grant = dict(scopes=list(scopes), expected_head=args.expected_head, expected_run_id=args.expected_run_id,
                 worker_id=args.worker_id, reason=args.reason, granted_at=now())
    grant_valid(grant)
    require(item.get('run_id') in (None, args.expected_run_id), 'bound task generation changed')
    no_main_task(repo)
    no_git_operation(repo)
    proof = extension_witness(repo, data, item, args.expected_head, args.expected_run_id)
    paths = extension_paths(repo, data, item, args.expected_head)
    replay = any(all(old[k] == grant[k] for k in grant if k != 'granted_at') for old in item.get('scope_grants', []))
    if replay:
        return {'status': 'running', 'request': copy.deepcopy(item), 'replayed': True,
                'handoff': dict(scopes=effective_scopes(item), worker_id=item['worker_id'], run_id=args.expected_run_id,
                                worktree=item['worktree'], branch=item['branch'], task_id=item['task_id'])}
    require(len(item.get('scope_grants', [])) < MAX_SCOPE_GRANTS, 'scope grant history limit reached')
    require(not any(p in effective_scopes(item) for p in scopes), 'extension scope already granted')
    requests = {item['slug']: scopes}
    require(not batch_preflight._globs_and_lists(repo, requests), 'extension scopes must be literal paths')
    admission = ordinary_admission_scopes(repo, data['requests'], bool(data.get('owner_task_id')))
    if admission is not None:
        admission += [os.path.join(repo, path) for path in scopes]
    report = batch_preflight.preflight(repo, requests, dirty_scopes=admission)
    require(report['verdict'] == 'ok', 'extension preflight refused: ' + json.dumps(report))
    resolved = {}
    modules = {m['path'] for m in report['submodules']}
    for path, binding in zip(effective_scopes(item), ownership(item)):
        current = batch_preflight.classify_scope(repo, path, modules)
        local = batch_preflight.classify_scope(item['worktree'], path, modules)
        require(current['class'] == local['class'] == 'tracked-area' and current['resolved'] == binding
                and os.path.relpath(local['resolved'], item['worktree']) == os.path.relpath(binding, repo),
                'old scope resolution changed')
    for scope in report['scopes']:
        path, absolute = scope['path'], scope['resolved']
        require(scope['class'] == 'tracked-area', 'extension scope is outside ordinary source')
        local = batch_preflight.classify_scope(item['worktree'], path, modules)
        require(local['class'] == 'tracked-area' and os.path.relpath(local['resolved'], item['worktree'])
                == os.path.relpath(absolute, repo), 'extension scope is unsafe in worktree')
        require(not any(overlap([absolute], [os.path.join(repo, p)]) for p in report['off_limits']),
                'extension scope overlaps excluded repository')
        require(not any(overlap([absolute], ownership(other)) for other in held(records, item)),
                'extension scope collides with retained owner')
        resolved[path] = absolute
    # Repeat the exact source/control/witness observations immediately before publication.
    no_main_task(repo)
    no_git_operation(repo)
    require(proof == extension_witness(repo, data, item, args.expected_head, args.expected_run_id),
            'task control changed during scope extension')
    require(paths == extension_paths(repo, data, item, args.expected_head), 'source changed during scope extension')
    item.setdefault('extra_scopes', {}).update(resolved)
    item.setdefault('scope_grants', []).append(grant)
    item['run_id'] = args.expected_run_id
    save()
    return {'status': 'running', 'request': copy.deepcopy(item), 'replayed': False,
            'handoff': dict(scopes=effective_scopes(item), worker_id=item['worker_id'], run_id=item['run_id'],
                            worktree=item['worktree'], branch=item['branch'], task_id=item['task_id'])}


def execute(args, directory, records):
    repo, batch_id = args.repo, args.batch_id
    path = os.path.join(directory, batch_id + '.json')
    if args.command == 'init':
        owner_task_id = getattr(args, 'owner_task_id', None)
        require(owner_task_id is None or batch_harvest.TASK_ID_RE.fullmatch(owner_task_id), 'invalid pool owner')
        requests = copy.deepcopy(args.requests) if hasattr(args, 'requests') else read_json(args.requests_file)
        requests_valid(requests)
        maximum, source = cap(repo, args.max_leads, managed=owner_task_id is not None)
        normalized = normalize_requests(requests)
        if batch_id in records:
            old = records[batch_id]
            require(old.get('owner_task_id') == owner_task_id, 'conflicting pool owner')
            require(normalize_requests(old['requests']) == normalized
                    and old['max_leads'] == maximum, 'conflicting existing batch')
            return {'status': 'ok', 'batch': old}
        require(len(records) < MAX_BATCHES, 'batch history limit reached')
        admission = ordinary_admission_scopes(repo, normalized, bool(owner_task_id))
        ref, head = main_boundary(repo, scopes=admission)
        require_unused_task_ids(repo, normalized, records)
        for item in normalized:
            task_id = 'TASK__' + item['slug']
            resolved, _ = preflight(repo, item, admission)
            item.update(status='queued', task_id=task_id, resolved_scopes=resolved)
        data = dict(schema_version=2 if any(x.get('submodules') for x in normalized) else 3, batch_id=batch_id, repo=repo, destination_ref=ref, initial_head=head,
                    max_leads=maximum, cap_source=source, created_at=now(), updated_at=now(), status='open', halted=False, requests=normalized)
        if owner_task_id is not None:
            data['owner_task_id'] = owner_task_id
        write_json(path, data)
        return {'status': 'ok', 'batch': data}
    require(batch_id in records, 'batch does not exist')
    data = records[batch_id]
    if args.command == 'status':
        return {'status': 'ok', 'batch': data, 'observed': {x['slug']: observation(repo, x) for x in data['requests']}}
    require(data['status'] == 'open', 'batch is closed')
    if args.command in {'claim', 'resume', 'finish', 'bind', 'bootstrap'}:
        unresolved = [x for x in data['requests'] if x['status'] in {'integrating', 'recovery-required'}]
        require(not unresolved or (args.command == 'finish' and args.resume
                and all(x['slug'] == args.slug for x in unresolved)), 'unresolved integration requires targeted recovery')
        require(finish_helper._git(repo, 'merge-base', '--is-ancestor', data['initial_head'], data['destination_ref']).returncode == 0,
                'destination no longer contains the initial HEAD')
        for completed in data['requests']:
            if completed['status'] == 'integrated':
                tip = completed.get('checkpoint', {}).get('integrated_tip') or completed.get('finish_result', {}).get('integrated_tip')
                require(tip and finish_helper._git(repo, 'merge-base', '--is-ancestor', tip, data['destination_ref']).returncode == 0,
                        'destination no longer contains integrated predecessor: ' + completed['slug'])
    def save():
        migrate_ordinary(data)
        data['updated_at'] = now()
        write_json(path, data)
    def capacity(target):
        active_pool(records, batch_id)
        require(sum(x is not target and x['status'] in {'reserved', 'running'} for x in data['requests']) < data['max_leads'], 'capacity exhausted')
    admission = ordinary_admission_scopes(repo, data['requests'], bool(data.get('owner_task_id')))
    if args.command == 'claim':
        require(not data['halted'], 'batch halted; recover retained integration first')
        active_pool(records, batch_id)
        _, head = main_boundary(repo, data['destination_ref'], admission)
        no_main_task(repo)
        claims, reasons = [], {}
        slots = data['max_leads'] - sum(x['status'] in {'reserved', 'running'} for x in data['requests'])
        available = getattr(args, 'available_slots', None)
        if available is not None:
            require(isinstance(available, int) and not isinstance(available, bool)
                    and available >= 0, 'available slots must be a nonnegative integer')
            slots = min(slots, available)
        by_slug = {x['slug']: x for x in data['requests']}
        from parallel_dispatch import independent_order
        ready = [item for item in data['requests'] if item['status'] == 'queued'
                 and all(by_slug[dep]['status'] == 'integrated' for dep in item['depends_on'])
                 and not any(overlap(ownership(item), ownership(x)) for x in held(records, item))]
        ordered = independent_order(ready, ownership)
        ordered += [item for item in data['requests'] if item not in ordered]
        for item in ordered:
            if item['status'] != 'queued':
                continue
            reason = None
            if slots <= 0:
                reason = 'capacity: available=' + str(slots) + ', max_leads=' + str(data['max_leads'])
            elif any(by_slug[dep]['status'] != 'integrated' for dep in item['depends_on']):
                reason = 'dependencies: ' + ', '.join(dep for dep in item['depends_on'] if by_slug[dep]['status'] != 'integrated')
            elif any(overlap(ownership(item), ownership(x)) for x in held(records, item)):
                owner = next(x for x in held(records, item) if overlap(ownership(item), ownership(x)))
                reason = 'retained scope ownership: ' + owner['slug'] + ' (' + ', '.join(owner['scopes']) + ')'
            if reason:
                reasons[item['slug']] = reason
                continue
            _, off_limits = preflight(repo, item, admission)
            item.update(status='reserved', spawn_head=head, off_limits=off_limits, reserved_at=now())
            claims.append(copy.deepcopy(item))
            slots -= 1
        save()
        return {'status': 'ok', 'claims': claims, 'reasons': reasons, 'max_leads': data['max_leads']}
    if args.command == 'close':
        require(all(x['status'] in {'integrated', 'abandoned'} for x in data['requests']), 'unfinished work or cleanup remains')
        require(all(x['status'] == 'integrated' or not (x.get('checkpoint', {}).get('integrated_tip')
                    or x.get('finish_result', {}).get('integrated_tip')) for x in data['requests']), 'integrated cleanup remains')
        require(not data['halted'], 'recovery remains unresolved')
        for completed in data['requests']:
            if completed['status'] == 'integrated':
                disposal_complete(repo, completed)
        data['status'] = 'closed'
        save()
        abandoned = [x for x in data['requests'] if x['status'] == 'abandoned']
        disposition = ('retained-abandonment' if any(x.get('disposition') != 'never-dispatched' for x in abandoned)
                       else 'never-dispatched-abandonment' if abandoned else 'integrated')
        return {'status': 'closed', 'disposition': disposition, 'batch': data}
    item = next((x for x in data['requests'] if x['slug'] == args.slug), None)
    require(item is not None, 'unknown slug')
    if args.command in {'bootstrap', 'bind'} and item.get('creation_intent'):
        intent = item['creation_intent']
        require(args.worktree == intent['worktree'] and args.branch == intent['branch'], 'managed creation binding changed')
    if args.command == 'extend':
        return extend_scopes(repo, data, item, records, args, save)
    if args.command == 'bootstrap':
        require(not data['halted'] and item['status'] == 'reserved' and not item.get('resuming'), 'bootstrap requires fresh unhalted reservation')
        no_main_task(repo)
        require(os.path.isabs(args.worktree), 'bootstrap requires absolute worktree')
        worktree = os.path.realpath(args.worktree)
        candidate = dict(item, worktree=worktree, branch=args.branch)
        require(bound(repo, candidate) == item['spawn_head'], 'bootstrap HEAD differs from reservation')
        for other in held(records, item):
            require(other.get('worktree') != worktree and other.get('branch') != args.branch, 'worktree/branch already owned')
        require(not os.path.lexists(os.path.join(worktree, 'doc/harness/tasks', item['task_id'])), 'bootstrap started task before binding')
        payload = retention_contract(batch_id, item)
        marker = os.path.join(worktree, finish_helper.RETENTION_MARKER)
        if os.path.lexists(marker):
            require(not finish_helper.lead_status(worktree, payload), 'bootstrap modified source before binding')
        else:
            require(not finish_helper._status_lines(worktree), 'bootstrap modified source before binding')
            require(not finish_helper._git_out(worktree, 'ls-files', '--', finish_helper.RETENTION_MARKER).strip()
                    and finish_helper._git(worktree, 'check-ignore', '--no-index', '-q', '--', finish_helper.RETENTION_MARKER).returncode == 1,
                    'bootstrap marker must be untracked and nonignored')
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as handle:
                handle.write(finish_helper.retention_bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
        return {'status': 'bootstrapped', 'marker': marker, 'payload': payload, 'worktree': worktree, 'branch': args.branch, 'spawn_head': item['spawn_head']}
    if args.command == 'bind':
        require(item['status'] == 'reserved', 'bind requires reservation')
        require(not data['halted'], 'batch halted; recover before authorizing worker')
        no_main_task(repo)
        require(args.worker_id and len(args.worker_id) <= 256 and os.path.isabs(args.worktree), 'invalid worker/worktree')
        require(finish_helper._git(repo, 'check-ref-format', '--branch', args.branch).returncode == 0, 'invalid branch')
        worktree = os.path.realpath(args.worktree)
        for other in held(records, item):
            require((other.get('worktree') or other.get('creation_intent', {}).get('worktree')) != worktree
                    and (other.get('branch') or other.get('creation_intent', {}).get('branch')) != args.branch
                    and other.get('worker_id') != args.worker_id, 'worktree/branch/worker already owned')
        if item.get('submodules') and item.get('worktree'):
            require(worktree == item['worktree'] and args.branch == item['branch']
                    and (item.get('resuming') or args.worker_id == item['worker_id']),
                    'module preparation requires exact worker, worktree and branch; resume preserves worktree and branch')
        candidate = dict(item, worktree=worktree, branch=args.branch, worker_id=args.worker_id)
        marker = os.path.join(worktree, finish_helper.RETENTION_MARKER)
        if os.path.lexists(marker):
            candidate['retention'] = retention_contract(batch_id, item)
            finish_helper.validate_retention(worktree, candidate['retention'])
        if item.get('resuming'):
            require(worktree == item['worktree'] and args.branch == item['branch'], 'resume must use original worktree and branch')
            bound(repo, candidate)
            resume_control(candidate)
            if item.get('retention'):
                finish_helper.validate_retention(worktree, item['retention'])
        else:
            require(bound(repo, candidate) == item['spawn_head'], 'bootstrap HEAD differs from reservation')
            require(not finish_helper.lead_status(worktree, candidate.get('retention')), 'bootstrap modified source before binding')
            require(not os.path.exists(os.path.join(worktree, 'doc/harness/tasks', item['task_id'])), 'bootstrap started task before binding')
        if item.get('submodules'):
            if item.get('resuming'):
                batch_submodules.validate(repo, worktree, item['submodule_manifest'], 'resume')
            else:
                if 'submodule_manifest' not in candidate:
                    descriptions = batch_submodules.selection(repo, item['submodules'])
                    candidate['submodule_manifest'] = batch_submodules.manifest(
                        repo, worktree, descriptions, batch_id=batch_id, slug=item['slug'], spawn_head=item['spawn_head'],
                    )
                item.update(candidate)
                save()  # exact reservation/worker and preparation intent before cloning
                def persist_preparation(manifest):
                    require(manifest is item['submodule_manifest'], 'preparation manifest identity changed')
                    save()
                batch_submodules.prepare(repo, worktree, item['submodule_manifest'], persist_preparation)
            require(all(m['phase'] == 'prepared' for m in item['submodule_manifest']['modules']), 'module preparation is incomplete')
        item.update(candidate)
        item['status'] = 'running'
    elif args.command == 'result':
        require(item['status'] == 'running' and args.worker_id == item['worker_id'], 'result worker/state mismatch')
        result = copy.deepcopy(args.result) if hasattr(args, 'result') else read_json(args.result_file)
        require(isinstance(result, dict) and result.get('verdict') in {'closed', 'blocked', 'failed'}, 'invalid result verdict')
        for key in ('task_id', 'branch', 'worktree'):
            require(result.get(key) == item[key], f'result {key} mismatch')
        require(result.get('worker_id', args.worker_id) == args.worker_id, 'result worker mismatch')
        tip = bound(repo, item)
        if item.get('retention'):
            finish_helper.validate_retention(item['worktree'], item['retention'])
        commit = result.get('commit')
        require(commit is None or (isinstance(commit, str) and finish_helper.COMMIT_RE.fullmatch(commit)), 'invalid result commit')
        if item.get('submodule_manifest'):
            batch_submodules.validate(repo, item['worktree'], item['submodule_manifest'],
                                      'finish' if result['verdict'] == 'closed' else 'resume')
        if result['verdict'] == 'closed':
            require(commit and finish_helper._resolve_commit(repo, commit) == tip, 'closed result commit is not branch tip')
            proof = control(item, closed=True)
        else:
            proof = read_task_control(os.path.join(item['worktree'], 'doc/harness/tasks', item['task_id']))
            if proof:
                control(item)
        if proof:
            require(result.get('run_id', proof['run_id']) == proof['run_id'], 'result run mismatch')
            item.update(run_id=proof['run_id'], closed=proof['closed'])
        else:
            require(result.get('run_id') is None, 'result claims missing task run')
            require(not os.path.lexists(os.path.join(item['worktree'], 'doc/harness/tasks', item['task_id'])), 'task control missing or invalid')
            item['run_id'] = None
        item.update(result=result, status='returned' if result['verdict'] == 'closed' else result['verdict'], returned_at=now())
    elif args.command == 'release':
        require(args.worker_stopped and args.no_external_work, 'release requires stopped/no-external-work assertions')
        require(item['status'] == 'reserved' and not item.get('worktree') and not item.get('creation_intent'), 'only unused reservations may be released')
        item['status'] = 'queued'
        item.pop('spawn_head', None)
    elif args.command == 'abandon':
        require(args.worker_stopped and args.reason.strip(), 'abandon requires stopped assertion and reason')
        require(item['status'] != 'integrated', 'cannot abandon this state')
        require(not (item.get('checkpoint', {}).get('integrated_tip') or item.get('finish_result', {}).get('integrated_tip')), 'integrated cleanup requires recovery')
        if item.get('checkpoint') or item['status'] in {'integrating', 'recovery-required', 'kept'}:
            checkpoint = item.get('checkpoint', {})
            main_boundary(repo, data['destination_ref'], admission)
            require(checkpoint.get('destination_ref') == data['destination_ref'] and checkpoint.get('branch_tip'), 'unknown integration requires recovery')
            tip = bound(repo, item)
            require(tip == checkpoint['branch_tip'], 'branch changed; integration requires recovery')
            no_git_operation(item['worktree'])
            require(finish_helper._git(repo, 'merge-base', '--is-ancestor', tip, data['destination_ref']).returncode == 1,
                    'integrated or unknown outcome requires recovery')
            require(item.get('finish_result', {}).get('status') in {'conflict', 'ff-refused', 'kept'}, 'unknown integration requires recovery')
            require(not item.get('finish_result', {}).get('integrated_tip') and checkpoint.get('stage') in {'started', 'rebase'}, 'post-effect cleanup requires recovery')
            control(item, closed=True)
        item.update(disposition='never-dispatched' if item['status'] == 'queued' else 'retained-work',
                    status='abandoned', abandonment_reason=args.reason, worker_stopped=True)
        data['halted'] = any(x['status'] in {'integrating', 'recovery-required'} for x in data['requests'])
    elif args.command == 'resume':
        require(args.worker_stopped and (item['status'] in {'blocked', 'failed', 'running'}
                or (item['status'] == 'reserved' and item.get('resuming') and item.get('worktree'))),
                'resume requires a stopped retained worker')
        require(not data['halted'], 'batch halted')
        no_main_task(repo)
        capacity(item)
        main_boundary(repo, data['destination_ref'], admission)
        bound(repo, item)
        run_id = resume_control(item, observe=item['status'] == 'running')
        require(not any(overlap(ownership(item), ownership(x)) for x in held(records, item)), 'retained scope collision')
        preflight(repo, item, admission)
        if item.get('submodule_manifest'):
            batch_submodules.validate(repo, item['worktree'], item['submodule_manifest'], 'resume')
        item.update(status='reserved', resuming=True, worker_stopped=True, run_id=run_id)
        save()
        handoff = dict(worktree=item['worktree'], branch=item['branch'], task_id=item['task_id'], run_id=item.get('run_id'),
                       worker_id=item['worker_id'], agent='task-lead-resume', fresh_run=False, scopes=effective_scopes(item))
        if item.get('submodule_manifest'):
            handoff.update(submodules=copy.deepcopy(item['submodules']), submodule_manifest=copy.deepcopy(item['submodule_manifest']))
        return {'status': 'reserved', 'handoff': handoff}
    elif args.command == 'finish':
        require(not data['halted'] or args.resume, 'batch halted; recover first')
        require(item['status'] == 'returned' or (args.resume and item['status'] in {'kept', 'recovery-required'}), 'finish requires closed returned result')
        active_pool(records, batch_id)
        reconcile_modules(repo, item)
        main_boundary(repo, data['destination_ref'], admission)
        bound(repo, item)
        control(item, closed=True)
        item['status'] = 'integrating'
        branch_tip = bound(repo, item)
        previous_checkpoint = item.get('checkpoint')
        # Retrying an exact checkpoint must not erase its rebase/ack/archive
        # proof before the next Git effect has produced a fresh checkpoint.
        if not (args.resume and previous_checkpoint and previous_checkpoint.get('branch_tip') == branch_tip):
            item['checkpoint'] = dict(stage='started', branch_tip=branch_tip, destination_ref=data['destination_ref'], run_id=item['run_id'], closed=True)
        save()
        def checkpoint(stage, result):
            require(result['destination_ref'] == data['destination_ref'], 'destination changed')
            control(item, closed=True)
            result.update(stage=stage, run_id=item['run_id'], closed=True)
            if stage == 'harvested':
                archive = result['harvest']['archived']
                control(item, base=archive, closed=True)
                result['archive_fingerprint'] = fingerprint(archive)
                sync_harvest(repo, archive)
            item['checkpoint'] = result
            save()
            return copy.deepcopy(result)
        options = {'checkpoint': checkpoint}
        if admission is not None:
            options['scopes'] = ownership(item)
        if item.get('submodule_manifest'):
            def persist_modules(manifest):
                require(manifest is item['submodule_manifest'], 'integration manifest identity changed')
                save()
            options.update(submodules=item['submodule_manifest'], persist_submodules=persist_modules)
        if item.get('retention'):
            options['retention'] = item['retention']
        result = finish_helper.finish(repo, SimpleNamespace(worktree=item['worktree'], branch=item['branch'], task_id=item['task_id'], commit=item['result']['commit'], resume=args.resume), **options)
        item['finish_result'] = result
        item['status'] = 'integrated' if result['status'] == 'integrated' else 'kept'
        if result['status'] in {'conflict', 'ff-refused', 'error'}:
            data['halted'] = True
            item['status'] = 'recovery-required'
        if result['status'] == 'integrated':
            data['halted'] = any(x['status'] in {'integrating', 'recovery-required'} for x in data['requests'])
        save()
        return result
    elif args.command == 'recover':
        require(args.worker_stopped and item['status'] in {'integrating', 'kept', 'recovery-required', 'integrated'}, 'recover requires stopped integration')
        checkpoint = item.get('checkpoint', {})
        recovery_commit = getattr(args, 'recovery_commit', None)
        if recovery_commit is not None:
            require(isinstance(recovery_commit, str) and re.fullmatch(r'[0-9a-f]{40}', recovery_commit), 'recovery_commit must be a full 40-character commit')
            require(item['status'] != 'integrated' and checkpoint.get('stage') in {'started', 'rebase'}
                    and not any(checkpoint.get(k) for k in ('integrated_tip', 'harvest', 'archive_fingerprint')),
                    'recovery_commit requires preintegration checkpoint')
            main_boundary(repo, data['destination_ref'], admission)
            require(bound(repo, item) == recovery_commit, 'recovery_commit differs from current branch tip')
            no_git_operation(item['worktree'])
            require(not finish_helper.lead_status(item['worktree'], item.get('retention')), 'recovery worktree is dirty')
            require(finish_helper._git(repo, 'merge-base', '--is-ancestor', data['destination_ref'], recovery_commit).returncode == 0,
                    'recovery_commit does not contain current destination')
            control(item, closed=True)
            checkpoint.update(branch_tip=recovery_commit, stage='rebase', recovery_commit=recovery_commit)
            save()  # explicit operator acknowledgement precedes any resumed effect
        try:
            reconcile_modules(repo, item)
            main_boundary(repo, data['destination_ref'], admission)
            tip = checkpoint.get('branch_tip')
            require(tip and checkpoint.get('destination_ref') == data['destination_ref'], 'exact post-rebase tip missing')
            require(checkpoint.get('run_id') == item.get('run_id') and checkpoint.get('closed') is True and item.get('closed') is True, 'checkpoint task identity mismatch')
            branch_tip = finish_helper._resolve_commit(repo, 'refs/heads/' + item['branch'])
            require(not branch_tip or branch_tip == tip, 'branch tip changed since checkpoint')
            ancestry = finish_helper._git(repo, 'merge-base', '--is-ancestor', tip, data['destination_ref']).returncode
            require(ancestry in {0, 1}, 'integration ancestry could not be determined')
            integrated = ancestry == 0
            if integrated:
                checkpoint['integrated_tip'] = tip
                if checkpoint.get('stage') in {'started', 'rebase'}:
                    checkpoint['stage'] = 'integrated'
            if (os.path.lexists(item['worktree']) and not item.get('submodule_manifest')
                    and batch_harvest.ordinary_unregistered(repo, item['worktree'])):
                require(integrated and checkpoint.get('integrated_tip') == tip
                        and checkpoint.get('archive_fingerprint'), 'integration/archive proof missing for residue')
                archive = checkpoint['harvest']['archived']
                require(fingerprint(archive) == checkpoint['archive_fingerprint'], 'archive fingerprint changed')
                control(item, base=archive, closed=True)
                batch_harvest.remove_ordinary_residue(repo, item['worktree'], item['task_id'], tip, checkpoint)
            if os.path.exists(item['worktree']):
                require(bound(repo, item) == tip, 'worktree tip changed')
                control(item, closed=True)
                # A retained worktree can safely retry harvest through the existing helper.
                if checkpoint.get('archive_fingerprint'):
                    archive = checkpoint['harvest']['archived']
                    require(fingerprint(archive) == checkpoint['archive_fingerprint'], 'archive fingerprint changed')
                    control(item, base=archive, closed=True)
                item['status'] = 'returned'
                item['recovery_action'] = 'finish --resume'
            else:
                if item.get('submodule_manifest'):
                    batch_submodules.preserved_proof(repo, item['submodule_manifest'])
                require(integrated and checkpoint.get('integrated_tip') == tip and checkpoint.get('archive_fingerprint'), 'integration/archive proof missing after removal')
                archive = checkpoint['harvest']['archived']
                require(fingerprint(archive) == checkpoint['archive_fingerprint'], 'archive fingerprint changed')
                control(item, base=archive, closed=True)
                listing = finish_helper._git_out(repo, 'worktree', 'list', '--porcelain')
                require('worktree ' + item['worktree'] + '\n' not in listing, 'missing worktree still registered')
                if branch_tip:
                    deleted = finish_helper._git(repo, 'branch', '-d', item['branch'])
                    require(deleted.returncode == 0, 'remaining branch cleanup refused: ' + item['branch'])
                item['status'] = 'integrated'
                item['recovery_action'] = 'none'
            data['halted'] = any(x['status'] in {'integrating', 'recovery-required'} for x in data['requests'])
        except Exception as exc:
            item.update(status='recovery-required', recovery_action=str(exc))
            data['halted'] = True
            save()
            raise Refusal('recovery required: ' + str(exc)) from exc
    save()
    return {'status': item['status'], 'request': item}



def acquire_lock(directory):
    """Acquire the one batch ownership lock, shared by CLI and MCP."""
    path = os.path.join(directory, '.lock')
    if os.path.lexists(path):
        safe(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and not info.st_mode & 0o022, 'unsafe lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Busy('batch lock is busy; retry') from exc
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def locked_records(repo, create=False, readonly=False):
    directory = os.path.join(repo, 'doc/harness/runtime/batches')
    parents(directory, create=create)
    fd = None if readonly else acquire_lock(directory)
    try:
        yield directory, load_all(directory, repo)
    finally:
        if fd is not None:
            os.close(fd)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=None)
    parser.add_argument('--batch-id', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--requests-file', required=True)
    init.add_argument('--max-leads')
    init.add_argument('--owner-task-id', help='Owning WBS execution task; immutable when supplied')
    for name in ('status', 'close'):
        commands.add_parser(name)
    claim = commands.add_parser('claim')
    claim.add_argument('--available-slots', type=int,
                       help='Actual free host agent slots; further bounds lead reservations.')
    for name in ('bootstrap', 'bind', 'result', 'finish', 'recover', 'resume', 'abandon', 'release'):
        command = commands.add_parser(name)
        command.add_argument('--slug', required=True)
        if name in {'bind', 'result'}:
            command.add_argument('--worker-id', required=True)
        if name in {'bootstrap', 'bind'}:
            command.add_argument('--worktree', required=True)
            command.add_argument('--branch', required=True)
        if name == 'result':
            command.add_argument('--result-file', required=True)
        if name == 'finish':
            command.add_argument('--resume', action='store_true')
        if name == 'recover':
            command.add_argument('--recovery-commit', help='Explicitly acknowledge the exact manually resolved preintegration tip')
        if name in {'recover', 'resume', 'abandon', 'release'}:
            command.add_argument('--worker-stopped', action='store_true')
        if name == 'release':
            command.add_argument('--no-external-work', action='store_true')
        if name == 'abandon':
            command.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    lock = None
    try:
        require(IDENT.fullmatch(args.batch_id), 'invalid batch id')
        args.repo = os.path.realpath(args.repo or find_repo_root())
        directory = os.path.join(args.repo, 'doc/harness/runtime/batches')
        parents(directory, create=args.command == 'init')
        if args.command != 'status':
            lock = acquire_lock(directory)
        records = load_all(directory, args.repo)
        result = execute(args, directory, records)
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') not in {'error', 'kept', 'conflict', 'ff-refused'} else 3
    except Busy as exc:
        print(json.dumps({'status': 'busy', 'reason': str(exc)}))
        return 3
    except (Refusal, ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'refused', 'reason': str(exc)}))
        return 3
    except Exception as exc:
        print(json.dumps({'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'}))
        return 1
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == '__main__':
    raise SystemExit(main())
