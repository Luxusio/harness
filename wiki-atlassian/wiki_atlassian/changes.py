"""Manifest-owned, interoperable bounded wiki notification projection."""
from __future__ import annotations

import re
import uuid
from .models import digest

NOTICES = 'wikiNotices'
WATCH = 'wikiCommentWatch'
DENIAL = 'notice-access-denied'
ID = re.compile(r'[1-9][0-9]{0,31}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def watch_pages(value):
    if value is None:
        return []
    pages = value.split(',')
    if len(value) > 329 or not 1 <= len(pages) <= 10 or len(set(pages)) != len(pages) or not all(ID.fullmatch(p) for p in pages):
        raise ValueError('Comment watch accepts up to 10 distinct positive page IDs of at most 32 digits')
    return sorted(pages)


def complete(manifest):
    return manifest.get('inventoryComplete', bool(manifest)) is True


def admitted(manifest):
    denied = set(manifest.get('unavailable', []))
    return {i: r for i, r in manifest.get('pages', {}).items()
            if complete(manifest) and ID.fullmatch(i) and i not in denied
            and r.get('type') == 'page' and r.get('status') == 'current'}


def _append(previous, pages, notices):
    old = previous.get(NOTICES, {})
    head = old.get('head', 0) + max(0, len(notices) - 64)
    events = [e for e in old.get('events', []) if 'id' not in e or e['id'] in pages]
    for notice in notices[-64:]:
        head += 1
        if head > 9223372036854775807:
            raise ValueError('Notification sequence overflow')
        events.append(dict(seq=head, **notice))
    return dict(epoch=old.get('epoch', uuid.uuid4().hex), head=head, events=events[-64:])


def _notice(kind, id, row):
    return dict(kind=kind, id=id, version=row['version'])


def documents(previous, next):
    old, new = admitted(previous), admitted(next)
    notices = []
    if complete(previous) and complete(next):
        for id, row in sorted(new.items()):
            if id not in old:
                notices.append(_notice('updated' if id in previous.get('pages', {}) else 'created', id, row))
            elif any(old[id].get(k) != row.get(k) for k in ('version', 'bodyHash', 'path', 'parentId', 'title')):
                notices.append(_notice('updated', id, row))
    if any(id not in new for id in old):
        notices.append(dict(kind='scope'))
    result = dict(next, **{NOTICES: _append(previous, new, notices)})
    if WATCH in previous:
        result[WATCH] = {id: row for id, row in previous[WATCH].items() if id in new}
    return result


def comment_fingerprint(inventory):
    rows = []
    for comment in sorted(inventory['comments'], key=lambda r: r['id']):
        row = {k: comment[k] for k in ('id', 'kind', 'parentId', 'version', 'status') if k in comment}
        context = comment.get('inlineContext', {})
        row.update({k: context[k] for k in ('resolutionStatus', 'resolutionLastModifiedAt') if k in context})
        rows.append(row)
    return digest(rows)


def comment_status(manifest, watched):
    if not watched:
        return None
    pages, checkpoints = admitted(manifest), manifest.get(WATCH, {})
    if any(id not in pages for id in watched):
        return 'unavailable'
    if not any(id in checkpoints for id in watched):
        return None
    return 'ready' if all(checkpoints.get(id, {}).get('status') == 'ready' for id in watched) else 'unavailable'


def comment_attempted_at(manifest, id):
    return manifest.get(WATCH, {}).get(id, {}).get('attemptedAt')


def comment_due(manifest, id, now, interval):
    at = comment_attempted_at(manifest, id)
    return at is None or at > now or now - at >= interval


def comments(previous, attempted_at, targets, observed):
    old, pages = previous.get(WATCH, {}), admitted(previous)
    notices = [_notice('comments', id, pages[id]) for id, hash in sorted(observed.items())
               if id in pages and old.get(id, {}).get('fingerprint') not in (None, hash)]
    watch = {}
    for id in targets:
        if id not in pages:
            continue
        row = dict(attemptedAt=attempted_at, status='ready' if id in observed else 'unavailable')
        hash = observed.get(id, old.get(id, {}).get('fingerprint'))
        if hash is not None:
            row['fingerprint'] = hash
        watch[id] = row
    retained = sorted(((i, r) for i, r in old.items() if i in pages and i not in watch), key=lambda p: (-p[1]['attemptedAt'], p[0]))
    watch.update(retained[:64-len(watch)])
    return dict(previous, **{NOTICES: _append(previous, pages, notices), WATCH: watch})


def _number(value):
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise ValueError('Invalid notification number')
    return value


def validate(manifest):
    pages = admitted(manifest)
    if NOTICES in manifest:
        state = manifest[NOTICES]
        if set(state) != {'epoch', 'head', 'events'} or not re.fullmatch('[0-9a-f]{32}', state['epoch']):
            raise ValueError('Invalid notification state')
        head = _number(state['head'])
        if not isinstance(state['events'], list) or len(state['events']) > 64:
            raise ValueError('Invalid notification events')
        last = 0
        for event in state['events']:
            seq = _number(event['seq'])
            if not last < seq <= head:
                raise ValueError('Invalid notification sequence')
            last = seq
            if event['kind'] == 'scope':
                if set(event) != {'seq', 'kind'}:
                    raise ValueError('Invalid scope notice')
            elif (set(event) != {'seq', 'kind', 'id', 'version'} or event['kind'] not in ('created', 'updated', 'comments')
                  or event['id'] not in pages or not 1 <= _number(event['version']) <= 2147483647):
                raise ValueError('Invalid document notice')
    watch = manifest.get(WATCH, {})
    if not isinstance(watch, dict) or len(watch) > 64:
        raise ValueError('Invalid comment checkpoints')
    for id, row in watch.items():
        hash = row.get('fingerprint')
        if (id not in pages or set(row) - {'attemptedAt', 'status', 'fingerprint'}
            or row['status'] not in ('ready', 'unavailable') or (hash is None and row['status'] == 'ready')
            or (hash is not None and (not isinstance(hash, str) or not HASH.fullmatch(hash)))):
            raise ValueError('Invalid comment checkpoint')
        _number(row['attemptedAt'])
