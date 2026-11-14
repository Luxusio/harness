"""The private inbox uses common wiki operations with an exact-page boundary."""
import json

import pytest

from test_remote import native
from wiki_atlassian.config import SpaceSettings, corpus_path, personal_settings
from wiki_atlassian.models import Credentials, RemoteError
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
