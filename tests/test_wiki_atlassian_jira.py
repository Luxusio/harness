"""Project Jira tools use the existing MCP envelope and verify native writes."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required to collect this module') from error

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(autouse=True)
def jira_import_paths(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'wiki-atlassian'))
    monkeypatch.syspath_prepend(str(ROOT / 'plugin/scripts'))


def call(server, tool, **args):
    response = server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                              'params': {'name': tool, 'arguments': args}})
    if 'error' in response:
        return response['error']
    return json.loads(response['result']['content'][0]['text'])


class JiraTransport:
    def __init__(self):
        self.calls = []
        self.key = 'CAT-209'
        self.project = 'CAT'
        self.comment = None
        self.post_status = 201
        self.readback_status = 200
        self.issue_status = {'id': '10001', 'name': 'In Progress'}
        self.done_status = {'id': '10002', 'name': 'Done'}
        self.transition_status = 204
        self.apply_transition = True

    def __call__(self, method, target, headers, payload, timeout):
        assert headers['Authorization'].startswith('Basic ')
        uri = urlsplit(target)
        assert uri.hostname == 'test.example.invalid'
        args = parse_qs(uri.query)
        assert 0 < timeout <= 25
        self.calls.append((method, uri.path))
        if uri.path == '/rest/api/3/issue/CAT-209':
            assert args['updateHistory'] == ['false']
            return 200, {}, json.dumps({'id': '10305', 'key': self.key, 'fields': {
                'project': {'key': self.project}, 'summary': 'Review migration', 'status': self.issue_status,
            }}).encode()
        if uri.path == '/rest/api/3/issue/10305/transitions':
            if method == 'GET':
                return 200, {}, json.dumps({'transitions': [
                    {'id': '31', 'name': 'Complete review', 'to': self.done_status},
                    {'id': '51', 'name': 'Archive', 'to': self.done_status, 'isAvailable': False},
                ]}).encode()
            assert method == 'POST' and json.loads(payload) == {'transition': {'id': '31'}}
            if self.transition_status == 204 and self.apply_transition:
                self.issue_status = self.done_status
            return self.transition_status, {}, b''
        if method == 'POST':
            assert uri.path == '/rest/api/3/issue/10305/comment'
            self.comment = {'id': '17', 'body': json.loads(payload)['body']}
            return self.post_status, {}, json.dumps(self.comment).encode()
        if uri.path.endswith('/comment/17'):
            return self.readback_status, {}, json.dumps(self.comment).encode()
        assert uri.path == '/rest/api/3/issue/10305/comment'
        assert args == {'maxResults': ['10'], 'orderBy': ['-created']}
        return 200, {}, json.dumps({'comments': [self.comment], 'total': 1}).encode()


def fixture():
    from wiki_atlassian.jira import Jira
    from wiki_atlassian.models import Credentials
    from wiki_atlassian.protocol import Server

    native = JiraTransport()
    jira = Jira(Credentials('https://test.example.invalid', 'owner@example.invalid', 'test-project-token'),
                ['CAT'], native)
    return Server(jira=jira, personal=lambda *_: pytest.fail('Personal credentials used for Jira')), native


def test_jira_tools_require_project_scope_and_never_appear_for_reviewer():
    from wiki_atlassian.protocol import Server

    server, native = fixture()
    assert call(Server(), 'get_jira_issue', issue='CAT-209')['code'] == -32602
    restricted = Server(jira=server.jira, reviewer=True)
    assert call(restricted, 'get_jira_issue', issue='CAT-209')['code'] == -32602
    assert call(server, 'get_jira_issue', issue='CAT-209', connection='personal')['code'] == -32602
    assert call(server, 'get_jira_issue', issue='OPS-209')['code'] == 'jira_project_not_configured'
    assert call(server, 'get_jira_issue', issue='CAT-209/../comment')['code'] == 'invalid_issue_key'
    assert native.calls == []


def test_jira_reads_issue_and_verifies_post_readback():
    server, native = fixture()
    result = call(server, 'get_jira_issue', issue='cat-209')
    assert result['fields']['summary'] == 'Review migration'
    assert result['url'] == 'https://test.example.invalid/browse/CAT-209'
    result = call(server, 'add_jira_comment', issue='CAT-209', text='검수 완료\r\n\r\n<plain text>')
    assert result['status'] == 'created'
    assert result['commentId'] == '17'
    result = call(server, 'get_jira_issue', issue='CAT-209', includeComments=True)
    assert result['comments'] == [native.comment]
    assert result['commentTotal'] == 1
    paragraphs = native.comment['body']['content']
    assert paragraphs[0]['content'][0]['text'] == '검수 완료'
    assert paragraphs[1] == {'type': 'paragraph'}
    assert paragraphs[2]['content'][0]['text'] == '<plain text>'
    assert [method for method, _ in native.calls].count('POST') == 1


def test_moved_issue_and_secret_are_rejected_before_post():
    server, native = fixture()
    native.key, native.project = 'OTHER-209', 'OTHER'
    assert call(server, 'get_jira_issue', issue='CAT-209')['code'] == 'jira_issue_scope_changed'
    assert call(server, 'add_jira_comment', issue='CAT-209', text='Readback')['code'] == 'jira_issue_scope_changed'
    assert call(server, 'add_jira_comment', issue='CAT-209', text='test-project-token')['code'] == 'secret_detected'
    assert all(method == 'GET' for method, _ in native.calls)


@pytest.mark.parametrize(('post_status', 'readback_status', 'status', 'comment_id'), [
    (503, 200, 'unknown', None),
    (201, 404, 'unknown', '17'),
    (403, 200, 'error', None),
])
def test_failed_comment_is_never_retried(post_status, readback_status, status, comment_id):
    server, native = fixture()
    native.post_status, native.readback_status = post_status, readback_status
    result = call(server, 'add_jira_comment', issue='CAT-209', text='검수 완료')
    assert result['status'] == status
    assert result.get('commentId') == comment_id
    assert [method for method, _ in native.calls].count('POST') == 1


@pytest.mark.parametrize('selection', ['CAT', ['CAT', 'CAT'], ['cat'], ['../CAT'], ['CAT', 12]])
def test_jira_project_config_is_explicit_and_validated(selection):
    from document_destination import _validate_config

    base = {'enabled': True, 'site': 'https://test.example.invalid', 'spaces': [{'key': 'SD'}]}
    assert 'jiraProjects' not in _validate_config(base)
    assert _validate_config({**base, 'jiraProjects': ['CAT']})['jiraProjects'] == ['CAT']
    with pytest.raises(ValueError, match='Jira project key'):
        _validate_config({**base, 'jiraProjects': selection})


def test_jira_discovers_transition_and_verifies_native_204():
    server, native = fixture()
    options = call(server, 'list_jira_transitions', issue='CAT-209')
    assert options['currentStatus'] == {'id': '10001', 'name': 'In Progress'}
    assert options['transitions'] == [{'id': '31', 'name': 'Complete review', 'to': native.done_status}]
    result = call(server, 'transition_jira_issue', issue='CAT-209',
                  transitionId='31', fromStatusId='10001', targetStatusId='10002')
    assert result['status'] == 'transitioned'
    assert result['currentStatus'] == native.done_status
    assert call(server, 'get_jira_issue', issue='CAT-209')['fields']['status'] == native.done_status
    again = call(server, 'transition_jira_issue', issue='CAT-209',
                 transitionId='31', fromStatusId='10001', targetStatusId='10002')
    assert again['code'] == 'jira_status_changed'
    assert [method for method, _ in native.calls].count('POST') == 1


@pytest.mark.parametrize(('issue', 'connection', 'transition', 'target', 'code'), [
    ('OPS-209', 'project', '31', '10002', 'jira_project_not_configured'),
    ('CAT-209', 'personal', '31', '10002', -32602),
    ('CAT-209', 'project', '51', '10002', 'jira_transition_unavailable'),
    ('CAT-209', 'project', '31', '10003', 'jira_transition_destination_changed'),
])
def test_jira_transition_rejects_wrong_scope_or_destination(issue, connection, transition, target, code):
    from wiki_atlassian.protocol import Server

    server, native = fixture()
    args = dict(issue=issue, connection=connection, transitionId=transition, fromStatusId='10001', targetStatusId=target)
    assert call(server, 'transition_jira_issue', **args)['code'] == code
    assert call(Server(jira=server.jira, reviewer=True), 'transition_jira_issue', **args)['code'] == -32602
    assert all(method == 'GET' for method, _ in native.calls)


@pytest.mark.parametrize(('native_status', 'status', 'code'), [
    (204, 'unknown', 'jira_transition_unverified'),
    (503, 'unknown', 'jira_transition_unverified'),
    (403, 'error', 'http_403'),
])
def test_jira_transition_reports_unverified_or_rejected_without_retry(native_status, status, code):
    server, native = fixture()
    native.transition_status, native.apply_transition = native_status, False
    result = call(server, 'transition_jira_issue', issue='CAT-209',
                  transitionId='31', fromStatusId='10001', targetStatusId='10002')
    assert result['status'] == status
    assert result['code'] == code
    assert [method for method, _ in native.calls].count('POST') == 1
