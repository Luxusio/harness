#!/usr/bin/env python3
"""Bounded, advisory projection of the existing batch owner records.

Never invokes Git, waits for the batch lock, or changes lifecycle state. A
malformed or excessive record is reported as unavailable, never as no backlog.
"""
from __future__ import annotations

import json
import os
import stat

import batch_state as owner

MAX_DIRECTORY_ENTRIES = 256
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_POOLS = 8
MAX_REQUESTS = 8
MAX_PROMPT_CHARS = 1200
MAX_PROJECTION_BYTES = 64 * 1024


def _pointer(path):
    owner.safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        owner.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                      and info.st_nlink == 1 and not info.st_mode & 0o022,
                      'unsafe Git pointer')
        raw = handle.read(4097)
    owner.require(len(raw) <= 4096, 'oversized Git pointer')
    return raw.decode('utf-8').strip()


def _control_root(repo):
    """Accept ordinary roots or exact, bidirectionally linked Git metadata."""
    root = os.path.realpath(os.fspath(repo))
    git = os.path.join(root, '.git')
    if not os.path.lexists(git) or stat.S_ISDIR(os.lstat(git).st_mode):
        return root
    line = _pointer(git)
    owner.require(line.startswith('gitdir: '), 'invalid Git pointer')
    target = os.path.abspath(os.path.join(root, line[8:]))
    owner.require(target == os.path.realpath(target), 'unsafe Git metadata')
    owner.parents(target)
    common = os.path.abspath(os.path.join(target, _pointer(os.path.join(target, 'commondir'))))
    main = os.path.dirname(common)
    owner.require(common == os.path.join(main, '.git')
                  and os.path.dirname(target) == os.path.join(common, 'worktrees')
                  and common == os.path.realpath(common), 'invalid common Git metadata')
    owner.parents(common)
    backlink = _pointer(os.path.join(target, 'gitdir'))
    owner.require(os.path.abspath(os.path.join(target, backlink)) == git,
                  'Git backlink mismatch')
    return main


def _operation(status):
    if status in {'integrating', 'recovery-required', 'kept'}:
        return 'recover'
    if status == 'returned':
        return 'complete'
    if status in {'blocked', 'failed'}:
        return 'resume'
    if status == 'abandoned':
        return 'resolve-retained-work'
    if status == 'reserved':
        return 'prepare-or-bind'
    if status == 'running':
        return 'await-worker'
    return 'prepare'


def pending(repo):
    """Return {pools, diagnostics, truncated}; statuses are reported, not proof.

    Entries expose identifiers and paths only, never request prose or evidence.
    Diagnostics are fixed codes, with no untrusted exception text. Limits are
    advisory: incomplete discovery never authorizes disposal or task completion.
    """
    result = {'pools': [], 'diagnostics': [], 'truncated': False}
    try:
        root = _control_root(repo)
        directory = os.path.join(root, 'doc/harness/runtime/batches')
        if not os.path.lexists(directory):
            # Check existing ancestors even when the leaf does not exist.
            try:
                owner.parents(directory)
            except owner.Refusal as exc:
                if not str(exc).startswith('missing state directory:'):
                    raise
            return result
        owner.parents(directory)
        names = []
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                owner.require(index < MAX_DIRECTORY_ENTRIES, 'directory limit')
                if entry.name.endswith('.json'):
                    names.append(entry.name)
        owner.require(len(names) <= owner.MAX_BATCHES, 'batch limit')
        total = 0
        ownership = {key: set() for key in ('task_id', 'worktree', 'branch', 'worker_id')}
        live_pools = 0
        for name in sorted(names):
            batch_id = name[:-5]
            owner.require(owner.IDENT.fullmatch(batch_id), 'invalid batch filename')
            path = os.path.join(directory, name)
            owner.safe(path)
            total += os.lstat(path).st_size
            owner.require(total <= MAX_TOTAL_BYTES, 'read budget')
            data = owner.validate(owner.read_json(path), root, batch_id)
            for item in data['requests']:
                for key, values in ownership.items():
                    value = item.get(key) or item.get('creation_intent', {}).get(key)
                    if value and (key == 'task_id' or item['status'] != 'integrated'):
                        owner.require(value not in values, 'duplicate ownership')
                        values.add(value)
            live_pools += any(item['status'] in owner.LIVE for item in data['requests'])
            owner.require(live_pools <= 1, 'multiple active pools')
            requests = [item for item in data['requests'] if item['status'] != 'integrated']
            if data['status'] == 'closed' and not requests:
                continue
            if len(result['pools']) >= MAX_POOLS:
                result['truncated'] = True
                continue
            result['truncated'] |= len(requests) > MAX_REQUESTS
            projection = {
                'owner_task_id': data.get('owner_task_id'), 'batch_id': batch_id,
                'status': data['status'], 'pending_requests': len(requests),
                'next_operation': 'inspect' if data['halted'] else
                    (_operation(requests[0]['status']) if requests else 'close'),
                'requests': [{
                    'slug': item['slug'], 'status': item['status'],
                    'worktree': item.get('worktree') or item.get('creation_intent', {}).get('worktree'),
                    'branch': item.get('branch') or item.get('creation_intent', {}).get('branch'),
                    'next_operation': _operation(item['status']),
                } for item in requests[:MAX_REQUESTS]],
            }
            if len(json.dumps(result).encode()) + len(json.dumps(projection).encode()) > MAX_PROJECTION_BYTES:
                result['truncated'] = True
                continue
            result['pools'].append(projection)
    except Exception:
        result['diagnostics'].append('pending-worktree-discovery-unavailable')
        result['truncated'] = True
    return result


def render_prompt_block(repo):
    """Separate capped recovery block; independent of active-task context cap."""
    state = pending(repo)
    if not state['pools'] and not state['diagnostics'] and not state['truncated']:
        return ''
    lines = ['[harness-worktree-recovery] advisory; use wbs(context/next), then dispatch lifecycle; no automatic deletion.']
    for pool in state['pools']:
        # Only allow identifier characters into the prompt, never arbitrary paths.
        owner_id = pool['owner_task_id'] or 'unassigned'
        actions = ','.join(item['slug'] + ':' + item['next_operation']
                           for item in pool['requests'][:3]) or pool['next_operation']
        line = f"batch={pool['batch_id']} owner={owner_id} pending={pool['pending_requests']} next={actions}"
        if sum(map(len, lines)) + len(lines) + len(line) > MAX_PROMPT_CHARS - 200:
            state['truncated'] = True
            break
        lines.append(line)
    if state['diagnostics']:
        lines.append('discovery unavailable; inspect durable batch state through MCP; do not infer no pending work.')
    if state['truncated']:
        lines.append('More or unreadable state may exist; authoritative MCP inspection required.')
    return '\n'.join(lines)[:MAX_PROMPT_CHARS]
