"""Native Python connection identities and actual bounded MCP protocol."""
import io
import json
from pathlib import Path
from queue import Queue
import sys
import threading

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
from wiki_atlassian.tools import SCHEMAS, WIKI_TOOLS, validate_arguments


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


def test_bound_reviewer_requires_exact_project_file_credentials(tmp_path):
    manifest = tmp_path / 'doc/harness/manifest.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'wiki': {'backend': 'atlassian', **project_value()}}), encoding='utf-8')
    environment = {'ATLASSIAN_BASE_URL': 'https://wiki.example.invalid', 'ATLASSIAN_EMAIL': 'host@example.invalid',
                   'ATLASSIAN_API_TOKEN': 'fixture', 'HOME': str(tmp_path), 'USERPROFILE': str(tmp_path)}
    assert Project.from_project(tmp_path, environment) is not None
    with pytest.raises(ValueError, match='connection or authentication unavailable'):
        Project.from_project(tmp_path, environment, bound_reviewer=True)


def test_public_tool_names_and_strict_types_are_preserved():
    assert set(SCHEMAS) == {'connection_status', 'search_documents', 'document_relations',
        'get_document', 'create_document', 'update_document', 'operation_status',
        'list_comments', 'create_comment', 'update_comment', 'list_document_versions'}
    for tool in SCHEMAS.values():
        assert tool['inputSchema']['additionalProperties'] is False
    assert validate_arguments('get_document', {'document': '42'})['length'] == 4000
    assert validate_arguments('search_documents', {'query': 'guide'})['limit'] == 10
    assert validate_arguments('search_documents', {'queries': ['guide', 'review']})['limit'] == 3
    assert validate_arguments('list_comments', {'target': '42'})['repliesPerComment'] == 3
    for args in ({'document': '42', 'version': True}, {'document': '42', 'version': None}, {'document': '42', 'ignore': True}, {}):
        with pytest.raises(Exception):
            validate_arguments('get_document', args)


@pytest.mark.parametrize('tool_name,bad', [
    ('search_documents', {'query': 'a', 'queries': ['a', 'b']}),
    ('search_documents', {'queries': ['a', 2]}),
    ('search_documents', {'queries': []}),
    ('search_documents', {'queries': ['a'], 'cursor': 'single-only'}),
    ('get_document', {'reads': [{'document': '42', 'version': True}]}),
    ('get_document', {'reads': [{'document': '42', 'connection': 'personal'}]}),
    ('get_document', {'reads': [{'document': '42', 'metadata': True}]}),
    ('get_document', {'reads': [{'document': '42', 'length': 12000}] * 3}),
    ('get_document', {'reads': [{'document': '42'}], 'length': 4}),
    ('get_document', {'reads': [{'document': '42'}], 'connection': 'personal'}),
])
def test_retrieval_batch_validates_alternatives_and_nested_arguments(tool_name, bad):
    from wiki_atlassian.models import RemoteError
    with pytest.raises(RemoteError):
        validate_arguments(tool_name, bad)


@pytest.mark.parametrize('name,arguments,hint', [
    ('get_document', {'reads': [{'pageId': 'private-value'}]}, 'arguments.reads[0].document'),
    ('get_document', {'private-key': 'private-value'}, 'document or reads'),
    ('get_document', {'document': '42', 'private-key': 'private-value'}, 'Allowed fields at arguments'),
    ('search_documents', {'queries': ['guide', {'query': 'private-value'}]}, 'arguments.queries[1] must have JSON type string'),
])
def test_retrieval_batch_reports_schema_paths_without_echoing_input(name, arguments, hint):
    server = Server(documents=object())
    response = server.handle({
        'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments},
    })
    assert response['error']['code'] == -32602
    assert hint in response['error']['message']
    assert 'private-' not in response['error']['message']


def test_inert_private_and_invalid_project_expose_truthful_surfaces():
    def tools(server):
        return server.handle({'id': 1, 'method': 'tools/list'})['result']['tools']
    assert tools(Server()) == []
    private = Server(personal=lambda operation, args: {'status': 'unconfigured'})
    assert tools(private) == WIKI_TOOLS
    combined = Server(personal=lambda operation, args: {'status': 'read'}, project_unavailable=True)
    assert tools(combined) == WIKI_TOOLS
    unavailable = combined.handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'connection_status', 'arguments': {'connection': 'project'}}})
    assert json.loads(unavailable['result']['content'][0]['text'])['code'] == 'connection_unavailable'
    assert combined.handle({'id': 3, 'method': 'tools/call', 'params': {'name': 'list_comments', 'arguments': {'target': '42', 'connection': 'personal', 'limit': True}}})['error']['code'] == -32602


def test_actual_stdio_keeps_unicode_and_rejects_oversize_without_echoing_body(monkeypatch):
    import wiki_atlassian.protocol as protocol
    monkeypatch.setattr(protocol, 'MAX_LINE', 256)
    requests = [b'{invalid}\n', b'X' * 300 + b'\n', json.dumps({'id': 1, 'method': 'initialize'}).encode() + b'\n',
                json.dumps({'id': 2, 'method': 'tools/call', 'params': {'name': 'get_document', 'arguments': {'document': '42', 'connection': 'personal'}}}).encode() + b'\n']
    reader, writer = io.BytesIO(b''.join(requests)), io.BytesIO()
    Server(personal=lambda operation, args: {'status': 'read', 'document': '설정😀'}).run(reader, writer)
    rows = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert [row.get('error', {}).get('code') for row in rows[:2]] == [-32700, -32700]
    assert rows[2]['result']['serverInfo']['name'] == 'atlassian'
    assert json.loads(rows[3]['result']['content'][0]['text'])['document'] == '설정😀'
    assert b'X' * 30 not in writer.getvalue()


def test_private_tool_exceptions_do_not_echo_credentials():
    def unsafe(operation, args):
        raise OSError('secret-do-not-print')
    value = Server(personal=unsafe).handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'get_document', 'arguments': {'document': '42', 'connection': 'personal'}}})
    assert value['result']['isError']
    assert 'secret-do-not-print' not in json.dumps(value)


@pytest.mark.parametrize('operation,status,connection,expected', [
    ('create_document', 'created', 'project', True),
    ('update_document', 'updated', 'project', True),
    ('update_document', 'unknown', 'project', False),
    ('update_document', 'error', 'project', False),
    ('create_comment', 'created', 'project', False),
    ('update_document', 'updated', 'personal', False),
    ('get_document', 'read', 'project', False),
])
def test_review_handoff_on_verified_project_document_writes(operation, status, connection, expected):
    class Documents:
        def execute(self, name, args):
            return {'status': status, 'id': '42', 'version': 5}

    docs = Documents()
    result = Server(documents=docs, personal=docs.execute)._execute(operation, {}, connection)
    assert ('reviewInstruction' in result) is expected
    assert (result['id'], result['version']) == ('42', 5)


@pytest.mark.parametrize('denied', [False, True])
def test_review_handoff_after_verified_save_preserves_access_fence(denied):
    class Documents:
        def execute(self, name, args):
            return {'status': 'updated', 'id': '42', 'version': 5, 'syncRequired': True,
                    'authorizationDenied': denied, 'next': 'Inspect access and sync state.'}

    result = Server(documents=Documents())._execute('update_document', {}, 'project')
    assert ('reviewInstruction' in result) is (not denied)
    assert result['next'] == 'Inspect access and sync state.'


def test_deferred_update_does_not_block_reads_and_keeps_a_single_bounded_write_lane():
    requests, replies = Queue(), Queue()
    started, release = threading.Event(), threading.Event()
    applied = []

    class Reader:
        def readline(self, size):
            return requests.get(timeout=8)

    class Writer:
        def write(self, raw):
            replies.put(json.loads(raw))

        def flush(self):
            pass

    class Documents:
        def execute(self, operation, args):
            if operation == 'update_document':
                started.set()
                assert release.wait(5)
                applied.append(args['version'])
                if args['version'] == 5:
                    return {'status': 'error', 'code': 'version_conflict'}
                if args['version'] == 6:
                    raise OSError('private provider details')
                if args['version'] == 7:
                    return {'status': 'updated', 'authorizationDenied': True, 'next': 'Restore access before reading.'}
                return {'status': 'updated', 'id': args['document'], 'version': args['version'] + 1}
            assert operation == 'get_document'
            return {'status': 'read', 'version': 4}

    server = Server(documents=Documents())
    thread = threading.Thread(target=server.run, args=(Reader(), Writer()))

    def request(id, name, **arguments):
        params = {'name': name, 'arguments': arguments}
        requests.put(json.dumps({'id': id, 'method': 'tools/call', 'params': params}).encode() + b'\n')
        response = replies.get(timeout=6)
        assert response['id'] == id
        return json.loads(response['result']['content'][0]['text'])

    thread.start()
    try:
        first = request(1, 'update_document', document='42', version=4, append='note', defer=True)
        assert first['status'] == 'queued' and started.wait(5)
        op = first['operationId']
        assert request(2, 'get_document', document='42')['version'] == 4
        assert request(3, 'operation_status', operationId=op)['status'] == 'running'
        assert request(4, 'operation_status', operationId=op, connection='personal')['code'] == 'operation_unavailable'
        assert request(5, 'update_document', document='42', append='note', defer=True)['code'] == 'invalid_edit'
        queued = {}
        for version in range(5, 12):
            result = request(version + 2, 'update_document', document='42', version=version, append='note', defer=True)
            assert result['status'] == 'queued'
            queued[version] = result['operationId']
        rejected = request(14, 'update_document', document='42', version=12, append='note', defer=True)
        assert rejected['code'] == 'server_busy' and not rejected['requestStarted']
    finally:
        requests.put(b'')
        release.set()
        thread.join(8)

    assert not thread.is_alive() and applied == list(range(4, 12))
    receipt = server._operation_status(op, 'project')
    assert receipt['status'] == 'finished'
    outcome = receipt['outcome']
    assert (outcome['status'], outcome['id'], outcome['version']) == ('updated', '42', 5)
    assert 'document reviewer' in outcome['reviewInstruction']
    assert server._operation_status(queued[5], 'project')['outcome']['code'] == 'version_conflict'
    unknown = server._operation_status(queued[6], 'project')
    assert unknown['outcome']['status'] == 'unknown' and 'private provider details' not in str(unknown)
    denied = server._operation_status(queued[7], 'project')['outcome']
    assert denied == {'status': 'updated', 'authorizationDenied': True, 'next': 'Restore access before reading.'}


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


def test_codex_review_receipts_isolate_revision_access_and_waiters():
    requests, replies = Queue(), Queue()
    started, release = threading.Event(), threading.Event()
    target = {'id': '42', 'version': 5, 'space': 'SD'}
    observed = []
    denied = False

    class Reader:
        def readline(self, size):
            return requests.get(timeout=10)

    class Writer:
        def write(self, raw):
            replies.put(json.loads(raw))
        def flush(self):
            pass

    class Documents:
        def execute(self, operation, args):
            if operation == 'get_document':
                if args.get('metadata'):
                    assert args == dict(document='42', version=5, space='SD', metadata=True, includeBody=False)
                    if denied:
                        return dict(status='error', code='http_403')
                return dict(status='read', **target)
            sources = {'basedOn': [{'id': '23', 'version': 2}]} if args.get('basedOn') else {}
            return dict(status='updated', body='private provider body', **target, **sources)

    def review(selected):
        observed.append(selected)
        started.set()
        assert release.wait(8)
        return dict(status='FAIL', document='42', version=5, summary='Missing citation',
                    sessionId='12345678-1234-4321-9876-123456789abc', body='provider transcript')

    server = Server(documents=Documents(), codex_review=review)
    thread = threading.Thread(target=server.run, args=(Reader(), Writer()))
    def send(id, name, **args):
        params = dict(name=name, arguments=args)
        requests.put(json.dumps(dict(jsonrpc='2.0', id=id, method='tools/call', params=params)).encode()+b'\n')
    def receive(id):
        response = replies.get(timeout=5)
        assert response['id'] == id
        return json.loads(response['result']['content'][0]['text'])

    thread.start()
    try:
        send(1, 'update_document', document='42', version=4, append='private author text')
        review_id = receive(1)['documentReview']['operationId']
        assert started.wait(3) and observed == [target]
        send(2, 'operation_status', operationId=review_id, connection='personal', waitSeconds=20)
        assert receive(2)['code'] == 'operation_unavailable'
        # This long poll must not stall the regular read, deferred write or its own receipt.
        send(3, 'operation_status', operationId=review_id, waitSeconds=20)
        send(4, 'get_document', document='42')
        assert receive(4)['id'] == '42'
        send(5, 'update_document', document='42', version=4,
             edits=[{'op': 'replace_section', 'section': '2.1', 'content': 'private author text'}],
             basedOn=[{'document': 'https://example.invalid/wiki/spaces/SD/pages/23', 'version': 2}], defer=True)
        write_id = receive(5)['operationId']
        release.set()
        finished = receive(3)
        assert finished['status'] == 'finished' and finished['outcome']['status'] == 'FAIL'
        assert finished['outcome']['summary'] == 'Missing citation' and 'transcript' not in str(finished)
    finally:
        release.set()
        requests.put(b'')
        thread.join(10)
    assert not thread.is_alive()
    deferred = server._operation_status(write_id, 'project')['outcome']
    assert deferred['documentReview']['operationId'] != review_id and 'body' not in deferred
    assert observed == [target, dict(target, sections=['2.1'], sources=[{'id': '23', 'version': 2}])]
    denied = True
    fenced = server._operation_status(review_id, 'project')
    assert fenced['outcome'] == dict(status='BLOCKED_ENV', code='review_access_unconfirmed')
    assert 'citation' not in str(fenced) and 'sessionId' not in str(fenced)


def test_review_queue_retains_active_entries_and_fails_closed(monkeypatch):
    import wiki_atlassian.protocol as protocol
    monkeypatch.setattr(protocol, 'RECEIPT_LIMIT', 1)
    target = dict(id='42', version=5, space='SD')
    class Documents:
        def execute(self, name, args):
            return dict(status='read' if name == 'get_document' else 'updated', **target)
    server = Server(documents=Documents(), codex_review=lambda t: dict(status='PASS', document='43', version=5))
    tasks = []
    server._submit_review = lambda task: tasks.append(task) or True
    first = server._schedule_review(target)
    assert server._schedule_review(target)['status'] == 'BLOCKED_ENV' and len(tasks) == 1
    assert server._operation_status(first['operationId'], 'project')['status'] == 'queued'
    tasks.pop()()
    assert server._operation_status(first['operationId'], 'project')['outcome']['status'] == 'BLOCKED_ENV'
    second = server._schedule_review(target)
    assert second['operationId'] != first['operationId']
    assert server._operation_status(first['operationId'], 'project')['code'] == 'operation_unavailable'
    assert server._schedule_review(dict(target, id='not a page id'))['status'] == 'BLOCKED_ENV'
    assert server.handle(dict(id=1, method='tools/call', params=dict(name='operation_status',
        arguments=dict(operationId=second['operationId'], waitSeconds=21))))['error']['code'] == -32602


@pytest.mark.parametrize('name,status,denied,connection', [
    ('update_document', 'updated', True, 'project'), ('create_document', 'unknown', False, 'project'),
    ('create_comment', 'created', False, 'project'), ('update_document', 'updated', False, 'personal'),
])
def test_no_review_on_unverified_private_or_comment_effects(name, status, denied, connection):
    class Docs:
        def execute(self, *args):
            return dict(status=status, id='42', version=5, space='SD', authorizationDenied=denied)
    docs = Docs()
    server = Server(documents=docs, personal=docs.execute, codex_review=lambda t: pytest.fail('review scheduled'))
    assert 'documentReview' not in server._execute(name, {}, connection)
    reader = Server(documents=docs, reviewer=True, codex_review=lambda t: pytest.fail('reader scheduled'))
    assert reader._codex_review is None and 'operation_status' not in {t['name'] for t in reader.tools}


@pytest.mark.parametrize('operation,status', [('get_document', 'unavailable'), ('create_comment', 'unknown')])
def test_private_process_failure_does_not_misreport_write_completion(monkeypatch, operation, status):
    import subprocess
    import wiki_platform
    from wiki_atlassian.main import personal_operation
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('private-worker', 1)
    monkeypatch.setattr(wiki_platform, 'run_bounded', timeout)
    result = personal_operation(operation, {'document': '42'}, timeout=1)
    assert result['status'] == status
    assert 'private-worker' not in json.dumps(result)


def test_private_process_uses_utf8_stdin_not_native_argv_or_environment(monkeypatch):
    import subprocess
    import wiki_platform
    from wiki_atlassian.main import personal_operation
    content = '가😀' * 20000
    def run(command, **kwargs):
        request = json.loads(kwargs['input_bytes'].decode('utf-8'))
        assert request['arguments']['text'] == content
        assert content not in repr(command) and content not in repr(kwargs['env'])
        assert kwargs['graceful'] is True and '--personal-operation' in command
        return subprocess.CompletedProcess(command, 0, b'{"status":"created"}', b'')
    monkeypatch.setattr(wiki_platform, 'run_bounded', run)
    assert personal_operation('create_comment', dict(target='42', text=content))['status'] == 'created'
