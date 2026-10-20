"""MCP-owned worktree lifecycle, using the batch owner's single durable state."""
from __future__ import annotations

import copy
import os
import secrets
from types import SimpleNamespace

import batch_state as batch


def _call(repo, spec, directory, records, command, **kwargs):
    args = SimpleNamespace(repo=repo, batch_id=spec['batch_id'], command=command,
                           **kwargs)
    answer = batch.execute(args, directory, records)
    # init is intentionally also usable by the operational CLI.
    if command == 'init':
        records[spec['batch_id']] = answer['batch']
    return answer


def _save(directory, data):
    data['updated_at'] = batch.now()
    batch.write_json(os.path.join(directory, data['batch_id'] + '.json'), data)


def _reason(intent):
    return 'harness managed creation ' + intent['token']


def _witness(repo, item):
    intent = item['creation_intent']
    candidate = dict(item, worktree=intent['worktree'], branch=intent['branch'])
    batch.require(batch.bound(repo, candidate) == intent['spawn_head'], 'managed creation HEAD changed')
    gitdir = batch.finish_helper._git_out(intent['worktree'], 'rev-parse', '--absolute-git-dir').strip()
    lock = os.path.join(gitdir, 'locked')
    batch.safe(lock)
    with open(lock, encoding='utf-8') as handle:
        batch.require(handle.read(256).rstrip('\n') == _reason(intent), 'creation ownership witness differs')



def _creation_parent(repo):
    current = repo
    batch.safe(current, True)
    for component in ('.claude', 'worktrees'):
        current = os.path.join(current, component)
        if not os.path.lexists(current):
            os.mkdir(current, 0o700)
        batch.safe(current, True)
    return current


def _branch_witness(repo, intent):
    ref = 'refs/heads/' + intent['branch']
    batch.require(batch.finish_helper._resolve_commit(repo, ref) == intent['spawn_head'],
                  'managed creation branch HEAD changed')
    proof = batch.finish_helper._git(repo, 'reflog', 'show', '-1', '--format=%gs', ref)
    batch.require(proof.returncode == 0 and proof.stdout.rstrip('\n') == _reason(intent),
                  'creation branch ownership witness differs')


def _create_branch(repo, intent):
    created = batch.finish_helper._git(repo, 'update-ref', '--create-reflog', '-m', _reason(intent),
                                       'refs/heads/' + intent['branch'], intent['spawn_head'],
                                       '0' * len(intent['spawn_head']))
    batch.require(created.returncode == 0, 'managed branch creation failed: ' + created.stderr.strip())


def _prepare_one(repo, directory, records, data, item):
    batch.require(not item.get('resuming'), 'retained reservation must use original worktree')
    if 'creation_intent' not in item:
        token = secrets.token_hex(32)
        intent = dict(token=token, worktree=os.path.join(repo, '.claude/worktrees', 'harness-' + token[:24]),
                      branch='harness/' + token, spawn_head=item['spawn_head'])
        for other in batch.held(records, item):
            other_intent = other.get('creation_intent', {})
            batch.require(intent['worktree'] != (other.get('worktree') or other_intent.get('worktree'))
                          and intent['branch'] != (other.get('branch') or other_intent.get('branch')),
                          'managed creation identity already owned')
        batch.require(not os.path.lexists(intent['worktree'])
                      and not batch.finish_helper._resolve_commit(repo, 'refs/heads/' + intent['branch']),
                      'managed creation resource already exists')
        item['creation_intent'] = intent
        _save(directory, data)  # identity precedes every Git effect
    intent = item['creation_intent']
    _creation_parent(repo)
    exists = os.path.lexists(intent['worktree'])
    branch = batch.finish_helper._resolve_commit(repo, 'refs/heads/' + intent['branch'])
    if not exists:
        if not branch:
            _create_branch(repo, intent)
        _branch_witness(repo, intent)
        result = batch.finish_helper._git(repo, 'worktree', 'add', '--lock', '--reason', _reason(intent),
                                          intent['worktree'], intent['branch'])
        batch.require(result.returncode == 0, 'managed worktree creation failed: ' + result.stderr.strip())
    _witness(repo, item)  # matching HEAD alone never authorizes adoption
    _call(repo, {'batch_id': data['batch_id']}, directory, records, 'bootstrap', slug=item['slug'],
          worktree=intent['worktree'], branch=intent['branch'])


def _handoff(item):
    intent = item.get('creation_intent', {})
    return {key: copy.deepcopy(value) for key, value in dict(
        slug=item['slug'], task_id=item['task_id'], worktree=item.get('worktree') or intent.get('worktree'),
        branch=item.get('branch') or intent.get('branch'), spawn_head=item.get('spawn_head'),
        run_id=item.get('run_id'), worker_id=item.get('worker_id'), request=item['request'],
        scopes=item['scopes'], off_limits=item.get('off_limits', []),
        fresh_run=False, resuming=bool(item.get('resuming')),
        submodules=item.get('submodules'), submodule_manifest=item.get('submodule_manifest'),
    ).items() if value is not None}


def execute(repo, spec, operation='prepare', slug=None, worker_id=None, result=None,
            worker_stopped=False, available_slots=None, recovery_commit=None):
    """Execute one exact work-pack operation under the existing pool lock.

    prepare returns retained reservations as well as new capacity-ready handoffs.
    complete records an actual final only with stopped-writer assertion, then
    integrates, archives and disposes before attempting pool close.
    """
    repo = os.path.realpath(repo)
    import work_pack
    expected = work_pack._spec(spec.get('task_id'), spec.get('requests'), batch_id=spec.get('batch_id'))
    batch.require(expected == spec and batch.IDENT.fullmatch(spec['batch_id']), 'invalid managed batch spec')
    batch.require(operation in {'prepare', 'bind', 'complete', 'resume', 'recover', 'status'}, 'unknown managed operation')
    batch.require(recovery_commit is None or operation == 'recover', 'recovery_commit requires explicit recover operation')
    if available_slots is not None:
        batch.require(type(available_slots) is int and available_slots >= 0, 'available slots must be a nonnegative integer')
    with batch.locked_records(repo, create=operation == 'prepare', readonly=operation == 'status') as (directory, records):
        if operation == 'prepare' and spec['batch_id'] not in records:
            _call(repo, spec, directory, records, 'init', requests=spec['requests'], max_leads=None,
                  owner_task_id=spec['task_id'])
        batch.require(spec['batch_id'] in records, 'managed pool does not exist')
        data = records[spec['batch_id']]
        batch.require(data.get('owner_task_id') == spec['task_id']
                      and batch.normalize_requests(data['requests']) == spec['requests'], 'managed pool intake changed')
        reasons = {}
        answer = None
        item = next((x for x in data['requests'] if x['slug'] == slug), None)
        if operation not in {'prepare', 'status'}:
            batch.require(item is not None, 'unknown managed slug')
        if operation == 'prepare' and data['status'] != 'closed':
            answer = _call(repo, spec, directory, records, 'claim', available_slots=available_slots)
            reasons = answer['reasons']
            for pending in data['requests']:
                if pending['status'] == 'reserved' and not pending.get('resuming'):
                    _prepare_one(repo, directory, records, data, pending)
        elif operation == 'bind':
            batch.require(isinstance(worker_id, str) and worker_id.strip(), 'actual worker identity required')
            intent = item.get('creation_intent')
            batch.require(intent or item.get('resuming'), 'managed creation intent missing')
            if item['status'] == 'running':
                batch.require(worker_id == item['worker_id'], 'bind replay worker differs')
                batch.bound(repo, item)
                if item.get('retention'):
                    batch.finish_helper.validate_retention(item['worktree'], item['retention'])
                answer = {'status': 'running', 'request': copy.deepcopy(item)}
            else:
                if not item.get('resuming'):
                    _witness(repo, item)
                answer = _call(repo, spec, directory, records, 'bind', slug=slug, worker_id=worker_id,
                               worktree=item.get('worktree') or intent['worktree'], branch=item.get('branch') or intent['branch'])
        elif operation in {'complete', 'recover', 'resume'}:
            batch.require(worker_stopped is True, 'explicit stopped worker and all nested writers assertion required')
            if operation == 'resume':
                answer = _call(repo, spec, directory, records, 'resume', slug=slug, worker_stopped=True)
            else:
                if operation == 'complete':
                    batch.require(isinstance(result, dict) and worker_id == item.get('worker_id'), 'result worker mismatch')
                    if item.get('result') is not None and item['status'] != 'running':
                        batch.require(result == item['result'], 'completion replay differs from recorded result')
                    else:
                        answer = _call(repo, spec, directory, records, 'result', slug=slug, worker_id=worker_id, result=result)
                    item['worker_stopped'] = True
                    _save(directory, data)
                checkpoint = item.get('checkpoint')
                if checkpoint and checkpoint.get('stage') == 'started' and not checkpoint.get('recovery_commit') and recovery_commit is None:
                    batch.require(checkpoint.get('branch_tip') == batch.finish_helper._resolve_commit(repo, item['result']['commit']),
                                  'unacknowledged initial integration tip differs from reported result')
                if item['status'] in {'integrating', 'kept', 'recovery-required'}:
                    answer = _call(repo, spec, directory, records, 'recover', slug=slug, worker_stopped=True,
                                   recovery_commit=recovery_commit)
                else:
                    batch.require(recovery_commit is None, 'recovery_commit requires interrupted integration')
                if item['status'] == 'returned':
                    if checkpoint:
                        batch.require(batch.bound(repo, item) == item['checkpoint']['branch_tip'],
                                      'branch tip differs from exact integration checkpoint')
                    else:
                        batch.require(batch.bound(repo, item) == batch.finish_helper._resolve_commit(repo, item['result']['commit']),
                                      'branch tip differs from reported result before integration')
                    answer = _call(repo, spec, directory, records, 'finish', slug=slug, resume=bool(checkpoint))
                if item['status'] == 'integrated':
                    batch.disposal_complete(repo, item)
                if data['status'] != 'closed' and all(x['status'] == 'integrated' for x in data['requests']):
                    answer = _call(repo, spec, directory, records, 'close')
        handoffs = [_handoff(x) for x in data['requests'] if x['status'] == 'reserved']
        for handoff in handoffs:
            handoff['retention'] = batch.retention_contract(data['batch_id'], next(x for x in data['requests'] if x['slug'] == handoff['slug']))
        observed = ({x['slug']: batch.observation(repo, x) for x in data['requests']}
                    if operation == 'status' else {})
        residuals = [x['slug'] for x in data['requests'] if x['status'] == 'integrated'
                     and observed.get(x['slug'], {}).get('identity_error')]
        status = 'closed' if data['status'] == 'closed' else 'ok'
        if item is not None and item['status'] in {'blocked', 'failed', 'kept', 'recovery-required', 'integrating'}:
            status = item['status']
        if residuals:
            status = 'recovery-required'
        return dict(status=status, batch_id=data['batch_id'],
                    batch=copy.deepcopy(data), handoffs=handoffs, reasons=reasons, operation_result=answer,
                    observed=observed,
                    next_action=('inspect retained integrated resources: ' + ', '.join(residuals) if residuals
                                 else 'recover the exact retained work with stopped writers' if status in {'kept', 'recovery-required', 'integrating'}
                                 else 'resolve retained worker blocker before resume' if status in {'blocked', 'failed'}
                                 else 'bind returned handoffs to actual workers' if handoffs
                                 else 'pool integration and disposal complete' if status == 'closed'
                                 else 'await running workers or prepare dependency-ready work'))
