"""The private inbox uses common wiki operations with an exact-page boundary."""
import base64
import json

import pytest

from test_remote import comment_raw, native
from wiki_atlassian.config import SpaceSettings, corpus_path, personal_settings
from wiki_atlassian.models import Credentials, RemoteError
from wiki_atlassian.remote import Remote
from wiki_atlassian.protocol import Server
from wiki_atlassian.replica import Replica
from wiki_atlassian.service import WikiService


def test_common_operations_select_private_connection_without_project_fallback():
    calls = []

    class Project:
        def execute(self, operation, arguments):
            calls.append(('project', operation, arguments))
            return {'status': 'read'}

    def personal(operation, arguments):
        calls.append(('personal', operation, arguments))
        return {'status': 'read'}

    server = Server(Project(), personal)
    tools = server.handle(dict(id=1, method='tools/list'))['result']['tools']
    assert 'read_personal_feedback' not in {tool['name'] for tool in tools}
    for connection in ('personal', 'project'):
        response = server.handle(dict(id=2, method='tools/call', params=dict(
            name='get_document', arguments=dict(document='10', connection=connection))))
        assert json.loads(response['result']['content'][0]['text'])['connection'] == connection
    assert [call[0] for call in calls] == ['personal', 'project']
    assert all('connection' not in call[2] for call in calls)

    private_only = Server(personal=personal)
    before = len(calls)
    response = private_only.handle(dict(id=3, method='tools/call', params=dict(
        name='get_document', arguments=dict(document='10'))))
    assert json.loads(response['result']['content'][0]['text'])['code'] == 'connection_unavailable'
    assert len(calls) == before
    response = server.handle(dict(id=4, method='tools/call', params=dict(
        name='get_document', arguments=dict(document='10', connection='other'))))
    assert response['error']['code'] == -32602


def test_status_does_not_contact_private_account_until_selected():
    def personal(operation, arguments):
        raise AssertionError('Private credentials must not be loaded by project status')

    response = Server(personal=personal).handle(dict(id=1, method='tools/call',
        params=dict(name='connection_status', arguments={})))
    value = json.loads(response['result']['content'][0]['text'])
    assert value['connections']['personal']['configured']
    assert value['connections']['personal']['authentication'] == 'not_checked'


@pytest.mark.parametrize('key', ['DOC', '~personal'])
def test_page_only_settings_and_cache_identity_do_not_admit_subtrees(native, tmp_path, key):
    state, remote, original = native
    state.hook = lambda method, path, query, payload: (
        (200, {}, dict(id='9', key=key, status='current'))
        if method == 'GET' and path == '/wiki/api/v2/spaces/9' else None)
    credentials = remote.credentials
    selected = dict(site=credentials.base_url, page_id='10')
    settings = personal_settings(selected, credentials, remote, tmp_path)
    assert settings.page_only and settings.root_page_id == '10'
    assert settings.corpus_root != corpus_path(tmp_path, credentials, key, '10')
    rotated = Credentials(credentials.base_url, credentials.email, credentials.api_token + '-rotated')
    again = personal_settings(selected, rotated, remote, tmp_path)
    assert again.corpus_root == settings.corpus_root
    assert again.identity() != settings.identity()
    state.calls.clear()
    assert [node.id for node in remote.inventory(settings)] == ['10']
    assert not any('/direct-children' in call[1] for call in state.calls)
    state.calls.clear()
    with pytest.raises(RemoteError, match='configured page'):
        remote.require_scope(settings, '11')
    assert not state.calls


def test_page_only_native_service_reads_comments_and_refuses_child_creation(native, tmp_path):
    state, remote, original = native
    config = {**original.config, 'pageOnly': True, 'rootPageId': '10'}
    settings = SpaceSettings(config, original.credentials, tmp_path)
    replica = Replica(settings, remote)
    replica.synchronize()
    service = WikiService(settings, remote, replica)
    read = service.execute('get_document', dict(document='10', format='storage'))
    assert 'Original' in read['content']
    state.calls.clear()
    child = service.execute('create_document', dict(parent='10', title='Child', content='<p>Unapproved</p>'))
    assert child['code'] == 'scope_outside_connection'
    assert all(call[0] == 'GET' and call[1] == '/wiki/api/v2/pages/10' for call in state.calls)
    state.calls.clear()
    denied = service.execute('get_document', dict(document='11', format='outline'))
    assert denied['code'] == 'scope_outside_connection'
    assert all(call[0] == 'GET' and call[1] == '/wiki/api/v2/pages/10' for call in state.calls)
    created = service.execute('create_comment', dict(target='10', text='<p>Private common comment</p>', includeBody=True))
    assert created['status'] == 'created' and created['reference'] == 'footer:60'
    state.hook = lambda method, path, query, payload: (
        (200, {}, dict(number=1, authorId='account'))
        if method == 'GET' and path == '/wiki/api/v2/footer-comments/60/versions/1' else None)
    updated = service.execute('update_comment', dict(comment='footer:60', version=1, text='<p>Revised once</p>', includeBody=True))
    assert updated['status'] == 'updated' and updated['version'] == 2
    stale = service.execute('update_comment', dict(comment='footer:60', version=1, text='<p>Stale</p>'))
    assert stale['code'] == 'version_conflict'
    assert len([call for call in state.calls if call[0] == 'POST']) == 1
    assert len([call for call in state.calls if call[0] == 'PUT']) == 1


def test_page_only_replica_refuses_a_descendant_manifest(native, tmp_path):
    state, remote, original = native
    settings = SpaceSettings({**original.config, 'pageOnly': True, 'rootPageId': '10'}, original.credentials, tmp_path)
    replica = Replica(settings, remote)
    replica.synchronize()
    manifest = replica._manifest()
    manifest['pages']['11'] = {**manifest['pages']['10'], 'id': '11', 'parentId': '10'}
    with pytest.raises(RemoteError, match='malformed'):
        replica._validate(manifest)


def test_native_history_continuation_binds_connection_page_and_limit(native, tmp_path):
    state, remote, original = native
    state.pages['10']['version']['number'] = 3
    def history(method, path, query, payload):
        if method != 'GET' or path != '/wiki/api/v2/pages/10/versions':
            return None
        row = dict(number=2 if 'cursor' in query else 3, authorId='account', createdAt=state.version_time)
        result = dict(results=[row])
        if 'cursor' not in query:
            result['_links'] = dict(next='?limit=1&cursor=second')
        return 200, {}, result
    state.hook = history
    personal_settings = SpaceSettings({**original.config, 'pageOnly': True, 'rootPageId': '10'},
                                      original.credentials, tmp_path / 'personal')
    personal = WikiService(personal_settings, remote, Replica(personal_settings, remote))
    other_credentials = Credentials(original.credentials.base_url, 'another@example.invalid', 'another-token')
    other_remote = Remote(other_credentials, ssl_context=remote._ssl_context)
    project_settings = SpaceSettings(original.config, other_credentials, tmp_path / 'project')
    project = WikiService(project_settings, other_remote, Replica(project_settings, other_remote))
    server = Server(documents=project, personal=personal.execute)

    def call(connection, **args):
        response = server.handle(dict(id=1, method='tools/call', params=dict(
            name='list_document_versions', arguments=dict(connection=connection, document='10', limit=1, **args))))
        return json.loads(response['result']['content'][0]['text'])

    first = call('personal')
    assert first['versions'][0]['number'] == 3 and first['hasMore']
    second = call('personal', cursor=first['cursor'])
    assert second['versions'][0]['number'] == 2 and second['complete']
    assert 'cursor' not in second
    assert any(path == '/wiki/api/v2/pages/10/versions' and query.get('cursor') == ['second']
               for _, path, query, _ in state.calls)
    state.calls.clear()
    assert call('project', cursor=first['cursor'])['code'] == 'invalid_cursor'
    for changes in (dict(document='11'), dict(limit=2)):
        result = personal.execute('list_document_versions', {'document': '10', 'limit': 1, 'cursor': first['cursor'], **changes})
        assert result['code'] in ('invalid_cursor', 'scope_outside_connection')
    assert not any(path.endswith('/versions') for _, path, _, _ in state.calls)

    # Native locators are internal transport data, not a public compatibility API.
    from wiki_atlassian.cursors import decode_cursor
    binding = ['versions', personal.identity, personal.settings.credential_fingerprint, '10', 1]
    raw = decode_cursor(first['cursor'], binding, key=personal.settings.cursor_key)['native']
    assert call('personal', cursor=raw)['code'] == 'invalid_cursor'
    other_remote.close()


def test_history_cursor_rejects_changed_project_credentials_with_same_configuration(native, tmp_path):
    state, remote, original = native
    state.pages['10']['version']['number'] = 3
    state.hook = lambda method, path, query, payload: (
        (200, {}, dict(results=[dict(number=3, authorId='account', createdAt=state.version_time)],
                       _links=dict(next='?limit=1&cursor=second')))
        if method == 'GET' and path == '/wiki/api/v2/pages/10/versions' else None)
    first_service = WikiService(original, remote, Replica(original, remote))
    first = first_service.execute('list_document_versions', dict(document='10', limit=1))
    state.calls.clear()
    cross_page = first_service.execute('list_document_versions', dict(document='11', limit=1, cursor=first['cursor']))
    assert cross_page['code'] == 'invalid_cursor'
    assert not any(path.endswith('/versions') for _, path, _, _ in state.calls)
    alternate = Credentials(original.credentials.base_url, original.credentials.email, 'rotated-token')
    settings = SpaceSettings(original.config, alternate, tmp_path / 'rotated')
    assert settings.identity() == original.identity()
    other = WikiService(settings, remote, Replica(settings, remote))
    state.calls.clear()
    result = other.execute('list_document_versions', dict(document='10', limit=1, cursor=first['cursor']))
    assert result['code'] == 'invalid_cursor'
    assert not any(path.endswith('/versions') for _, path, _, _ in state.calls)


def test_personal_comment_cursor_rejects_state_forgery_without_hiding_unread_comments(native, tmp_path):
    state, remote, original = native
    settings = SpaceSettings({**original.config, 'pageOnly': True}, original.credentials, tmp_path)
    service = WikiService(settings, remote, Replica(settings, remote))
    state.comments = {('footer', id): comment_raw(id, page='10') for id in ('50', '51')}
    arguments = dict(target='10', limit=1, format='storage')
    first = service.execute('list_comments', arguments)
    assert [row['id'] for row in first['comments']] == ['50'] and not first['complete']
    encoded, signature = first['cursor'].split('.')
    data = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    data['state'] = dict(kind='inline', native=None)
    forged = base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')
    for cursor in (forged + '.' + signature, encoded, encoded + '.' + '0' * 64):
        state.calls.clear()
        rejected = service.execute('list_comments', {**arguments, 'cursor': cursor})
        assert rejected['code'] == 'invalid_cursor' and not rejected.get('complete')
        assert not any(path.endswith(('/footer-comments', '/inline-comments')) for _, path, _, _ in state.calls)
    continued = service.execute('list_comments', {**arguments, 'cursor': first['cursor']})
    assert [row['id'] for row in continued['comments']] == ['51']
    assert not continued['complete']
    final = service.execute('list_comments', {**arguments, 'cursor': continued['cursor']})
    assert final['complete'] and not final['comments']
    changed_actor = SpaceSettings({**settings.config, 'privateAccountId': 'another-account'}, original.credentials, tmp_path)
    other = WikiService(changed_actor, remote, Replica(changed_actor, remote))
    assert other.execute('list_comments', {**arguments, 'cursor': first['cursor']})['code'] == 'invalid_cursor'


def test_personal_reply_cursor_is_signed_and_continues_native_thread(native, tmp_path):
    state, remote, original = native
    settings = SpaceSettings({**original.config, 'pageOnly': True}, original.credentials, tmp_path)
    service = WikiService(settings, remote, Replica(settings, remote))
    state.comments = {('footer', '50'): comment_raw('50', page='10')}
    def replies(method, path, query, payload):
        if method == 'GET' and path == '/wiki/api/v2/footer-comments/50/children':
            result = dict(results=[comment_raw('52' if 'cursor' in query else '51', parent='50', page='10')])
            if 'cursor' not in query:
                result['_links'] = dict(next='?limit=1&cursor=second')
            return 200, {}, result
    state.hook = replies
    roots = service.execute('list_comments', dict(target='10', limit=1, format='storage', includeReplies=True, repliesPerComment=1))
    nested = roots['comments'][0]['replies']
    assert nested['comments'][0]['id'] == '51' and '.' in nested['cursor']
    continued = service.execute('list_comments', dict(target=nested['target'], limit=1, format='storage', cursor=nested['cursor']))
    assert continued['comments'][0]['id'] == '52' and continued['complete']


def test_exact_page_search_rejects_forged_continuation_instead_of_skipping_only_hit(native, tmp_path):
    state, remote, original = native
    settings = SpaceSettings({**original.config, 'pageOnly': True}, original.credentials, tmp_path)
    replica = Replica(settings, remote)
    replica.synchronize()
    current = replica.search(dict(query='Original', limit=1))
    assert len(current['results']) == 1 and not current['truncated'] and 'nextCursor' not in current
    forged = replica.search(dict(query='Original', limit=1, cursor='invented-continuation'))
    assert forged['code'] == 'invalid_cursor'
