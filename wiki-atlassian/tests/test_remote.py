"""Native transport tests use only a synthetic localhost TLS Confluence."""
from __future__ import annotations

import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wiki_atlassian.config import SpaceSettings
from wiki_atlassian.models import Credentials, Node, Page, RemoteError
from wiki_atlassian.remote import Remote, RootScopeDenial, native_page_url_id


def page_raw(identifier='10', parent=None, version=1, body='<p>Original</p>', **extra):
    result = dict(id=identifier, title='Page ' + identifier, type='page', spaceId='9', status='current', parentId=parent,
                  version=dict(number=version, message='original'), body=dict(storage=dict(value=body, representation='storage')))
    result.update(extra)
    return result


def comment_raw(identifier='50', parent=None, page='11', version=1, body='<p>Comment</p>', **extra):
    result = dict(id=identifier, parentCommentId=parent, pageId=page, status='current',
                  version=dict(number=version, authorId='account', createdAt='2026-11-12T00:00:00Z'),
                  body=dict(storage=dict(value=body, representation='storage')))
    result.update(extra)
    return result


class NativeState:
    def __init__(self):
        self.pages = {'10': page_raw(), '11': page_raw('11', '10')}
        self.comments = {}
        self.calls = []
        self.hook = None
        self.lost = None
        self.draft = None
        self.space_id = '9'
        self.version_author = 'account'
        self.version_time = '2026-11-12T00:00:00Z'
        self.slow_body = False
        self.truncated_body = False
        self.slow_framing = None

    def response(self, method, target, payload=None):
        parsed = urlsplit(target)
        path, query = parsed.path, parse_qs(parsed.query)
        self.calls.append((method, path, query, copy.deepcopy(payload)))
        if self.hook is not None:
            override = self.hook(method, path, query, payload)
            if override is not None:
                return override
        if method == 'GET' and path == '/wiki/rest/api/user/current':
            return 200, {}, {'type': 'known', 'accountId': 'account', 'displayName': 'Synthetic recorder'}
        if method == 'GET' and path == '/wiki/api/v2/spaces':
            return 200, {}, {'results': [{'id': self.space_id, 'key': 'DOC', 'name': 'Documentation', 'status': 'current'}]}
        if method == 'GET' and path == '/wiki/api/v2/spaces/9/pages':
            return 200, {}, {'results': list(self.pages.values())}
        if method == 'GET' and path == '/wiki/rest/api/content/search':
            return 200, {}, {'results': []}
        if path.startswith('/wiki/api/v2/pages/'):
            tail = path.removeprefix('/wiki/api/v2/pages/').split('/')
            identifier = tail[0]
            raw = self.pages.get(identifier)
            if raw is None:
                return 404, {}, {}
            if method == 'GET' and len(tail) == 2 and tail[1] == 'direct-children':
                return 200, {}, {'results': [dict(id=x['id'], type=x['type']) for x in self.pages.values() if x.get('parentId') == identifier]}
            if method == 'GET' and len(tail) == 2 and tail[1] in ('footer-comments', 'inline-comments'):
                kind = tail[1].removesuffix('-comments')
                return 200, {}, {'results': [v for (k, _), v in self.comments.items() if k == kind and v.get('pageId') == identifier and v.get('parentCommentId') is None]}
            if method == 'GET' and len(tail) >= 2 and tail[1] == 'versions':
                if len(tail) == 2:
                    return 200, {}, {'results': [dict(number=raw['version']['number'], authorId=self.version_author, createdAt=self.version_time, message=raw['version'].get('message', ''))]}
                return 200, {}, dict(number=int(tail[2]), authorId=self.version_author, createdAt=self.version_time, message=raw['version'].get('message', ''))
            if method == 'GET' and len(tail) == 1:
                value = copy.deepcopy(self.draft if 'get-draft' in query and self.draft is not None else raw)
                if 'version' in query:
                    value['version']['number'] = int(query['version'][0])
                if 'body-format' not in query:
                    value.pop('body', None)
                return 200, {}, value
            if method == 'PUT' and len(tail) == 1:
                if payload['version']['number'] != raw['version']['number'] + 1:
                    return 409, {}, {'code': 'VERSION_CONFLICT'}
                raw['title'], raw['version'], raw['body'] = payload['title'], payload['version'], {'storage': payload['body']}
                if 'parentId' in payload:
                    raw['parentId'] = payload['parentId']
                if self.lost == 'update':
                    raise ConnectionError('synthetic committed response loss')
                return 200, {}, raw
        for kind in ('footer', 'inline'):
            prefix = '/wiki/api/v2/' + kind + '-comments'
            if path.startswith(prefix + '/'):
                tail = path[len(prefix) + 1:].split('/')
                raw = self.comments.get((kind, tail[0]))
                if raw is None:
                    return 404, {}, {}
                if method == 'GET' and len(tail) == 3 and tail[1] == 'versions':
                    return 200, {}, dict(number=1, authorId='original-author')
                if method == 'GET' and len(tail) == 2 and tail[1] == 'children':
                    return 200, {}, {'results': [v for (k, _), v in self.comments.items() if k == kind and v.get('parentCommentId') == tail[0]]}
                if method == 'GET' and len(tail) == 1:
                    value = copy.deepcopy(raw)
                    if 'body-format' not in query:
                        value.pop('body', None)
                    return 200, {}, value
                if method == 'PUT' and len(tail) == 1:
                    if payload['version']['number'] != raw['version']['number'] + 1:
                        return 409, {}, {}
                    raw['version'] = dict(payload['version'], authorId='account', createdAt=self.version_time)
                    raw['body'] = {'storage': payload['body']}
                    return 200, {}, raw
            if method == 'POST' and path == prefix:
                identifier = '60'
                parent = payload.get('parentCommentId')
                page_id = payload.get('pageId', '11')
                raw = comment_raw(identifier, parent, page_id, body=payload['body']['value'])
                if kind == 'inline' and 'inlineCommentProperties' in payload:
                    selection = payload['inlineCommentProperties']['textSelection']
                    raw.update(resolutionStatus='open', properties=dict(inlineOriginalSelection=selection, inlineMarkerRef='marker'))
                    self.pages[page_id]['body']['storage']['value'] = '<p><ac:inline-comment-marker ac:ref="marker">' + selection + '</ac:inline-comment-marker></p>'
                self.comments[(kind, identifier)] = raw
                return 200, {}, {'id': identifier}
        if method == 'POST' and path in ('/wiki/api/v2/pages', '/wiki/rest/api/content'):
            rootless = path == '/wiki/api/v2/pages'
            parent = None if rootless else payload['ancestors'][0]['id']
            body = payload['body'] if rootless else payload['body']['storage']
            raw = page_raw('12', parent, body=body['value'], title=payload['title'])
            raw['version'] = payload.get('version', {'number': 1, 'message': ''})
            self.pages['12'] = raw
            if self.lost == 'create':
                raise ConnectionError('synthetic committed response loss')
            return 200, {}, {'id': '12'}
        return 404, {}, {}

    def transport(self, method, target, headers, payload, timeout):
        return self._encode(self.response(method, target, json.loads(payload) if payload else None))

    @staticmethod
    def _encode(response):
        status, headers, value = response
        return status, headers, value if isinstance(value, bytes) else json.dumps(value).encode()


@pytest.fixture
def native(tmp_path):
    cert, key = tmp_path / 'certificate.pem', tmp_path / 'key.pem'
    cert.write_text(CERT)
    key.write_text(KEY)
    state = NativeState()
    auth = 'Basic ' + base64.b64encode(b'user@example.invalid:synthetic-token').decode()
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            if self.headers.get('Authorization') != auth:
                self.send_error(401)
                return
            data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            try:
                status, headers, value = state._encode(state.response(self.command, self.path, json.loads(data) if data else None))
            except ConnectionError:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if state.slow_framing is not None:
                prefixes = {
                    'status': b'HTTP/1.1 200 ',
                    'headers': b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nX-Slow: ',
                    'trailers': b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\nX-Slow: ',
                }
                suffix = b'\r\nContent-Length: 2\r\n\r\n{}' if state.slow_framing == 'status' else b'\r\n\r\n' + (b'{}' if state.slow_framing == 'headers' else b'')
                try:
                    self.connection.sendall(prefixes[state.slow_framing])
                    for _ in range(16):
                        self.connection.sendall(b'x')
                        time.sleep(.04)
                    self.connection.sendall(suffix)
                except (OSError, ssl.SSLError):
                    pass
                return
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(value) + (10 if state.truncated_body else 0)))
            for name, item in headers.items():
                self.send_header(name, item)
            self.end_headers()
            try:
                if state.slow_body:
                    for byte in value:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.025)
                else:
                    self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass
        do_GET = do_POST = do_PUT = handle_request
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    credentials = Credentials('https://127.0.0.1:' + str(server.server_port), 'user@example.invalid', 'synthetic-token')
    client = Remote(credentials, ssl_context=ssl.create_default_context(cafile=str(cert)))
    settings = SpaceSettings(dict(space='DOC', spaceId='9', rootPageId='10'), credentials, tmp_path / 'corpus')
    try:
        yield state, client, settings
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def injected(state, settings):
    return Remote(settings.credentials, state.transport)


def test_real_tls_scoped_read_inventory_history_and_native_user(native):
    state, remote, settings = native
    with remote.operation(timeout=30):
        page = remote.scoped_page(settings, '11', True)
        assert page == Page(Node('11', 'Page 11', '10', space_id='9', version=1), '<p>Original</p>', 'original')
        assert remote.current_user()['accountId'] == 'account'
        assert [n.id for n in remote.inventory(settings)] == ['10', '11']
        assert remote.require_page_version(page)['number'] == 1
        assert remote.list_versions('11', scope=settings)['results'][0]['authorId'] == 'account'
        assert remote.historical_page('11', 1, settings).body == page.body
    assert all(call[0] == 'GET' for call in state.calls)


@pytest.mark.parametrize('url', [
    'https://foreign.invalid/wiki/spaces/DOC/pages/11',
    'https://example.invalid/wiki/spaces/DOC/pages/11?x=1',
    'https://example.invalid/wiki/spaces/DOC/pages/11#part',
    'https://example.invalid/wiki/spaces/DOC/pages/11/%252fsecret',
    'https://example.invalid/wiki/spaces/DOC/pages/11/../12',
    'https://user@example.invalid/wiki/spaces/DOC/pages/11',
])
def test_page_url_reference_rejects_foreign_or_nonnative_authority(url):
    with pytest.raises(RemoteError):
        native_page_url_id(url, 'https://example.invalid', 'DOC', True)


def test_page_url_reference_uses_literal_id_and_validates_space():
    assert native_page_url_id('https://example.invalid/wiki/spaces/DOC/pages/11/Unicode-%ED%95%9C', 'https://example.invalid', 'DOC') == '11'
    assert native_page_url_id('https://example.invalid/wiki/api/v2/pages/11', 'https://example.invalid', allow_api=True) == '11'
    with pytest.raises(RemoteError):
        native_page_url_id('https://example.invalid/wiki/spaces/OTHER/pages/11', 'https://example.invalid', 'DOC')


@pytest.mark.parametrize('origin', ['https://example.invalid?', 'https://example.invalid#', 'https://example.invalid:', 'https://example.invalid:0'])
def test_origin_rejects_empty_delimiters_or_missing_native_port(origin):
    with pytest.raises(RemoteError) as failure:
        Remote(Credentials(origin, 'user@example.invalid', 'synthetic-token'))
    assert failure.value.code == 'invalid_origin'


@pytest.mark.parametrize('change', [dict(id=11), dict(spaceId=9), dict(status='archived'), dict(parentId='11'), dict(spaceId='8'), dict(version={'number': '1'})])
def test_invalid_native_identity_refuses_before_storage(native, change):
    state, remote, settings = native
    state.pages['11'].update(change)
    with pytest.raises(RemoteError):
        remote.scoped_page(settings, '11')
    assert not any('body-format' in q for _, _, q, _ in state.calls)


def test_final_ancestor_move_after_body_refuses_export(native):
    state, remote, settings = native
    def mutate(method, path, query, data):
        if path.endswith('/pages/11') and 'body-format' in query:
            state.pages['10']['title'] = 'Changed boundary'
    state.hook = mutate
    with pytest.raises(RemoteError, match='ancestry changed'):
        remote.scoped_page(settings, '11')


def test_outside_root_nested_comment_resolves_metadata_only(native):
    state, remote, settings = native
    state.pages['99'] = page_raw('99')
    state.comments[('footer', '51')] = comment_raw('51', '50', None)
    state.comments[('footer', '50')] = comment_raw('50', None, '99')
    assert remote.comment_location('51') == ('99', '50')
    with pytest.raises(RemoteError):
        remote.comment_detail('99', '51', expected_parent_id='50', scope=settings)
    assert not any('body-format' in q or '/versions/' in p or 'user/current' in p for _, p, q, _ in state.calls)


def test_whole_space_child_with_unreadable_parent_and_no_home(native):
    state, remote, settings = native
    whole = SpaceSettings(dict(space='DOC', spaceId='9'), settings.credentials, settings.corpus_root)
    state.pages = {'11': page_raw('11', '999')}
    assert remote.scoped_page(whole, '11').node.parent_id == '999'
    assert [n.id for n in remote.inventory(whole)] == ['11']
    assert not any(path.endswith('/999') for _, path, _, _ in state.calls)


def test_nested_native_folder_is_admitted_and_inventory_keeps_its_parent(native):
    state, remote, settings = native
    state.pages['11']['parentId'] = '20'
    folder = dict(id='20', title='Folder', type='folder', spaceId='9', status='current', parentId='10')
    def folders(method, path, query, data):
        if path == '/wiki/api/v2/folders/20':
            return 200, {}, folder
        if path == '/wiki/api/v2/pages/10/direct-children':
            return 200, {}, {'results': [{'id': '20', 'type': 'folder'}]}
        if path == '/wiki/api/v2/folders/20/direct-children':
            return 200, {}, {'results': [{'id': '11', 'type': 'page'}]}
    state.hook = folders
    assert remote.scoped_page(settings, '11').node.parent_id == '20'
    assert [(n.id, n.type) for n in remote.inventory(settings)] == [('10', 'page'), ('20', 'folder'), ('11', 'page')]


def test_move_deeper_preserves_all_ancestors_and_whole_space_move_cannot_escape(native):
    state, remote, settings = native
    state.pages['13'] = page_raw('13', '10')
    before = remote.scoped_page(settings, '11')
    saved = remote.update(before, before.body, 'move-message', settings, parent_id='13')
    assert saved.node.parent_id == '13'
    whole = SpaceSettings(dict(space='DOC', spaceId='9'), settings.credentials, settings.corpus_root)
    state.pages['14'] = page_raw('14')
    state.calls.clear()
    with pytest.raises(RemoteError, match='preserve every inherited ancestor'):
        remote.update(saved, saved.body, 'escape-message', whole, parent_id='14')
    assert not any(method == 'PUT' for method, _, _, _ in state.calls)


def test_root_revocation_is_classified_but_child_denial_is_not(native):
    state, remote, settings = native
    state.pages.pop('10')
    with pytest.raises(RootScopeDenial) as failure:
        remote.require_scope(settings, '11')
    assert failure.value.settings is settings
    state.pages['10'] = page_raw()
    state.pages.pop('11')
    with pytest.raises(RemoteError) as failure:
        remote.require_scope(settings, '11')
    assert not isinstance(failure.value, RootScopeDenial)


@pytest.mark.parametrize('identifier,root_denied', [('10', True), ('11', False)])
def test_published_root_body_denial_survives_successful_initial_metadata(native, identifier, root_denied):
    state, remote, settings = native
    state.hook = lambda m, p, q, d: (403, {}, {}) if p.endswith('/pages/' + identifier) and 'body-format' in q else None
    with pytest.raises(RemoteError) as failure:
        remote.scoped_page(settings, identifier)
    assert isinstance(failure.value, RootScopeDenial) is root_denied


@pytest.mark.parametrize('next_url', [
    'https://foreign.invalid/wiki/api/v2/spaces?cursor=x',
    '/wiki/api/v2/pages?cursor=x',
    '/wiki/api/v2/spaces?status=archived&cursor=x',
    '/wiki/api/v2/spaces?limit=1&cursor=x',
    '/wiki/api/v2/spaces?cursor=x&cursor=y',
    '/wiki/api/v2/spaces?unexpected=x',
])
def test_pagination_refuses_origin_endpoint_filter_or_duplicate_drift(native, next_url):
    state, remote, settings = native
    state.hook = lambda m, p, q, d: (200, {}, {'results': [], '_links': {'next': next_url}})
    with pytest.raises(RemoteError):
        remote.spaces('DOC')
    assert len(state.calls) == 1


def test_real_tls_header_and_body_continuations_must_agree(native):
    state, remote, _ = native
    state.hook = lambda m, p, q, d: (200, {'Link': '</wiki/api/v2/spaces?cursor=two>; rel="next"'},
                                       {'results': [], '_links': {'next': '/wiki/api/v2/spaces?cursor=one'}})
    with pytest.raises(RemoteError, match='disagree'):
        remote.spaces()


def test_real_tls_bounded_pagination_and_cycle(native):
    state, remote, _ = native
    def continuation(method, path, query, data):
        if 'cursor' not in query:
            return 200, {}, {'results': [{'id': '9', 'key': 'DOC'}], '_links': {'next': '?cursor=two'}}
        return 200, {}, {'results': [{'id': '8', 'key': 'OTHER'}]}
    state.hook = continuation
    assert [row['id'] for row in remote.spaces()] == ['9', '8']
    state.hook = lambda m, p, q, d: (200, {}, {'results': [], '_links': {'next': '?cursor=two'}})
    with pytest.raises(RemoteError, match='cyclic'):
        remote.spaces()


def test_real_tls_redirect_is_not_followed_and_error_payload_is_not_echoed(native):
    state, remote, _ = native
    state.hook = lambda m, p, q, d: (302, {'Location': 'https://foreign.invalid/secret'}, {'message': 'synthetic-token private contents'})
    with pytest.raises(RemoteError) as failure:
        remote.current_user()
    assert failure.value.code == 'http_302'
    assert 'private' not in str(failure.value) and 'synthetic-token' not in str(failure.value)
    assert len(state.calls) == 1


def test_recognized_provider_code_cannot_reflect_a_secret(native):
    state, _, settings = native
    state.hook = lambda m, p, q, d: (403, {}, {'code': 'FORBIDDEN'})
    remote = Remote(Credentials(settings.site, 'user@example.invalid', 'FORBIDDEN'), state.transport)
    with pytest.raises(RemoteError) as failure:
        remote.current_user()
    assert failure.value.details == {}


@pytest.mark.parametrize('raw', [b'{"accountId":"one","accountId":"two"}', b'{"a":NaN}', b'{"a":"\\ud800"}', b'\xff', b'[]', b'{}' + b' ' * (8 * 1024 * 1024)],
                         ids=['duplicate-key', 'nonfinite-number', 'isolated-surrogate', 'invalid-utf8', 'nonobject', 'overflow'])
def test_bounded_json_response_rejects_duplicates_nonnative_or_overflow(native, raw):
    state, _, settings = native
    state.hook = lambda m, p, q, d: (200, {}, raw)
    remote = injected(state, settings)
    with pytest.raises(RemoteError):
        remote.current_user()


def test_operation_request_and_byte_budgets_are_not_unbounded(native):
    state, _, settings = native
    remote = injected(state, settings)
    with pytest.raises(RemoteError, match='request/time budget'):
        with remote.operation(max_requests=1):
            remote.current_user()
            remote.current_user()
    assert len(state.calls) == 1
    with pytest.raises(RemoteError, match='body budget'):
        with remote.operation(max_response_bytes=1):
            remote.current_user()


def test_real_tls_slow_drip_body_cannot_extend_operation_deadline(native):
    state, remote, _ = native
    state.slow_body = True
    started = time.monotonic()
    with pytest.raises(RemoteError) as failure:
        with remote.operation(timeout=.15):
            remote.current_user()
    assert time.monotonic() - started < .8
    assert failure.value.code == 'transport_failed'


@pytest.mark.parametrize('framing', ['status', 'headers', 'trailers'])
def test_absolute_deadline_covers_http_framing_reads(native, framing):
    state, remote, settings = native
    state.slow_framing = framing
    started = time.monotonic()
    with pytest.raises(RemoteError) as failure:
        with remote.operation(timeout=.15):
            remote._request('GET', settings.site + '/wiki/rest/api/user/current')
    assert failure.value.code == 'transport_failed'
    assert time.monotonic() - started < .45


def test_native_dns_wait_is_bounded_and_pending_resolution_is_shared(native, monkeypatch):
    state, remote, _ = native
    original = socket.getaddrinfo
    calls = []
    def slow_resolver(*args, **kwargs):
        calls.append(args)
        time.sleep(.4)
        return original(*args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', slow_resolver)
    started = time.monotonic()
    for _ in range(2):
        with pytest.raises(RemoteError):
            with remote.operation(timeout=.06):
                remote.current_user()
    assert time.monotonic() - started < .3
    assert len(calls) == 1 and not state.calls


def test_transient_resolver_thread_start_failure_does_not_poison_later_reads(native, monkeypatch):
    state, remote, _ = native
    original = threading.Thread.start
    failed = False
    def start(thread):
        nonlocal failed
        if thread.name == 'wiki-native-resolver' and not failed:
            failed = True
            raise RuntimeError('Synthetic thread resource exhaustion')
        return original(thread)
    monkeypatch.setattr(threading.Thread, 'start', start)
    with pytest.raises(RemoteError):
        remote.current_user()
    with remote.operation(timeout=1):
        assert remote.current_user()['accountId'] == 'account'
    assert len(state.calls) == 1


def test_watchdog_start_failure_closes_the_actual_owned_tls_socket(native, monkeypatch):
    state, remote, _ = native
    sockets = []
    original = remote._ssl_context.wrap_socket
    def wrap(*args, **kwargs):
        result = original(*args, **kwargs)
        sockets.append(result)
        return result
    def fail_start(timer):
        raise RuntimeError('Synthetic thread resource exhaustion')
    monkeypatch.setattr(remote._ssl_context, 'wrap_socket', wrap)
    monkeypatch.setattr(threading.Timer, 'start', fail_start)
    with pytest.raises(RemoteError):
        remote.current_user()
    assert len(sockets) == 1 and sockets[0].fileno() == -1 and not state.calls


def test_native_conflict_stays_definite_when_its_optional_error_body_times_out(native):
    state, remote, settings = native
    state.slow_body = True
    state.hook = lambda m, p, q, d: (409, {}, {'message': 'Synthetic diagnostic body'})
    with pytest.raises(RemoteError) as failure:
        with remote.operation(timeout=.15):
            remote._request('PUT', settings.site + '/wiki/api/v2/pages/11', {})
    assert failure.value.code == 'http_409' and not failure.value.outcome_unknown
    assert len(state.calls) == 1


def test_native_conflict_stays_definite_when_error_body_exhausts_byte_budget(native):
    state, _, settings = native
    remote = injected(state, settings)
    state.hook = lambda m, p, q, d: (409, {}, {'code': 'VERSION_CONFLICT'})
    with pytest.raises(RemoteError) as failure:
        with remote.operation(max_response_bytes=1):
            remote._request('PUT', settings.site + '/wiki/api/v2/pages/11', {})
    assert failure.value.code == 'http_409' and not failure.value.outcome_unknown
    assert len(state.calls) == 1


@pytest.mark.parametrize('entry', ['body', 'header'])
def test_malformed_native_query_remains_a_typed_continuation_error(native, entry):
    state, _, settings = native
    remote = injected(state, settings)
    response = (200, {}, {'results': [], '_links': {'next': '?cursor=x&broken'}}) if entry == 'body' else (200, {'Link': '<?cursor=x&broken>; rel="next"'}, {'results': []})
    state.hook = lambda m, p, q, d: response
    with pytest.raises(RemoteError) as failure:
        remote.spaces()
    assert failure.value.code == 'invalid_next' and len(state.calls) == 1


@pytest.mark.parametrize('continuation', ['?cursor=x#', '/wiki/api/v2/unused/../spaces?cursor=x', '/wiki/api/v2/./spaces?cursor=x'])
def test_native_continuations_are_not_repaired_before_validation(native, continuation):
    state, _, settings = native
    remote = injected(state, settings)
    state.hook = lambda m, p, q, d: (200, {}, {'results': [], '_links': {'next': continuation}})
    with pytest.raises(RemoteError) as failure:
        remote.spaces()
    assert failure.value.code == 'invalid_next' and len(state.calls) == 1


@pytest.mark.parametrize('identifier', ['10', '11'])
@pytest.mark.parametrize('operation', ['history', 'versions', 'audit', 'draft'])
def test_scoped_history_audit_and_draft_denials_retain_only_root_provenance(native, identifier, operation):
    state, _, settings = native
    remote = injected(state, settings)
    def deny(method, path, query, data):
        matches = (operation == 'history' and 'version' in query or operation == 'versions' and path.endswith('/versions') or
                   operation == 'audit' and path.endswith('/versions/1') or operation == 'draft' and 'get-draft' in query)
        if '/pages/' + identifier in path and matches:
            return 403, {}, {}
    state.hook = deny
    operations = {'history': lambda: remote.historical_page(identifier, 1, settings),
                  'versions': lambda: remote.list_versions(identifier, scope=settings),
                  'audit': lambda: remote.version(identifier, 1, settings),
                  'draft': lambda: remote.ensure_no_draft(identifier, scope=settings)}
    with pytest.raises(RemoteError) as failure:
        operations[operation]()
    assert isinstance(failure.value, RootScopeDenial) is (identifier == '10')


def test_root_audit_denial_survives_unknown_update_wrapper(native):
    state, _, settings = native
    remote = injected(state, settings)
    before = remote.scoped_page(settings, '10')
    state.hook = lambda m, p, q, d: (403, {}, {}) if p.endswith('/pages/10/versions/2') else None
    with pytest.raises(RemoteError) as failure:
        remote.update(before, '<p>Saved</p>', 'unique-update-message', settings)
    assert failure.value.code == 'unknown_update' and isinstance(failure.value.__cause__, RootScopeDenial)
    assert sum(method == 'PUT' for method, _, _, _ in state.calls) == 1


@pytest.mark.parametrize('operation', ['page-create', 'page-update', 'comment-create', 'comment-update'])
@pytest.mark.parametrize('destination', [None, '13'])
def test_all_write_acknowledgements_revalidate_consumed_ancestry_after_account_reads(native, operation, destination):
    state, _, settings = native
    remote = injected(state, settings)
    state.pages['13'] = page_raw('13', '10')
    state.comments[('footer', '50')] = comment_raw(version=2)
    before = remote.scoped_page(settings, '11')
    comment = remote.comment_detail('11', '50', scope=settings)
    wrote = False
    def relocate(method, path, query, payload):
        nonlocal wrote
        if method in ('POST', 'PUT'):
            wrote = True
        elif wrote and path == '/wiki/rest/api/user/current':
            state.pages['12' if operation == 'page-create' else '11']['parentId'] = destination
    state.hook = relocate
    operations = {'page-create': lambda: remote.create('9', '10', 'New', '<p>Saved</p>', 'unique-create-message', settings),
                  'page-update': lambda: remote.update(before, '<p>Saved</p>', 'unique-update-message', settings),
                  'comment-create': lambda: remote.create_comment('11', '<p>Saved</p>', scope=settings),
                  'comment-update': lambda: remote.update_comment('11', comment, '<p>Saved</p>', settings)}
    with pytest.raises(RemoteError) as failure:
        operations[operation]()
    assert failure.value.outcome_unknown and failure.value.__cause__.code == 'scope_metadata_changed'
    assert sum(method in ('POST', 'PUT') for method, _, _, _ in state.calls) == 1


def test_truncated_http_body_with_valid_json_is_not_mistaken_for_complete_response(native):
    state, remote, settings = native
    state.truncated_body = True
    state.hook = lambda m, p, q, d: (200, {}, {'id': '12'})
    with pytest.raises(RemoteError) as failure:
        remote._request('POST', settings.site + '/wiki/api/v2/pages', {})
    assert failure.value.outcome_unknown
    assert sum(method == 'POST' for method, _, _, _ in state.calls) == 1


def test_safe_provider_reasons_preserve_useful_error_without_private_echo(native):
    state, remote, settings = native
    state.hook = lambda m, p, q, d: (400, {}, {'code': 'INVALID_STORAGE', 'message': 'Malformed XHTML private content synthetic-token'})
    with pytest.raises(RemoteError) as failure:
        remote.current_user()
    assert failure.value.details == {'providerCodes': ['INVALID_STORAGE'], 'providerMessages': ['Native storage XHTML is invalid or malformed.']}
    assert 'private' not in json.dumps(failure.value.details) and 'synthetic-token' not in json.dumps(failure.value.details)


def test_ambiguous_space_identity_is_never_selected(native):
    state, remote, settings = native
    state.hook = lambda m, p, q, d: (200, {}, {'results': [{'id': '9', 'key': 'DOC'}, {'id': '8', 'key': 'DOC'}]})
    with pytest.raises(RootScopeDenial):
        remote.require_space(SpaceSettings(dict(space='DOC', spaceId='9'), settings.credentials, settings.corpus_root))


def test_comments_native_original_author_editor_replies_and_inline_context(native):
    state, remote, settings = native
    state.comments[('footer', '50')] = comment_raw(version=3)
    state.comments[('footer', '51')] = comment_raw('51', '50', None)
    state.comments[('inline', '52')] = comment_raw('52', resolutionStatus='open', properties={'inlineOriginalSelection': 'Original', 'inlineMarkerRef': 'marker'})
    result = remote.list_comments('11', scope=settings)
    assert result['results'][0]['authorId'] == 'original-author'
    assert result['results'][0]['editorId'] == 'account'
    assert remote.comment_detail('11', '51', expected_parent_id='50', scope=settings)['parentId'] == '50'
    inventory = remote.comment_inventory('11', scope=settings)
    assert {row['id'] for row in inventory['comments']} == {'50', '51', '52'}
    assert remote.comment_detail('11', '52', 'inline', scope=settings)['inlineContext']['resolutionStatus'] == 'open'


def test_comment_inventory_shares_one_scope_graph_instead_of_spending_budget_per_thread(native):
    state, _, settings = native
    remote = injected(state, settings)
    state.comments = {('footer', str(50 + n)): comment_raw(str(50 + n), version=2) for n in range(20)}
    with remote.operation(max_requests=40):
        inventory = remote.comment_inventory('11', scope=settings)
    assert len(inventory['comments']) == 20 and len(state.calls) < 40
    assert not any('/versions/' in path for _, path, _, _ in state.calls)


def test_missing_original_history_keeps_unknown_author_and_native_edit_permission(native):
    state, remote, settings = native
    state.comments[('footer', '50')] = comment_raw(version=2)
    state.hook = lambda m, p, q, d: (403, {}, {}) if p.endswith('/footer-comments/50/versions/1') else None
    before = remote.comment_detail('11', '50', scope=settings)
    assert before['authorId'] is None and before['editorId'] == 'account'
    saved = remote.update_comment('11', before, '<p>Edited</p>', settings)
    assert saved['authorId'] is None and saved['version'] == 3
    assert sum(method == 'PUT' for method, _, _, _ in state.calls) == 1


def test_draft_absence_is_unverified_and_identical_automatic_draft_is_accepted(native):
    state, remote, settings = native
    state.hook = lambda m, p, q, d: (404, {}, {}) if 'get-draft' in q else None
    with pytest.raises(RemoteError) as failure:
        remote.ensure_no_draft('11', scope=settings)
    assert failure.value.code == 'draft_unverified'
    state.hook = None
    state.draft = copy.deepcopy(state.pages['11'])
    state.draft['status'] = 'draft'
    state.draft['version']['message'] = ''
    remote.ensure_no_draft('11', scope=settings)
    state.draft['body']['storage']['value'] = '<p>Divergent unpublished work</p>'
    with pytest.raises(RemoteError):
        remote.ensure_no_draft('11', scope=settings)


def test_parented_create_uses_v1_message_and_one_post(native):
    state, remote, settings = native
    callbacks = []
    saved = remote.create('9', '10', 'New', '<p>Native</p>', 'unique-create-message', settings, lambda: callbacks.append('effect'))
    assert saved.node.id == '12' and saved.version_message == 'unique-create-message'
    posts = [call for call in state.calls if call[0] == 'POST']
    assert len(posts) == 1 and posts[0][1] == '/wiki/rest/api/content'
    assert posts[0][3]['version']['message'] == 'unique-create-message'
    assert callbacks == ['effect']


def test_rootless_create_pins_v2_native_space_and_audit_without_fake_message(native):
    state, remote, settings = native
    whole = SpaceSettings(dict(space='DOC', spaceId='9'), settings.credentials, settings.corpus_root)
    saved = remote.create('9', None, 'Root page', '<p>Native</p>', '', whole)
    assert saved.node.parent_id is None and saved.version_message == ''
    posts = [call for call in state.calls if call[0] == 'POST']
    assert len(posts) == 1 and posts[0][1] == '/wiki/api/v2/pages' and posts[0][2] == {'root-level': ['true']}
    assert posts[0][3]['spaceId'] == '9' and 'version' not in posts[0][3]


@pytest.mark.parametrize('field,value', [('version_author', 'another-account'), ('version_time', 'not-a-time'), ('version_time', '2026-11-12')])
def test_rootless_native_audit_mismatch_is_unknown_without_repeated_create(native, field, value):
    state, remote, settings = native
    setattr(state, field, value)
    whole = SpaceSettings(dict(space='DOC', spaceId='9'), settings.credentials, settings.corpus_root)
    with pytest.raises(RemoteError) as failure:
        remote.create('9', None, 'Root page', '<p>Native</p>', '', whole)
    assert failure.value.outcome_unknown and failure.value.code == 'unknown_create'
    assert sum(method == 'POST' for method, _, _, _ in state.calls) == 1


def test_lost_create_stays_unknown_and_never_replays(native):
    state, remote, settings = native
    state.lost = 'create'
    with pytest.raises(RemoteError) as failure:
        remote.create('9', '10', 'New', '<p>Saved</p>', 'unique-create-message', settings)
    assert failure.value.outcome_unknown and '12' in state.pages
    assert sum(method == 'POST' for method, _, _, _ in state.calls) == 1


def test_lost_committed_update_reconciles_only_exact_native_next_version(native):
    state, remote, settings = native
    before = remote.scoped_page(settings, '11')
    state.lost = 'update'
    saved = remote.update(before, '<p>Saved</p>', 'unique-update-message', settings)
    assert saved.node.version == 2 and saved.version_message == 'unique-update-message'
    assert sum(method == 'PUT' for method, _, _, _ in state.calls) == 1


@pytest.mark.parametrize('mode', ['absent', 'mismatch', 'unreadable', 'root-denied'])
def test_lost_update_nonexact_readback_is_unknown_and_never_replays(native, mode):
    state, _, settings = native
    remote = injected(state, settings)
    before = remote.scoped_page(settings, '11')
    wrote = False
    def fault(method, path, query, data):
        nonlocal wrote
        if method == 'PUT':
            wrote = True
            if mode != 'absent':
                state.pages['11']['version'] = dict(number=2, message='different-message')
                state.pages['11']['body']['storage']['value'] = '<p>Saved</p>'
            raise ConnectionError('synthetic loss')
        if wrote and mode == 'unreadable':
            return 403, {}, {}
        if wrote and mode == 'root-denied' and path.endswith('/10'):
            return 403, {}, {}
    state.hook = fault
    with pytest.raises(RemoteError) as failure:
        remote.update(before, '<p>Saved</p>', 'unique-update-message', settings)
    assert failure.value.code == 'unknown_update' and failure.value.outcome_unknown
    assert sum(method == 'PUT' for method, _, _, _ in state.calls) == 1
    if mode == 'root-denied':
        assert isinstance(failure.value.__cause__, RootScopeDenial)


def test_conflict_is_definite_and_callback_failure_has_no_effect(native):
    state, remote, settings = native
    before = remote.scoped_page(settings, '11')
    state.hook = lambda m, p, q, d: (409, {}, {'code': 'VERSION_CONFLICT'}) if m == 'PUT' else None
    with pytest.raises(RemoteError) as failure:
        remote.update(before, '<p>Saved</p>', 'unique-update-message', settings)
    assert failure.value.code == 'http_409' and not failure.value.outcome_unknown
    state.calls.clear()
    def reject():
        raise RemoteError('source_changed', 'Synthetic source changed')
    with pytest.raises(RemoteError) as failure:
        remote.update(before, '<p>Saved</p>', 'unique-update-message', settings, before_effect=reject)
    assert failure.value.code == 'source_changed' and not any(m == 'PUT' for m, _, _, _ in state.calls)


def test_footer_create_and_inline_anchor_use_native_contract_and_one_effect(native):
    state, remote, settings = native
    saved = remote.create_comment('11', '<p>New comment</p>', account_id='account', scope=settings)
    assert saved['id'] == '60' and saved['version'] == 1
    state.comments.clear()
    state.calls.clear()
    saved = remote.create_comment('11', '<p>Inline</p>', account_id='account', scope=settings, kind='inline', selection=('Original', 0, 1))
    assert saved['inlineContext']['inlineMarkerRef'] == 'marker'
    posts = [call for call in state.calls if call[0] == 'POST']
    assert len(posts) == 1 and posts[0][3]['inlineCommentProperties'] == dict(textSelection='Original', textSelectionMatchIndex=0, textSelectionMatchCount=1)


def test_inline_readback_wrong_marker_is_unknown_and_never_replayed(native):
    state, remote, settings = native
    original = state.response
    def response(method, target, payload=None):
        result = original(method, target, payload)
        if method == 'POST':
            state.pages['11']['body']['storage']['value'] = '<p>Original</p>'
        return result
    state.response = response
    with pytest.raises(RemoteError) as failure:
        remote.create_comment('11', '<p>Inline</p>', scope=settings, kind='inline', selection=('Original', 0, 1))
    assert failure.value.outcome_unknown and failure.value.details['reference'] == 'inline:60'
    assert sum(method == 'POST' for method, _, _, _ in state.calls) == 1


# Public synthetic localhost fixture material; never used outside these tests.
CERT = '-----BEGIN CERTIFICATE-----\nMIIDJTCCAg2gAwIBAgIUeKKFNjV4PJOEwIfBw1V2zjDlfSkwDQYJKoZIhvcNAQEL\nBQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MTExMjAxMjYwMloXDTM2MTEw\nOTAxMjYwMlowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF\nAAOCAQ8AMIIBCgKCAQEAzAsSj/NOwmVNyOhbM0O+U5z6Z2uj4TaRNoH9RLQtThRt\nMqHbzWgWB4EL2ZZu4u8nkuwpFvWxJ+uKCyC+lFmIaXRTSn2SjA0GESz2S6AywUGx\nIG4Uu28tcy3chYKsgDXDNU5Lfx2ec5MLWt71GzAg/1rjqQ8WKOnSVt6VCGRQF2zF\n960BI4N0d/wpOANkptiZ+H+pZR6XxCYSj10Bde1YlF6Ql6O6Cv0mxWy0qZrsUT5g\nOiA/vKwR0NOoBP8YcA/XAIUf9SsL3mT1u1BeSG6MsuYepQN8SD2ogPlfoNlkC8w0\nU0pE58d6ehogaqx8pzkVjJ9SFqNK7VFFCmz8bovaBwIDAQABo28wbTAdBgNVHQ4E\nFgQU6DGM5/6XY7fvitpDPJtuPjrzSH8wHwYDVR0jBBgwFoAU6DGM5/6XY7fvitpD\nPJtuPjrzSH8wDwYDVR0TAQH/BAUwAwEB/zAaBgNVHREEEzARgglsb2NhbGhvc3SH\nBH8AAAEwDQYJKoZIhvcNAQELBQADggEBALuf8oT/YW/n27ULDYklkm6vu/GTcBxk\nLmjnDao1K2FyUThpPZsfgiybmkK4g3zcdWGZ6u3IVnHJLjBKxFSxp/3JSDVpTJal\nyNYtYT/jXU6jBvS34Et3ykOrR0ki3v9Lx5Pi3pAvSKE3ZK2ET8CZcI65qIltblnW\ntsjj2FDwHfL/p6QEmCxfdp+imuRE2ByhQrq47sxVojHHBi0TE5kw09GJVvIZn/DK\nQ/V+fugBS7gMtaL97Gbb0XgIPj8sgZiCkZJeRTJ00rGXKZymixVr75waOOOyPJv2\nm9SA3qy6WbWe+uukyS7gw18LNCsZOjopgvtKGkblPop+QvjqrjBtyoc=\n-----END CERTIFICATE-----\n'
KEY = '-----BEGIN PRIVATE KEY-----\nMIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDMCxKP807CZU3I\n6FszQ75TnPpna6PhNpE2gf1EtC1OFG0yodvNaBYHgQvZlm7i7yeS7CkW9bEn64oL\nIL6UWYhpdFNKfZKMDQYRLPZLoDLBQbEgbhS7by1zLdyFgqyANcM1Tkt/HZ5zkwta\n3vUbMCD/WuOpDxYo6dJW3pUIZFAXbMX3rQEjg3R3/Ck4A2Sm2Jn4f6llHpfEJhKP\nXQF17ViUXpCXo7oK/SbFbLSpmuxRPmA6ID+8rBHQ06gE/xhwD9cAhR/1KwveZPW7\nUF5Iboyy5h6lA3xIPaiA+V+g2WQLzDRTSkTnx3p6GiBqrHynORWMn1IWo0rtUUUK\nbPxui9oHAgMBAAECggEACi99UDlFk7ep398VemQl0pex+kqpxhrKue1YPr89ybmE\nT/Q7fC/3dY1wEX8MbwCgOZnXXUGLg+IipHaQfOhRQm5foAbPs3UM+fuEGfdXwUTt\nuiA0obyliKvf57xDryy/31/WrfRFDiDnfrRFiQNoika3uZ86rHUrnlGDIAWd5+Vb\nuLSIKD+BHbM4GzFGALffqbxeV1JSkneXeIkBA3bKLR2vYzIpcYYG+2cAQDsCGXcF\nuMoYy0Eg1cMM2PcajEgtV3vFKSTUj6huveFCUrxdJHn2yvZ6E38bzWDUBxzPVSQP\n1inCWFe7FOYgSb7nFAoUmmreWHsavbVWJPmwmmFsqQKBgQD8On11AzY10szBdNHY\ntb/oeOaU56wnPtLq8HqkU4HqCthOWJO9gCXiliffRozfvUV3KeUBOn60Ski1Smg/\nZKOQv+2N5Oz9EXR/6vY/ou5NuSJNgTEoAGBXbXW8RLg2iTlcFcQsyLi1lrr1WOz2\neXnwXlPOwiuumrd0IIXB0NGCbwKBgQDPGCIjU08iJUu/XzyQmTDD1wCj1P9rBUDZ\nR+ja2R0DQv3Tr6mfXo2DiOhKALYllNzXtM8EBu/kSImR+DdXO/XewIZOB8q880h2\nQklHnJDOREqLindv3dG2S7BJujNgJIYDYOLSWiG2PrbVoO7/WxS9xYSxvrKYTq6k\ne0S7w8uN6QKBgQCs7lk30cToMChMdxCtGSeDduj4rMJegfdQNyKO6eZHlbnqNVbN\n9T+EU9jwf0l02Sr4YZPuPLHEYK3rBiHlXNW8F6sCvzMw6zfJwEmnJclnJoh7RRlz\nyJZFOvoe46q9UWYvWYkP/2qMrdRvBNIqxuywvauONZiHdPJUvzuVv9r1JwKBgQCz\nwrgHP85Fzmfk0s8omMCajesxSrb295VlxaSbKrCjXygi8qzi+FkQHuu7KMjy8DeO\nSZUUYs0is9AuU23+G779PtKVsu/JecehV0JylbWgRekLHQM1qC7b9PEt3iGwR6PI\nFPZuLuxqXB+qacpq/22OmH/pJaWyzUGM5q8v6PGs2QKBgQDItcOZfrY7VV0w89as\nTUAGofZzlQJzTs9XNRlOkQURRLt36Zj8/C0jQJyDzNnnDwDtdfbUnxlsRfRXRxUG\nxlgtzelNae7lcXR1e1MTJiYg+R6O8N8YPi+gTyNKbuOW9j0ZQOujDudjuZwiL+PV\nGCM0hhJfC+T0kl12OH4xKWIUHw==\n-----END PRIVATE KEY-----\n'
