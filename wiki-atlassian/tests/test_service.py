"""Application CAS/authority/effect boundaries using deterministic native peers."""
from contextlib import contextmanager
from dataclasses import replace

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required') from error

from wiki_atlassian.config import SpaceSettings
from wiki_atlassian.models import Credentials, Node, Page, RemoteError
from wiki_atlassian.service import WikiService


class Native:
    def __init__(self):
        self.nodes = {'1': Node('1', 'Root', None, space_id='10', version=1),
                      '2': Node('2', 'Note', '1', space_id='10', version=1),
                      '3': Node('3', 'Deeper', '2', space_id='10', version=1),
                      '99': Node('99', 'Foreign', None, space_id='20', version=1)}
        self.body = '<h2>개선 의견</h2><p>Old 😀</p>'
        self.message = ''
        self.writes = []
        self.actor = {'accountId': 'actor'}
        self.before_callback = None
        self.after_commit = None

    @contextmanager
    def operation(self, **kwargs):
        yield

    def require_scope(self, settings, id):
        node = self.nodes[id]
        if node.space_id != settings.space_id or node.status != 'current':
            raise RemoteError('scope_outside_connection')
        return node

    def require_space(self, settings):
        return None

    def move_ancestry(self, settings, id):
        result = []
        while id:
            node = self.require_scope(settings, id)
            result.append(node)
            id = node.parent_id
        return result

    def page(self, id, expected_node=None, scope=None, fresh_storage=False):
        return Page(self.nodes[id], self.body, self.message)

    def ensure_no_draft(self, *args):
        pass

    def current_user(self):
        return dict(self.actor)

    def version(self, id, number, scope=None):
        return {'number': number, 'message': self.message, 'authorId': 'actor', 'createdAt': '2026-11-12T00:00:00Z'}

    def update(self, before, body, message, scope, title=None, before_effect=None, parent_id=None, **kwargs):
        if self.before_callback:
            self.before_callback(self)
        before_effect()
        self.writes.append(body)
        self.body, self.message = body, message
        self.nodes[before.node.id] = replace(before.node, title=title or before.node.title,
            version=before.node.version + 1, parent_id=parent_id or before.node.parent_id)
        if self.after_commit:
            self.after_commit(self)
        return self.page(before.node.id)

    def create(self, space_id, parent_id, title, body, message, scope, before_effect=None):
        before_effect()
        self.writes.append(body)
        self.body, self.message = body, message
        self.nodes['4'] = Node('4', title, parent_id, space_id=space_id, version=1)
        return self.page('4')


class Replica:
    def __init__(self):
        self.fail_publish = False
        self.repairs = []
        self.failures = []

    def publish_verified(self, page, before=None):
        if self.fail_publish:
            raise OSError('publication failed')

    def contains_document(self, id):
        return True

    def request_repair(self, id):
        self.repairs.append(id)

    def get_document(self, data):
        return None

    def handle_denial(self, error):
        self.failures.append(error.code)


@pytest.fixture
def service(tmp_path):
    settings = SpaceSettings({'space': 'TEAM', 'spaceId': '10', 'rootPageId': '1',
                              'memoryPageId': '2', 'feedbackPageId': '2'},
                             Credentials('https://wiki.invalid', 'fixture@example.invalid', 'fixture-private-credential'), tmp_path)
    remote, replica = Native(), Replica()
    return WikiService(settings, remote, replica), remote, replica


def test_text_update_saves_once_and_returns_native_storage(service):
    api, remote, replica = service
    result = api.execute('update_document', {'document': '2', 'version': 1, 'content': 'New 😀', 'format': 'text', 'includeBody': True})
    assert result['status'] == 'updated' and result['version'] == 2
    assert result['format'] == 'storage' and result['content'] == '<p>New 😀</p>'
    assert len(remote.writes) == 1


@pytest.mark.parametrize('data', [
    {'document': '2', 'version': 2, 'append': 'New'},
    {'document': '2', 'version': 1, 'append': 'New', 'content': '<p>Both</p>'},
    {'document': '2', 'version': 1, 'new_text': 'Missing old'},
    {'document': '2', 'version': 1, 'append': 'New', 'section': '1'},
    {'document': '2', 'version': 1, 'append': 'New', 'allowReferenceRemoval': True},
    {'document': '99', 'version': 1, 'append': 'Outside'},
])
def test_invalid_or_foreign_document_edits_never_write(service, data):
    api, remote, replica = service
    assert api.execute('update_document', data)['status'] == 'error'
    assert remote.writes == []


def test_pure_append_uses_authenticated_mention_and_existing_feedback_section(service):
    api, remote, replica = service
    result = api.execute('update_document', {'document': 'feedback', 'append': 'Scoped observation'})
    assert result['status'] == 'updated' and len(remote.writes) == 1
    assert 'ri:account-id="actor"' in remote.body and '<p>Scoped observation</p>' in remote.body
    assert remote.body.startswith('<h2>개선 의견</h2><p>Old 😀</p>')


def test_changed_body_or_account_before_effect_refuses_without_mutation(service):
    api, remote, replica = service
    remote.before_callback = lambda peer: setattr(peer, 'body', '<p>Concurrent winner</p>')
    result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>Mine</p>'})
    assert result['status'] == 'error' and not remote.writes
    remote.before_callback = lambda peer: setattr(peer, 'actor', {'accountId': 'different'})
    result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>Mine</p>'})
    assert result['status'] == 'error' and not remote.writes


def test_postcommit_identity_failure_remains_unknown_without_replay(service):
    api, remote, replica = service
    remote.after_commit = lambda peer: setattr(peer, 'actor', {'accountId': 'changed'})
    result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>Mine</p>'})
    assert result['status'] == 'unknown' and len(remote.writes) == 1
    assert result['details']['reference'] == '2' and 'never automatically repeat' in result['next']


def test_native_success_with_replica_failure_requests_repair_without_replaying(service):
    api, remote, replica = service
    replica.fail_publish = True
    result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>Mine</p>'})
    assert result['status'] == 'updated' and result['syncRequired']
    assert len(remote.writes) == 1 and replica.repairs == ['2']


def test_basedon_and_link_targets_require_current_selected_scope(service):
    api, remote, replica = service
    for basedon in ([{'document': '2', 'version': 1}], [{'document': '3', 'version': 2}], [{'document': '99', 'version': 1}]):
        result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>Derived</p>', 'basedOn': basedon})
        assert result['status'] == 'error' and not remote.writes
    remote.body = '<p><a href="https://old.invalid">Keep label</a></p>'
    result = api.execute('update_document', {'document': '2', 'version': 1, 'links': {'https://old.invalid': '99'}})
    assert result['status'] == 'error' and not remote.writes


def test_moves_must_keep_all_existing_ancestors(service):
    api, remote, replica = service
    result = api.execute('update_document', {'document': '3', 'version': 1, 'parent': '1'})
    assert result['status'] == 'error' and not remote.writes


def test_secret_detection_does_not_echo_values(service):
    api, remote, replica = service
    result = api.execute('update_document', {'document': '2', 'version': 1, 'append': '<p>fixture-private-credential</p>'})
    assert result['code'] == 'secret_detected' and 'fixture-private-credential' not in str(result)
    assert not remote.writes


@pytest.mark.parametrize('changed', ['scope', 'actor'])
def test_last_native_audit_cannot_acknowledge_changed_scope_or_account(service, changed):
    api, remote, replica = service
    original = remote.version
    def audit(id, number, scope=None):
        result = original(id, number, scope)
        if changed == 'scope':
            remote.nodes[id] = replace(remote.nodes[id], space_id='20')
        else:
            remote.actor = {'accountId': 'new-actor'}
        return result
    remote.version = audit
    result = api.execute('update_document', dict(document='2', version=1, append='<p>Mine</p>', metadata=True))
    assert result['status'] == 'unknown' and not result.get('verified')
    assert len(remote.writes) == 1


@pytest.mark.parametrize('suffix', ['?', '#', '\t'])
def test_comment_reference_rejects_raw_uri_normalization(service, suffix):
    from wiki_atlassian.service import comment_ref
    api, remote, replica = service
    value = api.settings.site + '/wiki/api/v2/footer-comments/60' + suffix
    with pytest.raises(RemoteError):
        comment_ref(value, api.settings.site)


def test_comment_markdown_keeps_native_page_anchor_context(service):
    api, remote, replica = service
    result = api._comment_row(dict(id='60', kind='footer', version=1,
        body='<ac:link ac:anchor="part"><ac:plain-text-link-body><![CDATA[Jump]]></ac:plain-text-link-body></ac:link>',
        format='storage'), dict(format='markdown'), '2')
    assert api._url('2') + '#part' in result['body']


@pytest.mark.parametrize('wake_fails', [False, True])
def test_pending_revocation_preserves_unknown_write_and_primary_native_error(service, wake_fails):
    api, remote, replica = service
    calls = []
    def unavailable(*args, **kwargs):
        raise RemoteError('http_403')
    def pending(error):
        raise RemoteError('replica_denial_pending')
    def wake():
        calls.append('wake')
        if wake_fails:
            raise RuntimeError('callback failed')
    remote.version, replica.handle_denial, replica.on_repair = unavailable, pending, wake
    result = api.execute('update_document', dict(document='2', version=1, append='<p>Mine</p>'))
    assert result['status'] == 'unknown' and result['code'] == 'http_403'
    assert result['details']['scopeFence'] == 'pending' and len(remote.writes) == 1
    assert 'never automatically repeat' in result['next'] and calls == ['wake']


def test_structured_relation_denial_wakes_without_replacing_primary_error(service):
    api, remote, replica = service
    result = dict(status='error', code='http_403', details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending'))
    calls = []
    replica.relations = lambda data: result
    replica.on_repair = lambda: calls.append('wake')
    assert api.execute('document_relations', dict(document='2')) == result
    assert calls == ['wake']


@pytest.mark.parametrize('code', ['http_401', 'http_403'])
def test_wrapped_postcommit_root_denial_still_fences_without_replay(service, code):
    from wiki_atlassian.remote import RootScopeDenial
    api, remote, replica = service
    def version(*args, **kwargs):
        raise RemoteError('unknown_update', outcome_unknown=True) from RootScopeDenial(api.settings, RemoteError(code))
    remote.version = version
    result = api.execute('update_document', dict(document='2', version=1, append='<p>Mine</p>'))
    assert result['status'] == 'unknown' and result['code'] == 'unknown_update'
    assert replica.failures == [code] and len(remote.writes) == 1
    assert result.get('authorizationDenied', False) is (code == 'http_401')


def test_root_recheck_unauthorized_preserves_original_child_denial(service):
    api, remote, replica = service
    def version(*args, **kwargs):
        raise RemoteError('http_403')
    def confirmed(error):
        error.details['authorizationDenied'] = True
        raise RemoteError('replica_denial_pending')
    remote.version, replica.handle_denial = version, confirmed
    result = api.execute('update_document', dict(document='2', version=1, append='<p>Mine</p>'))
    assert result['status'] == 'unknown' and result['code'] == 'http_403'
    assert result['authorizationDenied'] and result['details']['scopeFence'] == 'pending'
    assert len(remote.writes) == 1
