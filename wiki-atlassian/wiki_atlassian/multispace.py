"""One site's independent spaces, bounded background sync, and explicit routing."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import threading
import time

from wiki_platform import safe_metadata_read
from .config import SpaceSettings
from .cursors import decode_cursor, encode_cursor
from .models import RemoteError, digest, native_id
from .references import native_page_url_id
from .replica import Replica
from .service import WikiService, comment_ref, require


@dataclass
class Slot:
    selection: dict
    settings: object = None
    replica: object = None
    service: object = None
    failure: str | None = None
    denied: bool = False
    wake: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)


class MultiSpaceKnowledge:
    def __init__(self, project, remote, *, start_workers=True):
        self.project, self.remote = project, remote
        self.slots = {row['key']: Slot(row) for row in project.selections}
        self.stop = threading.Event()
        self.permit = threading.BoundedSemaphore(4)
        self.threads = []
        self._revocations = threading.local()
        if start_workers:
            for key in self.slots:
                thread = threading.Thread(target=self._worker, args=(key,), name='wiki-sync-' + key, daemon=True)
                thread.start()
                self.threads.append(thread)

    @contextmanager
    def _revocation_call(self):
        if getattr(self._revocations, 'operation', None) is not None:
            yield
            return
        self._revocations.operation = {}
        try:
            yield
        finally:
            del self._revocations.operation

    def _configured_fencer(self, key, slot):
        if slot.replica is not None:
            return slot.replica
        root = self.project.corpus_root(key)
        try:
            raw = safe_metadata_read(root / 'confluence' / key / '.sync/manifest.json', 16 * 1024 * 1024)
        except FileNotFoundError:
            return None
        manifest = json.loads(raw.decode('utf-8'))
        # Cached identity is used only to revoke an existing admission, never
        # to initialize a running service or grant native access.
        config = dict(enabled=True, site=self.project.credentials.base_url, space=key,
                      spaceId=native_id(manifest['spaceId']), pollIntervalSeconds=self.project.poll_interval_seconds)
        config.update({k: v for k, v in slot.selection.items() if k != 'key'})
        return Replica(SpaceSettings(config, self.project.credentials, root), self.remote)

    def _deny_all(self, wake=True):
        # A sync may call this while holding its own slot lock. Never acquire
        # sibling slot locks in opposite order during simultaneous 401s.
        for slot in self.slots.values():
            slot.denied = True
        operation = getattr(self._revocations, 'operation', None)
        if operation is not None and 'pending' in operation:
            pending = operation['pending']
        else:
            pending, fencers = {}, {}
            deadline = time.monotonic() + 3
            # Fence every available sibling before waiting on any held lease.
            for key, slot in self.slots.items():
                try:
                    fencer = self._configured_fencer(key, slot)
                    if fencer is not None:
                        fencers[key] = fencer
                        fencer.deny(timeout=0)
                except Exception:
                    pending[key] = 'replica_denial_pending'
            for key in list(pending):
                if key not in fencers:
                    continue
                try:
                    fencers[key].deny(timeout=max(0, deadline - time.monotonic()))
                    pending.pop(key)
                except Exception:
                    pass  # The original failure remains explicitly pending.
            if operation is not None:
                operation['pending'] = pending
        for key in pending:
            self.slots[key].failure = 'replica_denial_pending'
            if wake:
                self.slots[key].wake.set()
        return pending

    def synchronize(self, key):
        with self._revocation_call():
            return self._synchronize(key)

    def _synchronize(self, key):
        slot = self.slots[key]
        with slot.lock, self.remote.operation(timeout=30):
            try:
                settings = self.project.resolve(self.remote, key)
                if slot.settings is not None and settings.identity() != slot.settings.identity():
                    slot.denied = True
                    slot.replica.deny()
                    raise RemoteError('scope_metadata_changed', 'Native space selection changed; reconnect to admit it')
                if slot.replica is None:
                    slot.settings = settings
                    slot.replica = Replica(settings, self.remote)
                    slot.replica.on_repair = slot.wake.set
                    slot.replica.on_authorization_denied = lambda: self._deny_all(wake=False)
                    slot.service = WikiService(settings, self.remote, slot.replica)
                slot.replica.synchronize(authoritative_bodies=True)
                slot.failure, slot.denied = None, False
            except Exception as exc:
                slot.failure = exc.code if isinstance(exc, RemoteError) else 'sync_failed'
                if slot.replica is not None:
                    slot.replica.sync_failed(slot.failure)
                if slot.failure == 'http_401' or isinstance(exc, RemoteError) and exc.details.get('authorizationDenied') is True:
                    self._deny_all(wake=False)
                raise

    def _worker(self, key):
        slot = self.slots[key]
        while not self.stop.is_set():
            if not self.permit.acquire(timeout=.2):
                continue
            try:
                if not self.stop.is_set():
                    try:
                        self.synchronize(key)
                    except Exception:
                        pass  # Sanitized per-space state owns the failure.
            finally:
                self.permit.release()
            slot.wake.wait(self.project.poll_interval_seconds)
            slot.wake.clear()

    def close(self):
        self.stop.set()
        for slot in self.slots.values():
            slot.wake.set()
        deadline = time.monotonic() + 2
        for thread in self.threads:
            thread.join(max(0, deadline - time.monotonic()))
        for slot in self.slots.values():
            if slot.replica is not None:
                slot.replica.close()

    def status(self):
        entries = []
        for key, slot in self.slots.items():
            current = slot.replica.status() if slot.replica else {'status': 'pending', 'ready': False}
            row = {**slot.selection, **current, 'key': key, 'wholeSpace': 'rootPageId' not in slot.selection}
            if slot.settings:
                row['spaceId'] = slot.settings.space_id
            if slot.failure:
                row.update(status='failed', code=slot.failure, syncError='Automatic synchronization for this space failed')
                if slot.failure == 'replica_denial_pending':
                    row.update(authorizationDenied=True, scopeFence='pending', scopeFenceCode='replica_denial_pending')
            if slot.denied:
                row['ready'] = False
            entries.append(row)
        ready = sum(row.get('ready') is True for row in entries)
        healthy = sum(row.get('ready') is True and row.get('status') == 'ready' for row in entries)
        return dict(enabled=True, site=self.project.credentials.base_url, ready=ready > 0, complete=healthy == len(entries),
                    status='ready' if healthy == len(entries) else 'partial' if ready else 'failed' if any(row.get('status') == 'failed' for row in entries) else 'pending', spaces=entries)

    def _running(self, key):
        slot = self.slots[key]
        if slot.denied:
            raise RemoteError('space_unavailable', 'The selected space is no longer admitted')
        if slot.service is None:
            raise RemoteError('space_pending', 'The selected space is still resolving; inspect connection_status')
        return slot

    def _id(self, value):
        if isinstance(value, str) and value.isdecimal():
            return native_id(value)
        return native_page_url_id(value, self.project.credentials.base_url, allow_api=True)

    def _document_space(self, value, supplied=None, cached=False):
        id = self._id(value)
        existing = [key for key, slot in self.slots.items() if cached and not slot.denied and slot.replica and slot.replica.contains_document(id)]
        if len(existing) == 1:
            key = existing[0]
        else:
            try:
                node = self.remote.scope_node(id)
            except RemoteError as exc:
                if exc.code != 'http_404':
                    raise
                node = self.remote.scope_node(id, 'folder')
            candidates = [key for key, slot in self.slots.items() if not slot.denied and slot.settings and slot.settings.space_id == node.space_id]
            require(len(candidates) == 1, 'scope_outside_connection', 'The document is outside currently admitted configured spaces')
            key = candidates[0]
            self.remote.require_scope(self.slots[key].settings, id)
        require(supplied is None or key == supplied, 'space_mismatch', 'The explicit space and native target disagree')
        return key

    def _comment_page(self, reference):
        kind, id = comment_ref(reference, self.project.credentials.base_url)
        if kind:
            return self.remote.comment_location(id, kind)[0]
        try:
            return self.remote.comment_location(id, 'footer')[0]
        except RemoteError as exc:
            if exc.code != 'http_404':
                raise
            return self.remote.comment_location(id, 'inline')[0]

    def execute(self, operation, data):
        with self._revocation_call():
            return self._execute(operation, data)

    def _execute(self, operation, data):
        if operation == 'connection_status':
            return self.status()
        try:
            supplied = data.get('space')
            require(supplied is None or supplied in self.slots, 'invalid_request', 'Select a configured space key')
            arguments = {k: v for k, v in data.items() if k != 'space'}
            if operation == 'search_documents':
                with self.remote.operation(timeout=45):
                    return self._search(arguments, supplied)
            with self.remote.operation(timeout=45):
                if operation == 'create_document':
                    key = self._document_space(arguments['parent'], supplied) if 'parent' in arguments else supplied
                    require(key is not None, 'selection_required', 'Choose an explicit space or parent before creating a page')
                elif operation == 'update_document' and arguments.get('document', 'memory') in ('memory', 'feedback'):
                    field = 'memoryPageId' if arguments.get('document', 'memory') == 'memory' else 'feedbackPageId'
                    keys = [key for key, slot in self.slots.items() if field in slot.selection and (supplied is None or supplied == key)]
                    require(len(keys) == 1, 'selection_required', 'Choose a space with one configured recording destination')
                    key = keys[0]
                elif operation in ('get_document', 'update_document', 'list_document_versions', 'document_relations'):
                    key = self._document_space(arguments['document'], supplied, operation == 'get_document')
                elif operation == 'update_comment':
                    key = self._document_space(self._comment_page(arguments['comment']), supplied)
                elif operation in ('list_comments', 'create_comment'):
                    target = arguments['target']
                    if target.startswith(('footer:', 'inline:')) or '-comments/' in target:
                        target = self._comment_page(target)
                    key = self._document_space(target, supplied)
                else:
                    raise RemoteError('invalid_request', 'Unknown wiki operation')
                slot = self._running(key)
                result = slot.service.execute(operation, arguments)
                if result.get('code') == 'http_401' or result.get('authorizationDenied') is True:
                    pending = self._deny_all()
                    if pending:
                        result.setdefault('details', {}).update(scopeFence='pending', scopeFenceCode='replica_denial_pending', scopeFenceSpaces=pending)
                    return {**result, 'space': key, 'spaceId': slot.settings.space_id}
                return self._label(result, key, operation in ('get_document', 'create_document', 'update_document'),
                                   mutation=operation in ('create_document', 'update_document', 'create_comment', 'update_comment'))
        except Exception as exc:
            pending = {}
            if isinstance(exc, RemoteError) and (exc.code == 'http_401' or exc.details.get('authorizationDenied') is True):
                pending = self._deny_all()
            result = dict(status='error', code=exc.code if isinstance(exc, RemoteError) else 'invalid_request',
                          message=str(exc) if isinstance(exc, RemoteError) else 'The request could not be admitted in the selected spaces')
            if pending:
                result['details'] = dict(scopeFence='pending', scopeFenceCode='replica_denial_pending', scopeFenceSpaces=pending)
            return result

    def _label(self, value, key, page_response=False, mutation=False):
        # Admission precedes the service call. Revocation must not erase a write
        # that committed (or may have committed) while background sync ran.
        slot = self.slots[key] if mutation else self._running(key)
        if mutation and slot.denied:
            value = {k: v for k, v in value.items() if k in
                     ('status', 'code', 'message', 'id', 'version', 'reference', 'details',
                      'operation', 'request', 'requestHash', 'next', 'syncRequired', 'authorizationDenied')}
            value['authorizationDenied'] = True
            if value.get('status') in ('created', 'updated', 'unknown'):
                value.setdefault('next', 'The native write completed or may have completed before access was revoked. Do not repeat it; restore access before reading content.')
        result = {**value, 'space': key, 'spaceId': slot.settings.space_id}
        if page_response and 'id' in value and 'url' not in result:
            result['url'] = f"{self.project.credentials.base_url}/wiki/spaces/{key}/pages/{value['id']}"
        if isinstance(result.get('read'), dict):
            result['read'] = {**result['read'], 'space': key}
        return result

    def _search(self, arguments, supplied):
        limit = arguments.get('limit', 10)
        require(type(limit) is int and 1 <= limit <= 25)
        keys = [self._document_space(arguments['root'], supplied, True)] if arguments.get('root') else [supplied] if supplied else list(self.slots)
        snapshots = {key: None if self.slots[key].denied or self.slots[key].replica is None else self.slots[key].replica.snapshot_identity() for key in keys}
        binding = [self.project.read(), {k: v for k, v in arguments.items() if k != 'cursor'}, keys, snapshots]
        states = decode_cursor(arguments['cursor'], binding) if 'cursor' in arguments else {}
        require(set(states).issubset(keys), 'invalid_cursor')
        result, coverage = [], []
        more = False
        for key in keys:
            slot = self.slots[key]
            if snapshots[key] is None:
                coverage.append(dict(space=key, status='unavailable', readable=False, code=slot.failure or 'replica_pending'))
                continue
            status = slot.replica.status()
            readable = not slot.denied and status.get('ready') is not False
            coverage.append(dict(space=key, status='ready' if readable and not slot.failure and status.get('status') == 'ready' else 'stale' if readable else 'unavailable',
                                 readable=readable, freshness=status.get('freshness')))
            state = states.get(key, {})
            require(type(state) is dict and not set(state) - {'cursor', 'skip', 'done'}, 'invalid_cursor')
            require('done' not in state or type(state['done']) is bool, 'invalid_cursor')
            require('cursor' not in state or type(state['cursor']) is str and bool(state['cursor']), 'invalid_cursor')
            if state.get('done'):
                continue
            if len(result) >= limit:
                more = True
                continue
            request = {k: v for k, v in arguments.items() if k != 'cursor'}
            if state.get('cursor'):
                request['cursor'] = state['cursor']
            response = slot.replica.search(request)
            if response.get('status') == 'error':
                coverage[-1].update(status='unavailable', readable=False, code=response.get('code'))
                continue
            hits = response['results']
            skip = state.get('skip', 0)
            require(type(skip) is int and 0 <= skip <= len(hits), 'invalid_cursor')
            count = min(limit - len(result), len(hits) - skip)
            result.extend(self._label(row, key, True) for row in hits[skip:skip + count])
            next = response.get('nextCursor') or response.get('cursor')
            if skip + count < len(hits):
                states[key] = {**state, 'skip': skip + count}
                more = True
            elif next:
                states[key] = dict(cursor=next, skip=0)
                more = True
            else:
                states[key] = dict(done=True)
        for key in keys:
            current = None if self.slots[key].denied or self.slots[key].replica is None else self.slots[key].replica.snapshot_identity()
            require(snapshots[key] == current, 'snapshot_changed', 'Search snapshot changed; search again')
        partial = any(row['status'] != 'ready' for row in coverage)
        response = dict(results=result, spaces=coverage, partial=partial, complete=not partial and not more, truncated=more)
        if more:
            response['nextCursor'] = encode_cursor(binding, states)
        if not any(row['readable'] for row in coverage):
            response.update(status='error', code='replica_unavailable', message='No selected space has a readable replica; inspect connection_status')
        return response
