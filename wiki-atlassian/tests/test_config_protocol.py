"""Native Python connection identities and actual bounded MCP protocol."""
import io
import json
from pathlib import Path
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required') from error

from wiki_atlassian.config import Credentials, Project, SpaceSettings, corpus_path
from wiki_atlassian.models import digest
from wiki_atlassian.protocol import Server
from wiki_atlassian.tools import PERSONAL_TOOL, SCHEMAS, WIKI_TOOLS, validate_arguments


def project_value():
    return {'enabled': True, 'site': 'https://wiki.example.invalid', 'spaces': [{'key': 'TEAM'}, {'key': 'DEV', 'rootPageId': '42'}]}


def test_partition_identity_matches_existing_compact_unicode_json_and_excludes_token(tmp_path):
    a = Credentials('https://wiki.example.invalid', '이름@example.invalid', 'first-token')
    b = Credentials(a.base_url, a.email, 'second-token')
    assert corpus_path(tmp_path, a, 'TEAM') == corpus_path(tmp_path, b, 'TEAM')
    assert corpus_path(tmp_path, a, 'TEAM').name == digest([a.base_url, a.email, '*', 'TEAM'])
    assert corpus_path(tmp_path, a, 'TEAM') != corpus_path(tmp_path, a, 'TEAM', '42')
    assert corpus_path(tmp_path, a, 'TEAM') != corpus_path(tmp_path, a, 'DEV')
    value = {'space': 'TEAM', 'spaceId': '1'}
    sa, sb = SpaceSettings(value, a, tmp_path), SpaceSettings(value, b, tmp_path)
    assert sa.credential_fingerprint != sb.credential_fingerprint


def test_project_and_selection_are_immutable_and_resolve_exactly_one_space(tmp_path):
    value = project_value()
    credentials = Credentials(value['site'], 'fixture@example.invalid', 'fixture-token')
    project = Project(value, credentials, tmp_path)
    value['spaces'][0]['key'] = 'POISON'
    class Remote:
        def spaces(self, key):
            return [{'key': key, 'id': '1'}]
    settings = project.resolve(Remote(), 'TEAM')
    assert settings.space == 'TEAM' and settings.whole_space and settings.root_page_id is None
    selected = project.resolve(Remote(), 'DEV')
    assert selected.root_page_id == '42' and not selected.whole_space
    returned = project.selections
    returned[0]['key'] = 'CHANGED'
    assert project.selections[0]['key'] == 'TEAM'
    settings.config['space'] = 'CHANGED'
    assert settings.space == 'TEAM' and settings.config['space'] == 'TEAM'


@pytest.mark.parametrize('key', ['ATLASSIAN_BASE_URL', 'ATLASSIAN_EMAIL', 'ATLASSIAN_API_TOKEN'])
def test_absent_and_disabled_project_are_inert_before_private_auth(tmp_path, key):
    assert Project.from_project(tmp_path, {}) is None
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('wiki:\n  enabled: false\n', encoding='utf-8')
    assert Project.from_project(tmp_path, {key: 'unrelated'}) is None


def test_shared_settings_keep_private_origin_pin_and_nested_repository_boundary(tmp_path):
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'wiki': {'backend': 'atlassian', **project_value()}}), encoding='utf-8')
    (tmp_path / '.git').mkdir()
    env = {'HOME': str(tmp_path), 'USERPROFILE': str(tmp_path), 'ATLASSIAN_BASE_URL': 'https://another.invalid',
           'ATLASSIAN_EMAIL': 'fixture@example.invalid', 'ATLASSIAN_API_TOKEN': 'do-not-print'}
    with pytest.raises(ValueError) as caught:
        Project.from_project(tmp_path, env)
    assert 'do-not-print' not in str(caught.value)
    nested = tmp_path / 'nested'
    nested.mkdir()
    (nested / '.git').write_text('gitdir: boundary', encoding='utf-8')
    assert Project.from_project(nested, env) is None


def test_public_tool_names_and_strict_types_are_preserved():
    assert set(SCHEMAS) == {'connection_status', 'read_personal_feedback', 'search_documents', 'document_relations',
        'get_document', 'create_document', 'update_document', 'list_comments', 'create_comment', 'update_comment', 'list_document_versions'}
    for tool in SCHEMAS.values():
        assert tool['inputSchema']['additionalProperties'] is False
    assert validate_arguments('get_document', {'document': '42'})['length'] == 4000
    assert validate_arguments('list_comments', {'target': '42'})['repliesPerComment'] == 3
    for args in ({'document': '42', 'version': True}, {'document': '42', 'version': None}, {'document': '42', 'ignore': True}, {}):
        with pytest.raises(Exception):
            validate_arguments('get_document', args)


def test_inert_private_and_invalid_project_expose_truthful_surfaces():
    def tools(server):
        return server.handle({'id': 1, 'method': 'tools/list'})['result']['tools']
    assert tools(Server()) == []
    private = Server(personal=lambda args: {'status': 'unconfigured'})
    assert tools(private) == [PERSONAL_TOOL]
    combined = Server(personal=lambda args: {'status': 'read'}, project_unavailable=True)
    assert [tool['name'] for tool in tools(combined)] == ['connection_status', 'read_personal_feedback']
    unavailable = combined.handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'connection_status'}})
    assert json.loads(unavailable['result']['content'][0]['text'])['code'] == 'connection_unavailable'
    assert combined.handle({'id': 3, 'method': 'tools/call', 'params': {'name': 'read_personal_feedback', 'arguments': {'limit': True}}})['error']['code'] == -32602


def test_actual_stdio_keeps_unicode_and_rejects_oversize_without_echoing_body(monkeypatch):
    import wiki_atlassian.protocol as protocol
    monkeypatch.setattr(protocol, 'MAX_LINE', 256)
    requests = [b'{invalid}\n', b'X' * 300 + b'\n', json.dumps({'id': 1, 'method': 'initialize'}).encode() + b'\n',
                json.dumps({'id': 2, 'method': 'tools/call', 'params': {'name': 'read_personal_feedback', 'arguments': {}}}).encode() + b'\n']
    reader, writer = io.BytesIO(b''.join(requests)), io.BytesIO()
    Server(personal=lambda args: {'status': 'read', 'document': '설정😀'}).run(reader, writer)
    rows = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert [row.get('error', {}).get('code') for row in rows[:2]] == [-32700, -32700]
    assert rows[2]['result']['serverInfo']['name'] == 'atlassian'
    assert json.loads(rows[3]['result']['content'][0]['text'])['document'] == '설정😀'
    assert b'X' * 30 not in writer.getvalue()


def test_private_tool_exceptions_do_not_echo_credentials():
    def unsafe(args):
        raise OSError('secret-do-not-print')
    value = Server(personal=unsafe).handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'read_personal_feedback'}})
    assert value['result']['isError']
    assert 'secret-do-not-print' not in json.dumps(value)


def test_overflowing_numeric_rpc_id_is_refused_before_dispatch_and_stream_survives():
    class Documents:
        calls = 0
        def execute(self, *args):
            self.calls += 1
            return {'status': 'created'}
    documents = Documents()
    requests = (b'{"jsonrpc":"2.0","id":1e999,"method":"tools/call","params":'
                b'{"name":"create_document","arguments":{"title":"Synthetic","content":"<p>Test</p>"}}}\n'
                b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    writer = io.BytesIO()
    Server(documents=documents).run(io.BytesIO(requests), writer)
    rows = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert documents.calls == 0 and rows[0]['error']['code'] == -32600 and rows[0]['id'] is None
    assert rows[1]['id'] == 2 and rows[1]['result'] == {}


@pytest.mark.parametrize('slow', [False, True])
def test_private_read_has_an_owned_process_deadline_and_keeps_stdio_usable(tmp_path, monkeypatch, slow):
    import personal_feedback
    import time
    from wiki_atlassian.main import read_personal
    helper = tmp_path / 'personal_feedback.py'
    helper.write_text("import os,sys,time\ndef perform(action,request,deadline=None):\n"
                      " if request['limit']==2: time.sleep(10)\n"
                      " assert '_HARNESS_PRIVATE_READ_REQUEST' not in os.environ\n"
                      " assert request['cursor'] not in repr(sys.argv)\n"
                      " return dict(status='read',request=request)\n")
    monkeypatch.setattr(personal_feedback, '__file__', str(helper))
    values = dict(limit=2 if slow else 1, cursor='private-continuation-😀')
    started = time.monotonic()
    server = Server(personal=lambda data: read_personal(data, timeout=.5 if slow else 5))
    response = server.handle(dict(id=1, method='tools/call',
        params=dict(name='read_personal_feedback', arguments=values)))
    result = json.loads(response['result']['content'][0]['text'])
    assert result['status'] == ('unavailable' if slow else 'read')
    if slow:
        assert time.monotonic() - started < 2
    else:
        assert result['request'] == values
    assert server.handle(dict(id=2, method='ping'))['result'] == {}


def test_private_process_transports_large_unicode_as_utf8(tmp_path, monkeypatch):
    import personal_feedback
    from wiki_atlassian.main import read_personal
    helper = tmp_path / 'personal_feedback.py'
    helper.write_text("def perform(action,request,deadline=None):\n"
                      " return dict(status='read',comments=[dict(body='가'*11900) for _ in range(25)])\n")
    monkeypatch.setattr(personal_feedback, '__file__', str(helper))
    result = read_personal(dict(limit=25), timeout=5)
    assert result['status'] == 'read' and len(result['comments']) == 25
    assert result['comments'][-1]['body'] == '가' * 11900
