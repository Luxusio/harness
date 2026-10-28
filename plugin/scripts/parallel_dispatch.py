#!/usr/bin/env python3
"""Stateless, shared within-task lane admission for Claude and Codex.

``schedule(snapshot)`` accepts ``acs`` (id/files/tests/depends_on), ``progress``
(keyed by AC id), ``live_agents`` (id/role/optional ac_id and paths),
``host_capacity``, optional ``ac_cap`` (default 4, maximum 8), and optional
``repo`` for real-path ownership checks. Inventory MUST include coordinators,
other live agents, and reserved slots, even before their agents start.

Progress statuses: pending, running, verifying, completed, failed, blocked,
reservation_failed. Completed requires verified=true, workers_released=true,
and no live agents for that AC. Unreleased writers retain their entire scope.
A released running lane retains its AC slot and paths until verification ends.

Results are proposals, not reservations. The caller must reserve each entire
worker group against fresh host inventory before spawning. On reservation
failure, spawn none, record reservation_failed, and resnapshot. No task, Goal,
receipt, or scheduling ledger is written here. Pass JSON on stdin or --input.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys


class DispatchError(ValueError):
    """An invalid snapshot cannot safely authorize dispatch."""


def _require(condition, message):
    if not condition:
        raise DispatchError(message)


def _integer(value, label, minimum, maximum=None):
    _require(type(value) is int and value >= minimum
             and (maximum is None or value <= maximum),
             f'{label} must be an integer in {minimum}..{maximum or "unbounded"}')
    return value


def _paths(values, label, repo):
    _require(isinstance(values, list), f'{label} must be a list of literal relative paths')
    result = []
    for value in values:
        _require(isinstance(value, str) and value.strip() == value and value
                 and not value.startswith('/') and '\\' not in value
                 and not re.match(r'^[A-Za-z]:', value)
                 and not any(c in value for c in '\x00\n\r*?[]')
                 and '..' not in value.split('/'),
                 f'{label}: invalid literal relative path {value!r}')
        path = PurePosixPath(value).as_posix()
        _require(path != '.', f'{label}: repository-wide ownership is not a lane scope')
        if repo is not None:
            try:
                resolved = (repo / path).resolve()
                _require(resolved != repo and resolved.is_relative_to(repo),
                         f'{label}: path escapes repository: {value!r}')
                path = resolved.relative_to(repo).as_posix()
            except (OSError, RuntimeError) as exc:
                raise DispatchError(f'{label}: cannot resolve path {value!r}: {exc}') from exc
        if path not in result:
            result.append(path)
    return result


def _overlap(left, right):
    return any(a == b or a.startswith(b + '/') or b.startswith(a + '/')
               for a in left for b in right)


def host_slots(host):
    """Validate caller-observed capacity; this is not a reservation/auth token."""
    _require(isinstance(host, dict), 'host must be an object')
    fields = {'capacity', 'live_agents', 'reserved_slots', 'verification_reserve',
              'spawn_available', 'continuation_available'}
    _require(set(host) == fields, 'host fields: missing=' + ','.join(sorted(fields - set(host)))
             + '; unexpected=' + ','.join(sorted(set(host) - fields)))
    capacity = _integer(host['capacity'], 'capacity', 1, 256)
    reserve = _integer(host['verification_reserve'], 'verification_reserve', 1, capacity)
    from _lib import REVIEW_LENSES, QA_LENSES
    ids, coordinators, verification = set(), 0, 0
    for field in ('live_agents', 'reserved_slots'):
        _require(isinstance(host[field], list), field + ' must be an agent inventory')
        for agent in host[field]:
            _require(isinstance(agent, dict) and set(agent) == {'id', 'role'},
                     'host inventory needs exact id/role')
            identity, role = agent['id'], agent['role']
            _require(isinstance(identity, str) and identity == identity.strip() and 0 < len(identity) <= 256
                     and identity not in ids, 'host agent/reservation IDs must be unique')
            _require(isinstance(role, str) and bool(role.strip()), 'host agent role required')
            ids.add(identity)
            if role == 'coordinator':
                _require(field == 'live_agents', 'coordinator must occupy a live slot')
                coordinators += 1
            verification += role in REVIEW_LENSES | QA_LENSES
    _require(coordinators == 1, 'include exactly one live coordinator')
    _require(len(ids) <= capacity, 'host inventory exceeds capacity')
    for field in ('spawn_available', 'continuation_available'):
        _require(type(host[field]) is bool, field + ' must be boolean')
    remaining_reserve = max(0, reserve - verification)
    return max(0, capacity - len(ids) - remaining_reserve)


def independent_order(items, paths, compatible=None):
    """Prioritize a bounded disjoint pair without rewriting immutable intake."""
    for index, first in enumerate(items):
        for second in items[index + 1:]:
            if (not _overlap(paths(first), paths(second))
                    and (compatible is None or compatible(first, second))):
                return [first, second] + [item for item in items if item is not first and item is not second]
    return list(items)


def schedule(snapshot):
    """Return deterministic dispatch groups and an actionable reason per deferred AC."""
    _require(isinstance(snapshot, dict), 'snapshot must be a JSON object')
    capacity = _integer(snapshot.get('host_capacity'), 'host_capacity', 1)
    cap = _integer(snapshot.get('ac_cap', 4), 'ac_cap', 1, 8)
    repo = snapshot.get('repo')
    _require(repo is None or isinstance(repo, str) and bool(repo), 'repo must be a directory path')
    try:
        repo = Path(repo).resolve() if repo is not None else None
    except (OSError, RuntimeError, ValueError) as exc:
        raise DispatchError(f'cannot resolve repo: {exc}') from exc
    _require(repo is None or repo.is_dir(), 'repo must name an existing directory')
    raw_acs = snapshot.get('acs')
    _require(isinstance(raw_acs, list), 'acs must be a list')
    acs = {}
    for item in raw_acs:
        _require(isinstance(item, dict), 'each AC must be an object')
        aid = item.get('id')
        _require(isinstance(aid, str) and bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', aid)),
                 'each AC needs a nonempty literal id')
        _require(aid not in acs, f'duplicate AC id: {aid}')
        files = _paths(item.get('files', []), f'{aid}.files', repo)
        tests = _paths(item.get('tests', []), f'{aid}.tests', repo)
        _require(files or tests, f'{aid}: declare at least one owned file or test path')
        _require(not _overlap(files, tests), f'{aid}: implementation and test-author scopes overlap')
        deps = item.get('depends_on', [])
        _require(isinstance(deps, list) and all(isinstance(dep, str) for dep in deps)
                 and len(set(deps)) == len(deps), f'{aid}: depends_on must contain unique AC ids')
        acs[aid] = dict(files=files, tests=tests, depends_on=deps)
    # Kahn traversal also rejects self-dependencies without recursion limits.
    unresolved = {aid: set(ac['depends_on']) for aid, ac in acs.items()}
    for aid, deps in unresolved.items():
        _require(deps <= acs.keys(), f'{aid}: unknown dependency: {sorted(deps - acs.keys())}')
    while unresolved:
        ready = {aid for aid, deps in unresolved.items() if not deps}
        _require(ready, f'dependency cycle among: {", ".join(sorted(unresolved))}')
        unresolved = {aid: deps - ready for aid, deps in unresolved.items() if aid not in ready}

    progress = snapshot.get('progress', {})
    _require(isinstance(progress, dict) and progress.keys() <= acs.keys(),
             'progress must be an object keyed by declared AC ids')
    states = {}
    statuses = {'pending', 'running', 'verifying', 'completed', 'failed', 'blocked', 'reservation_failed'}
    for aid in acs:
        state = progress.get(aid, {})
        _require(isinstance(state, dict), f'{aid}: progress must be an object')
        status = state.get('status', 'pending')
        _require(isinstance(status, str) and status in statuses, f'{aid}: invalid progress status')
        released = state.get('workers_released', status == 'pending')
        verified = state.get('verified', False)
        _require(type(released) is bool and type(verified) is bool,
                 f'{aid}: workers_released and verified must be booleans')
        _require(status != 'pending' or released and not verified,
                 f'{aid}: pending cannot retain writers or claim verification')
        states[aid] = dict(status=status, released=released, verified=verified)

    agents = snapshot.get('live_agents')
    _require(isinstance(agents, list), 'live_agents must include all live/reserved slots and coordinators')
    ids, live_acs, external_claims = set(), set(), []
    coordinators = 0
    for agent in agents:
        _require(isinstance(agent, dict), 'each live agent must be an object')
        agent_id, role, aid = agent.get('id'), agent.get('role'), agent.get('ac_id')
        _require(isinstance(agent_id, str) and agent_id and agent_id not in ids,
                 'live agent ids must be nonempty and unique')
        ids.add(agent_id)
        _require(isinstance(role, str) and role, f'{agent_id}: role is required')
        coordinators += role == 'coordinator'
        _require(aid is None or isinstance(aid, str) and aid in acs,
                 f'{agent_id}: ac_id must identify a declared AC')
        if aid is not None:
            live_acs.add(aid)
        external_claims.extend(_paths(agent.get('paths', []), f'{agent_id}.paths', repo))
    _require(coordinators > 0, 'live_agents must include the coordinator slot')
    _require(len(agents) <= capacity, 'live/reserved agent inventory exceeds host_capacity')

    complete = {aid for aid, state in states.items()
                if state['status'] == 'completed' and state['verified']
                and state['released'] and aid not in live_acs}
    held = {aid for aid, state in states.items() if not state['released']} | live_acs
    active = held | {aid for aid, state in states.items()
                     if state['status'] in {'running', 'verifying', 'completed'} and aid not in complete}
    claims = list(external_claims)
    for aid in active:
        claims.extend(acs[aid]['files'] + acs[aid]['tests'])
    available = capacity - len(agents)
    remaining = available
    dispatch, deferred = [], []

    def worker_slots(aid):
        ac = acs[aid]
        return 2 if ac['files'] and ac['tests'] and capacity - coordinators >= 2 else 1

    # Only lanes admissible against the original inventory may determine priority.
    eligible = [aid for aid, ac in acs.items()
                if states[aid]['status'] == 'pending' and aid not in live_acs
                and all(dep in complete for dep in ac['depends_on'])
                and not _overlap(ac['files'] + ac['tests'], claims)
                and worker_slots(aid) <= available]
    ordered = independent_order(
        eligible, lambda aid: acs[aid]['files'] + acs[aid]['tests'],
        lambda first, second: cap - len(active) >= 2
        and worker_slots(first) + worker_slots(second) <= available)
    ordered += [aid for aid in acs if aid not in eligible]
    for aid in ordered:
        ac = acs[aid]
        if aid in complete:
            continue
        state = states[aid]
        reason = None
        if state['status'] != 'pending' or aid in live_acs:
            reason = ('awaiting own verification and writer release' if state['status'] == 'completed'
                      else f'lane {state["status"]}; explicit progress update required')
        elif any(states[dep]['status'] in {'failed', 'blocked', 'reservation_failed'} for dep in ac['depends_on']):
            reason = 'dependency failed or blocked'
        elif any(dep not in complete for dep in ac['depends_on']):
            reason = 'waiting for dependency verification and writer release'
        elif _overlap(ac['files'] + ac['tests'], claims):
            reason = 'scope overlaps retained or reserved writer ownership'
        elif len(active) >= cap:
            reason = 'AC concurrency cap reached'
        # Pair whenever both roles have work and the host can ever fit the pair.
        paired = bool(ac['files'] and ac['tests']) and capacity - coordinators >= 2
        needed = worker_slots(aid)
        if reason is None and remaining < needed:
            reason = f'need {needed} available agent slots; {remaining} available'
        if reason:
            deferred.append(dict(ac_id=aid, reason=reason))
            continue
        workers = ([dict(role='implementation', paths=ac['files']),
                    dict(role='test-author', paths=ac['tests'])] if paired else
                   [dict(role='implementation' if ac['files'] else 'test-author',
                         paths=ac['files'] + ac['tests'])])
        dispatch.append(dict(ac_id=aid, mode='paired' if paired else 'unpaired',
                             workers=workers, slots=needed,
                             reason='paired reservation' if paired else
                             ('host cannot fit a pair; explicit unpaired fallback' if ac['files'] and ac['tests']
                              else 'only one writer role has declared paths')))
        remaining -= needed
        active.add(aid)
        claims.extend(ac['files'] + ac['tests'])
    return dict(dispatch=dispatch, deferred=deferred, available_slots=available, remaining_slots=remaining)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='snapshot JSON file (default: stdin)')
    args = parser.parse_args(argv)
    try:
        if args.input:
            with args.input.open(encoding='utf-8') as stream:
                snapshot = json.load(stream)
        else:
            snapshot = json.load(sys.stdin)
        result = schedule(snapshot)
    except (DispatchError, OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
