from contextlib import contextmanager
import threading

import pytest

from wiki_atlassian.config import Credentials, Project
from wiki_atlassian.models import Node, RemoteError
from wiki_atlassian import multispace


class Remote:
    def __init__(self):
        self.failure = None

    @contextmanager
    def operation(self, **options):
        yield self

    def spaces(self, key):
        return [{'key': key, 'id': {'TEAM': '10', 'DEV': '20'}[key]}]

    def scope_node(self, id, type='page'):
        return Node(id, 'Page', space_id='10' if id == '11' else '20', version=1)

    def require_scope(self, settings, id):
        node = self.scope_node(id)
        if node.space_id != settings.space_id:
            raise RemoteError('scope_outside_connection')
        return node


class Replica:
    def __init__(self, settings, remote):
        self.settings, self.remote = settings, remote
        self.ready = self.denied = False
        self.on_repair = None
        self.snapshot = 'initial'

    def synchronize(self, **kwargs):
        if self.remote.failure == self.settings.space:
            raise RemoteError('http_503')
        self.ready = True

    def status(self):
        return dict(status='ready' if self.ready and not self.denied else 'pending', ready=self.ready and not self.denied)

    def sync_failed(self, code):
        pass

    def deny(self):
        self.denied = True

    def close(self):
        pass

    def snapshot_identity(self):
        return self.snapshot if self.ready and not self.denied else None

    def search(self, args):
        prefix = '1' if self.settings.space == 'TEAM' else '2'
        return {'results': [dict(id=prefix + str(i), title='Item') for i in range(1, 4)]}

    def contains_document(self, id):
        return id.startswith('1' if self.settings.space == 'TEAM' else '2')


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(multispace, 'Replica', Replica)
    value = dict(enabled=True, site='https://wiki.invalid', spaces=[dict(key='TEAM'), dict(key='DEV')])
    project = Project(value, Credentials(value['site'], 'fixture@example.invalid', 'fixture-token'), tmp_path)
    manager = multispace.MultiSpaceKnowledge(project, Remote(), start_workers=False)
    yield manager
    manager.close()


def test_one_failed_space_does_not_stop_a_healthy_sibling(manager):
    manager.remote.failure = 'TEAM'
    with pytest.raises(RemoteError):
        manager.synchronize('TEAM')
    manager.synchronize('DEV')
    status = manager.status()
    assert status['ready'] and status['status'] == 'partial' and not status['complete']
    assert {row['key']: row['status'] for row in status['spaces']} == {'TEAM': 'failed', 'DEV': 'ready'}


def test_search_continues_across_spaces_without_duplicating_results(manager):
    for key in manager.slots:
        manager.synchronize(key)
    data = dict(query='Item', limit=2)
    ids = []
    for _ in range(4):
        response = manager.execute('search_documents', data)
        assert response.get('status') != 'error'
        ids.extend(row['id'] for row in response['results'])
        if 'nextCursor' not in response:
            break
        data['cursor'] = response['nextCursor']
    assert ids == ['11', '12', '13', '21', '22', '23']


def test_search_cursor_is_bound_to_query_scope_and_replica_snapshot(manager):
    for key in manager.slots:
        manager.synchronize(key)
    data = dict(query='Item', limit=2)
    cursor = manager.execute('search_documents', data)['nextCursor']
    for changed in ({**data, 'query': 'Other'}, {**data, 'space': 'DEV'}):
        assert manager.execute('search_documents', {**changed, 'cursor': cursor})['status'] == 'error'
    manager.slots['TEAM'].replica.snapshot = 'changed'
    assert manager.execute('search_documents', {**data, 'cursor': cursor})['code'] == 'invalid_cursor'


def test_explicit_native_space_mismatch_and_ambiguous_creation_never_write(manager):
    for key in manager.slots:
        manager.synchronize(key)
    assert manager.execute('get_document', dict(document='11', space='DEV'))['code'] == 'space_mismatch'
    assert manager.execute('create_document', dict(title='New', content='Body'))['code'] == 'selection_required'


def test_simultaneous_authentication_denials_do_not_take_opposite_slot_locks(manager):
    for key in manager.slots:
        manager.synchronize(key)
    barrier = threading.Barrier(2)
    def denial(key):
        with manager.slots[key].lock:
            barrier.wait(timeout=2)
            manager._deny_all()
    threads = [threading.Thread(target=denial, args=(key,), daemon=True) for key in manager.slots]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert not any(thread.is_alive() for thread in threads)
    assert all(slot.denied and slot.replica.denied for slot in manager.slots.values())


def test_comment_ids_do_not_become_page_urls(manager):
    manager.synchronize('TEAM')
    assert 'url' not in manager._label(dict(id='901', reference='footer:901'), 'TEAM')
    assert manager._label(dict(id='11'), 'TEAM', True)['url'].endswith('/pages/11')


def test_pending_revocation_keeps_sibling_fences_and_original_unauthorized_error(manager):
    for key in manager.slots:
        manager.synchronize(key)
    def pending():
        raise RemoteError('replica_denial_pending')
    manager.slots['TEAM'].replica.deny = pending
    manager.slots['TEAM'].service.execute = lambda *args: dict(status='error', code='http_401')
    result = manager.execute('get_document', dict(document='11'))
    assert result['code'] == 'http_401' and result['details']['scopeFence'] == 'pending'
    assert all(slot.denied for slot in manager.slots.values())
    assert manager.slots['DEV'].replica.denied and manager.slots['TEAM'].wake.is_set()
    manager.slots['TEAM'].wake.clear()
    manager._deny_all(wake=False)
    assert not manager.slots['TEAM'].wake.is_set()
